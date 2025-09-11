import json
import os
from app.agents.triage import load_yaml_rules
from app.utils.rules import get_taxonomy_options
from typing import List, Dict, Tuple
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

def _extract_guideline_options(yaml_text: str) -> Tuple[List[str], List[Dict[str, List[str]]]]:
    """
    Build:
      - flat list of keys (sorted)
      - grouped options: [{"group":"Accounts Payable","items":["invoice.unpaid","invoice.overdue"]}, ...]
    Groups come from each class's `group:` field. Classes without a group go under "Ungrouped".
    Ensures "other" exists (in Ungrouped if absent).
    """
    taxonomy = _extract_taxonomy_from_yaml(yaml_text)

    # flat options
    flat: List[str] = sorted([str(k) for k in taxonomy.keys()])

    # ensure "other" always present in flat
    if "other" not in flat:
        flat.append("other")

    # group -> [keys...]
    groups: Dict[str, List[str]] = {}
    for key, cfg in taxonomy.items():
        grp = (cfg or {}).get("group") or "Ungrouped"
        groups.setdefault(grp, []).append(key)

    # if "other" didn’t exist in taxonomy, put it in Ungrouped
    if "other" not in taxonomy:
        groups.setdefault("Ungrouped", []).append("other")

    # sort items inside each group & build stable list
    grouped = [{"group": g, "items": sorted(items)} for g, items in groups.items()]
    # ensure deterministic order by group name
    grouped.sort(key=lambda x: x["group"].lower())

    return flat, grouped

def escalation_prompt(email, triage: dict, action: dict, yaml_rules: str,
                      flat_options: list[str], grouped_options: list[dict]) -> str:
    # Render a compact, human-friendly view of groups for the model
    grouped_lines = []
    for g in grouped_options:
        grouped_lines.append(f"- {g['group']}")
        for k in g["items"]:
            grouped_lines.append(f"  • {k}")
    grouped_text = "\n".join(grouped_lines)

    return f"""
SYSTEM:
You are the escalation agent. The action agent flagged this email as needing human review.
Use the current policy, and select the best single classification key from the allowed set.

YAML_RULES:
{yaml_rules}

AVAILABLE_CLASSIFICATIONS (flat list; must pick exactly one of these):
{json.dumps(flat_options)}

CLASSIFICATIONS BY GROUP (reference only; same keys grouped):
{grouped_text}

TRIAGE:
{json.dumps(triage)}

ACTION_AGENT:
{json.dumps(action)}

EMAIL SUBJECT: {email.subject}

OUTPUT (JSON only):
{{
  "proposed_classification": "<one key from AVAILABLE_CLASSIFICATIONS>",
  "rationale": ["bullet evidence"]
}}
""".strip()

def run_escalation_agent(email, triage: dict, action: dict) -> dict:
    yaml_rules = load_yaml_rules()  # raw YAML text
    flat_options, grouped_options = _extract_guideline_options(yaml_rules)

    prompt = escalation_prompt(email, triage, action, yaml_rules, flat_options, grouped_options)
    resp = openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
    text = resp.output_text

    try:
        parsed = json.loads(text)
    except Exception:
        parsed = {"proposed_classification": "other", "rationale": ["Parse error"]}

    # harden to flat options
    if parsed.get("proposed_classification") not in flat_options:
        parsed["proposed_classification"] = "other"

    # include both flat + grouped in the result we return to main.py
    parsed["guideline_options"] = flat_options
    parsed["guideline_groups"] = grouped_options
    return parsed

def send_to_power_automate(payload: dict) -> dict:
    try:
        res = httpx.post(POWER_AUTOMATE_URL, json=payload)
        return {"status": "ok", "resp": res.json()}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
 