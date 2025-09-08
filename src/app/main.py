from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os, json
import httpx
from uuid import uuid4

from supabase import create_client

# load rules at startup
from .utils.rules import load_action_rules
load_action_rules()  # reads rules/actions.yaml at boot

# optional pdf parsing
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# Import agents
from app.agents.triage import run_triage
from app.agents.action import run_action_agent, execute_actions
from app.agents.escalation import run_escalation_agent, send_to_power_automate

# --- Config ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
MIN_AUTOPILOT = float(os.getenv("MIN_AUTOPILOT", "0.75"))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

# --- Schemas ---
class EmailParty(BaseModel):
    name: Optional[str]
    email: str

class Attachment(BaseModel):
    filename: str
    content_type: str
    download_url: str

class EmailPayload(BaseModel):
    message_id: str
    internet_message_id: str
    subject: str
    from_: EmailParty
    to: List[EmailParty]
    cc: List[EmailParty] = []
    bcc: List[EmailParty] = []
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    attachments: List[Attachment] = []
    headers: Dict[str, Any] = {}

class FeedbackPayload(BaseModel):
    nhr_token: str
    human: Dict[str, Any]
    final_classification: str
    actions: List[Dict[str, Any]]

def _normalize_n8n_payload(raw: Dict[str, Any]) -> "EmailPayload":
    """Accepts either our rich EmailPayload or simplified n8n body and returns EmailPayload."""
    # If payload already looks like our EmailPayload (has from_/to keys), build directly
    if "from_" in raw and "to" in raw:
        return EmailPayload(**raw)

    # Simplified n8n body:
    # {
    #   subject, body, from_address, message_id, in_reply_to, attachment_links: []
    # }
    subject = raw.get("subject") or ""
    body_text = raw.get("body") or raw.get("body_text") or ""
    msg_id = raw.get("message_id") or subject or "missing-id"
    internet_id = raw.get("internet_message_id") or raw.get("message_id") or msg_id
    from_addr = raw.get("from_address") or ""

    # Build attachments from attachment_links (assumed public URLs)
    links = raw.get("attachment_links") or []
    attachments: List[Attachment] = []
    for link in links:
        fname = (link.split("/")[-1] or "attachment").split("?")[0]
        ctype = "application/pdf" if link.lower().endswith(".pdf") else "application/octet-stream"
        attachments.append(Attachment(filename=fname, content_type=ctype, download_url=link))

    return EmailPayload(
        message_id=str(msg_id),
        internet_message_id=str(internet_id),
        subject=str(subject),
        from_=EmailParty(name=None, email=str(from_addr)),
        to=[],
        cc=[],
        bcc=[],
        body_text=str(body_text),
        body_html=None,
        attachments=attachments,
        headers={"in_reply_to": raw.get("in_reply_to")}
    )

def _extract_pdf_text(url: str, timeout: float = 15.0) -> str:
    """Downloads a PDF and extracts text. Returns '' on any failure or if parser missing."""
    if not PdfReader:
        return ""
    try:
        r = httpx.get(url, timeout=timeout)
        r.raise_for_status()
        import io
        reader = PdfReader(io.BytesIO(r.content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        # cap to keep prompts lean
        return "\n".join(filter(None, parts))[:50000]
    except Exception:
        return ""

# --- Routes ---
@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest_email(email_raw: Dict[str, Any]):
    # normalize (supports both rich EmailPayload and your simplified n8n JSON)
    email = _normalize_n8n_payload(email_raw)

    # --- intake log (email_logs) ---
    try:
        supabase.table("email_logs").insert({
            "email_id": email.internet_message_id,
            "message_id": email.message_id,
            "subject": email.subject,
            "from_email": email.from_.email,
            "to_emails": [p.email for p in (email.to or [])],
            "cc_emails": [p.email for p in (email.cc or [])],
            "body_text": email.body_text or "",
            "attachment_links": [a.download_url for a in (email.attachments or [])],
            "headers": email.headers,
            "thread_hint": email.headers.get("in_reply_to"),
            "status": "received"
        }).execute()
    except Exception:
        # never fail the request because of logging
        pass

    # Augment for agents: original body + extracted PDF text (NOT stored in DB)
    augmented_body = email.body_text or ""
    for att in email.attachments:
        if att.download_url and att.download_url.lower().endswith(".pdf"):
            txt = _extract_pdf_text(att.download_url)
            if txt:
                augmented_body += f"\n\n[Attachment Extract: {att.filename}]\n" + txt[:20000]

    email_for_agents = email.model_copy(update={"body_text": augmented_body})

    # --- triage & log ---
    triage_result = run_triage(email_for_agents)
    try:
        supabase.table("email_decisions").insert({
            "classification": triage_result["classification"],
            "confidence": triage_result["confidence"],
            "rationale": "\n".join(triage_result.get("rationale", [])),
            "email_id": email.internet_message_id,
            "stage": "triage"
        }).execute()
    except Exception:
        pass

    # --- action agent & log ---
    action_result = run_action_agent(email_for_agents, triage_result)
    try:
        supabase.table("email_decisions").insert({
            "classification": action_result["final_classification"],
            "confidence": action_result["final_confidence"],
            "rationale": "\n".join(action_result.get("final_rationale", [])),
            "email_id": email.internet_message_id,
            "stage": "action",
            "nhr": action_result["needs_human_review"]
        }).execute()
    except Exception:
        pass

    executed: List[Dict[str, Any]] = []
    escalation_payload: Optional[Dict[str, Any]] = None

    if (
        action_result.get("agree")
        and not action_result.get("needs_human_review")
        and float(action_result.get("final_confidence", 0.0)) >= MIN_AUTOPILOT
    ):
        # execute actions and audit in action_runs
        executed = execute_actions(email, action_result, supabase=supabase)
    else:
        # escalate
        from app.agents.escalation import run_escalation_agent, send_to_power_automate  # lazy import to avoid cycles
        from uuid import uuid4

        nhr_token = f"NHR_{uuid4().hex}"
        try:
            supabase.table("email_decisions").insert({
                "classification": action_result["final_classification"],
                "confidence": action_result["final_confidence"],
                "rationale": "\n".join(action_result.get("final_rationale", [])),
                "email_id": email.internet_message_id,
                "stage": "nhr",
                "nhr": True,
                "nhr_token": nhr_token
            }).execute()
        except Exception:
            pass

        escalation_result = run_escalation_agent(email_for_agents, triage_result, action_result)
        escalation_payload = {
            "email": email.model_dump(),   # links only; no embedded large text
            "triage": triage_result,
            "action": action_result,
            "escalation": escalation_result,
            "nhr_token": nhr_token
        }
        try:
            send_to_power_automate(escalation_payload)
        except Exception:
            # don't crash ingestion if PA is down
            pass

        # --- finalize intake status (email_logs) ---
        try:
            final_status = "executed" if executed else ("escalated" if escalation_payload else "no_action")
            supabase.table("email_logs").update({
                "status": final_status
            }).eq("email_id", email.internet_message_id).execute()
        except Exception:
            pass

        return {
            "status": "processed",
            "triage": triage_result,
            "action": action_result,
            "executed": executed,
            "escalated": escalation_payload is not None
        }

@app.post("/feedback")
def feedback(p: FeedbackPayload):
    # TODO: apply human feedback actions
    return {"status": "feedback received", "final_classification": p.final_classification}