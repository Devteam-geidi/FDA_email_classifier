# src/app/agents/action.py
from typing import List, Dict, Any
import os

# uses our tiny helpers
from ..utils.tools import call_tool
from ..utils.rules import (
    get_actions_for_classification,
    render_action_params,
    _flatten_email_for_template,
)

# Confidence threshold for auto-pilot (also used by /ingest orchestrator)
MIN_AUTOPILOT = float(os.getenv("MIN_AUTOPILOT", "0.75"))


def decide_actions(email, triage_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Look up actions for the triage classification from rules/actions.yaml,
    render env + email field placeholders, and return a normalized list:
      [{"action": "<tool>", "params": {...}}, ...]
    """
    cls = (triage_result.get("classification") or "").lower()
    raw_actions = get_actions_for_classification(cls)

    ctx = _flatten_email_for_template(email)
    actions: List[Dict[str, Any]] = []
    for step in raw_actions:
        # step is like {"forward": {...}} OR {"delete": {}}
        if not isinstance(step, dict) or len(step) != 1:
            continue
        action, params = list(step.items())[0]
        params = params or {}
        rendered = render_action_params(params, ctx)
        actions.append({"action": action, "params": rendered})
    return actions


def run_action_agent(email, triage_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight "verification": we trust Triage by default.
    If confidence is below MIN_AUTOPILOT, we flag for human review.
    Actions are chosen purely from rules/actions.yaml.
    """
    final_cls = triage_result.get("classification", "other")
    final_conf = float(triage_result.get("confidence", 0.0))

    actions = decide_actions(email, triage_result)

    needs_human_review = final_conf < MIN_AUTOPILOT
    agree = True  # keep simple: we agree unless another signal says otherwise

    return {
        "agree": agree,
        "needs_human_review": needs_human_review,
        "final_classification": final_cls,
        "final_confidence": final_conf,
        "final_rationale": triage_result.get("rationale", []),
        "actions": actions,
    }


from typing import List, Dict, Any
from ..utils.tools import call_tool

def execute_actions(email, action_result: Dict[str, Any], supabase=None) -> List[Dict[str, Any]]:
    receipts: List[Dict[str, Any]] = []
    meta = {
        "message_id": email.message_id,
        "internet_message_id": email.internet_message_id,
        "subject": email.subject,
        "from": email.from_.dict() if hasattr(email.from_, "dict") else vars(email.from_),
        "to": [p.dict() if hasattr(p, "dict") else vars(p) for p in (email.to or [])],
        "cc": [p.dict() if hasattr(p, "dict") else vars(p) for p in (email.cc or [])],
        "headers": email.headers,
        "attachments": [a.dict() if hasattr(a, "dict") else vars(a) for a in (email.attachments or [])],
    }

    # keep a quick lookup of params per action for logging
    action_params_map = {a["action"]: a.get("params", {}) for a in action_result.get("actions", [])}

    for step in action_result.get("actions", []):
        action = step.get("action")
        params = step.get("params", {})
        payload = {"email": meta, "params": params}

        if action == "forward" and isinstance(params.get("to"), str):
            params["to"] = [params["to"]]

        res = call_tool(action, payload)  # {"ok","status","body","url"} or {"ok":False,"error","url"}
        receipts.append({"action": action, "ok": res.get("ok"), "detail": res})

        # Supabase audit log (optional)
        if supabase is not None:
            try:
                supabase.table("action_runs").insert({
                    "email_id": email.internet_message_id,
                    "action": action,
                    "url": res.get("url"),
                    "request": {"params": action_params_map.get(action, {}), "email_id": email.internet_message_id},
                    "response_status": res.get("status"),
                    "response_body": res.get("body") or res.get("error")
                }).execute()
            except Exception:
                # don't fail the whole pipeline on logging issues
                pass

    return receipts
