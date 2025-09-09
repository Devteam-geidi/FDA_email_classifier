from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import httpx
from uuid import uuid4

from supabase import create_client

# load rules at startup
from app.utils.rules import load_action_rules
load_action_rules()  # reads rules/actions.yaml at boot

# optional pdf parsing
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# Import agents
from app.agents.triage import run_triage
from app.agents.action import run_action_agent, execute_actions, decide_actions
from app.agents.policy_refiner import update_policy_from_logs

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
    human: str | None = None
    final_classification: str

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
    ...
    executed: List[Dict[str, Any]] = []
    escalation_payload: Optional[Dict[str, Any]] = None

    if (
        action_result.get("agree")
        and not action_result.get("needs_human_review")
        and float(action_result.get("final_confidence", 0.0)) >= MIN_AUTOPILOT
    ):
        # autopilot path
        executed = execute_actions(email, action_result, supabase=supabase)
        escalation_payload = None
    else:
        # escalate path
        from app.agents.escalation import run_escalation_agent, send_to_power_automate  # lazy import to avoid cycles

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
            "email": email.model_dump(),
            "triage": triage_result,
            "action": action_result,
            "escalation": escalation_result,
            "nhr_token": nhr_token
        }
        try:
            send_to_power_automate(escalation_payload)
        except Exception:
            pass

    # FINALIZE STATUS for both paths
    try:
        final_status = "executed" if executed else ("escalated" if escalation_payload else "no_action")
        supabase.table("email_logs").update({
            "status": final_status
        }).eq("email_id", email.internet_message_id).execute()
    except Exception:
        pass

    # CONSISTENT RESPONSE for both paths
    return {
        "status": "processed",
        "triage": triage_result,
        "action": action_result,
        "executed": executed,
        "escalated": escalation_payload is not None
    }

@app.post("/feedback")
def feedback(p: FeedbackPayload):
    # 1) look up the email by nhr_token
    row = supabase.table("email_decisions").select("email_id") \
           .eq("nhr_token", p.nhr_token).eq("stage", "nhr").single().execute().data
    if not row:
        raise HTTPException(404, "nhr_token not found")

    email_id = row["email_id"]
    # 2) upsert human decision
    supabase.table("email_decisions").insert({
        "email_id": email_id,
        "stage": "human",
        "classification": p.final_classification,
        "confidence": 1.0,
        "rationale": (p.human or ""),
        "nhr": False,
        "nhr_token": p.nhr_token,
    }).execute()

    # 3) fetch the email payload (from logs) and recompute actions
    email_log = supabase.table("email_logs").select("*").eq("email_id", email_id).single().execute().data
    if not email_log:
        raise HTTPException(404, "email_id not found in email_logs")
    email = _normalize_n8n_payload({
        "subject": email_log["subject"],
        "body": email_log.get("body_text") or "",
        "from_address": email_log.get("from_email") or "",
        "message_id": email_log.get("message_id") or email_id,
        "internet_message_id": email_id,
        "attachment_links": email_log.get("attachment_links") or [],
    })

    triage_result = {"classification": p.final_classification, "confidence": 1.0, "rationale": ["human override"]}
    actions = decide_actions(email, triage_result)      # maps class -> steps from rules/actions.yaml
    result = {
        "agree": True,
        "needs_human_review": False,
        "final_classification": p.final_classification,
        "final_confidence": 1.0,
        "final_rationale": ["human override"],
        "actions": actions,
    }
    receipts = execute_actions(email, result, supabase=supabase)
    return {"status": "ok", "executed": receipts}

@app.post("/policy/refresh")
def policy_refresh():
    try:
        result = update_policy_from_logs(supabase)
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))