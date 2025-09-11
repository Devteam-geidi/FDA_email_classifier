import json
from dotenv import load_dotenv
from openai import OpenAI
import os

# Load .env variables
load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

openai_client = OpenAI(
    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    api_key=os.getenv("OPENAI_API_KEY"),
    default_headers={
        "Helicone-Auth": f"Bearer {os.getenv('HELICONE_API_KEY')}",  # Helicone API key fron env
        "Helicone-Property-Project": "Email-Triage-System"            
    }
)


def load_yaml_rules() -> str:
    with open("rules/email_policy.yaml", "r") as f:
        return f.read()


def triage_prompt(email, yaml_rules: str) -> str:
    return f"""
SYSTEM:
You are the triage agent for an email system. Classify the email strictly following the YAML taxonomy. Extract invoice fields if present.

YAML_RULES:
{yaml_rules}

EMAIL:
From: {email.from_.name} <{email.from_.email}>
To: {[p.email for p in email.to]}
Subject: {email.subject}
Body: {email.body_text or ''}

OUTPUT (JSON only):
{{
  "classification": "<taxonomy key>",
  "confidence": 0.0-1.0,
  "rationale": ["bullet evidence"],
  "extracted": {{"invoice_number":"...","due_date":"...","total":"...","vendor":"..."}}
}}
"""


def run_triage(email) -> dict:
    yaml_rules = load_yaml_rules()
    prompt = triage_prompt(email, yaml_rules)
    resp = openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
    text = resp.output_text
    try:
        return json.loads(text)
    except Exception:
        return {
            "classification": "other",
            "confidence": 0.0,
            "rationale": ["Failed to parse"],
            "extracted": {}
        }