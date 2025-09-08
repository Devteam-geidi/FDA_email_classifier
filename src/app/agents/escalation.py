import json
import os
from app.utils.rules import get_taxonomy_options
from typing import List
from openai import OpenAI
import httpx
import yaml  # <-- add pyyaml in your deps if not already

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
POWER_AUTOMATE_URL = os.getenv("POWER_AUTOMATE_URL")
openai_client = OpenAI()

def _extract_taxonomy_keys(yaml_text: str) -> List[str]:
    """Parse email_policy.yaml text and return sorted list of taxonomy keys."""
    try:
        data = yaml.safe_load(yaml_text) or {}
        taxonomy = data.get("taxonomy", {}) or {}
        keys = [str(k) for k in taxonomy.keys()]
        # always include 'other' as a safety fallback
        if "other" not in keys:
            keys.append("other")
        return sorted(keys)
    except Exception:
        # absolute fallback
        return ["invoice.paid", "invoice.unpaid", "invoice.overdue", "task", "support", "spam", "newsletter", "other"]

def escalation_prompt(email, triage: dict, action: dict, yaml_rules: str, options: list[str], options_objs: list[dict]) -> str:
    return f"""
SYSTEM:
You are the escalation agent. The action agent flagged this email as needing human review. Summarize the situation clearly, include triage and action rationales, and propose the best guess classification. Do not execute actions.

YAML_RULES:
{yaml_rules}

AVAILABLE_CLASSIFICATIONS (choose one of these, exactly as written):
{json.dumps(options)}

TRIAGE:
{json.dumps(triage)}

ACTION_AGENT:
{json.dumps(action)}

EMAIL SUBJECT: {email.subject}

OUTPUT (JSON only):
{{
  "proposed_classification": "<one key from AVAILABLE_CLASSIFICATIONS>",
  "rationale": ["bullet evidence"],
  "guideline_options": {json.dumps(options)}
}}
""".strip()

def run_escalation_agent(email, triage: dict, action: dict) -> dict:
    from app.agents.triage import load_yaml_rules

    yaml_rules = load_yaml_rules()
    options = _extract_taxonomy_keys(yaml_rules)              # list[str]

    prompt = escalation_prompt(email, triage, action, yaml_rules, options, options)

    resp = openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
    text = resp.output_text
    try:
        parsed = json.loads(text)
        # harden: ensure the proposed_classification is one of the options
        pc = parsed.get("proposed_classification")
        if pc not in options:
            parsed["proposed_classification"] = "other"
        # always include the object list for UI
        parsed["guideline_options"] = options
        return parsed
    except Exception:
        return {
            "proposed_classification": "other",
            "rationale": ["Parse error"],
            "guideline_options": options_objs
        }

def send_to_power_automate(payload: dict) -> dict:
    try:
        res = httpx.post(POWER_AUTOMATE_URL, json=payload)
        return {"status": "ok", "resp": res.json()}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
 