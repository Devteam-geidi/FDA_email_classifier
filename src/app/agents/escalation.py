import json
import os
from openai import OpenAI
import httpx

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
POWER_AUTOMATE_URL = os.getenv("POWER_AUTOMATE_URL")
openai_client = OpenAI()


def escalation_prompt(email, triage: dict, action: dict, yaml_rules: str) -> str:
    return f"""
SYSTEM:
You are the escalation agent. The action agent has flagged this email as needing human review. Summarize the situation clearly, include triage and action rationales, and propose the best guess classification. Do not execute actions.

YAML_RULES:
{yaml_rules}

TRIAGE:
{json.dumps(triage)}

ACTION_AGENT:
{json.dumps(action)}

EMAIL SUBJECT: {email.subject}

OUTPUT (JSON only):
{{
  "proposed_classification": "<taxonomy key>",
  "rationale": ["bullet evidence"],
  "guideline_options": ["invoice.paid","invoice.unpaid","invoice.overdue","task","support","spam","newsletter","other"]
}}
"""


def run_escalation_agent(email, triage: dict, action: dict) -> dict:
    from app.agents.triage import load_yaml_rules
    yaml_rules = load_yaml_rules()
    prompt = escalation_prompt(email, triage, action, yaml_rules)
    resp = openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
    text = resp.output_text
    try:
        return json.loads(text)
    except Exception:
        return {
            "proposed_classification": "other",
            "rationale": ["Parse error"],
            "guideline_options": ["other"]
        }


def send_to_power_automate(payload: dict) -> dict:
    try:
        res = httpx.post(POWER_AUTOMATE_URL, json=payload)
        return {"status": "ok", "resp": res.json()}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
