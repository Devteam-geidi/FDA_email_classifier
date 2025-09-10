# src/app/agents/escalation.py
import json
import os
from typing import List
import httpx
import yaml

from openai import OpenAI

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
POWER_AUTOMATE_URL = os.getenv("POWER_AUTOMATE_URL")
openai_client = OpenAI()


def _extract_taxonomy_keys(yaml_text: str) -> List[str]:
    """Parse email_policy.yaml text and return sorted list of taxonomy keys."""
    try:
        data = yaml.safe_load(yaml_text) or {}
        taxonomy = data.get("taxonomy", {}) or {}
        keys = [str(k) for k in taxonomy.keys()]
        if "other" not in keys:
            keys.append("other")
        return sorted(keys)
    except Exception:
        # conservative fallback
        return ["invoice.paid", "invoice.unpaid", "invoice.overdue", "task", "support", "spam", "newsletter", "other"]


def escalation_prompt(
    email,
    triage: dict,
    action: dict,
    yaml_rules: str,
    options: List[str],
) -> str:
    # keep the YAML under control if massive
    yaml_rules_short = (yaml_rules or "")[:40000]

    return f"""
SYSTEM:
You are the escalation agent. The action agent flagged this email as needing human review.
Summarize the situation clearly, include triage and action rationales, and propose the BEST
classification strictly from AVAILABLE_CLASSIFICATIONS. Do not execute actions.

Return ONLY a single JSON object, no prose before or after.

YAML_RULES (truncated):
{yaml_rules_short}

AVAILABLE_CLASSIFICATIONS (choose exactly one):
{json.dumps(options)}

TRIAGE:
{json.dumps(triage)}

ACTION_AGENT:
{json.dumps(action)}

EMAIL SUBJECT: {email.subject}

OUTPUT (JSON only):
{{
  "proposed_classification": "<one key from AVAILABLE_CLASSIFICATIONS>",
  "rationale": ["short bullet evidence 1", "short bullet evidence 2"],
  "guideline_options": {json.dumps(options)}
}}
""".strip()


def run_escalation_agent(email, triage: dict, action: dict) -> dict:
    # Avoid import cycles; if you have a central loader, import it here.
    from app.agents.triage import load_yaml_rules  # lazy import

    yaml_rules = load_yaml_rules() or ""
    options = _extract_taxonomy_keys(yaml_rules)  # list[str]

    prompt = escalation_prompt(email, triage, action, yaml_rules, options)

    # Call OpenAI Responses API
    resp = openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
    text = getattr(resp, "output_text", None) or getattr(resp, "content", None) or ""

    try:
        parsed = json.loads(text)
        # Harden: ensure proposed_classification is one of the available options
        pc = parsed.get("proposed_classification")
        if pc not in options:
            parsed["proposed_classification"] = "other"
        parsed["guideline_options"] = options
        return parsed
    except Exception:
        return {
            "proposed_classification": "other",
            "rationale": ["Parse error or non-JSON response"],
            "guideline_options": options,  # <-- fixed from undefined options_objs
        }


def send_to_power_automate(payload: dict) -> dict:
    if not POWER_AUTOMATE_URL:
        return {"status": "skipped", "error": "POWER_AUTOMATE_URL not configured"}

    try:
        res = httpx.post(POWER_AUTOMATE_URL, json=payload, timeout=15)
        # Be defensive about JSON decoding
        try:
            body = res.json()
        except Exception:
            body = {"text": res.text[:2000]}
        return {"status": "ok" if res.status_code < 400 else "error", "code": res.status_code, "resp": body}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
