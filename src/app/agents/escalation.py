import time, random, logging
from openai import OpenAI
from openai import APIError, APIConnectionError, RateLimitError, APITimeoutError
try:
    # InternalServerError is in recent SDKs; if not available, we’ll catch APIError anyway
    from openai import InternalServerError
except Exception:  # fallback for older SDKs
    class InternalServerError(APIError): ...

import json
import os
from typing import List, Dict, Tuple
from openai import OpenAI
import httpx
import yaml

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
POWER_AUTOMATE_URL = os.getenv("POWER_AUTOMATE_URL")
openai_client = OpenAI()


logger = logging.getLogger(__name__)

# Create a client with a sensible timeout (seconds)
openai_client = OpenAI(timeout=120)

RETRY_EXCEPTIONS = (InternalServerError, APIError, APIConnectionError, APITimeoutError, RateLimitError)

# --- taxonomy parsing helpers ---

def _retry_call(func, *, attempts=4, base_delay=0.5, jitter=0.3, **kwargs):
    """Generic retry with exponential backoff and jitter."""
    for i in range(attempts):
        try:
            return func(**kwargs)
        except RETRY_EXCEPTIONS as e:
            last = (i == attempts - 1)
            req_id = None
            try:
                req_id = getattr(getattr(e, "response", None), "headers", {}).get("x-request-id")
            except Exception:
                pass
            logger.warning(
                "Escalation LLM call failed (attempt %s/%s). request_id=%s err=%s",
                i + 1, attempts, req_id, repr(e),
            )
            if last:
                raise
            sleep_for = base_delay * (2 ** i) + random.uniform(0, jitter)
            time.sleep(sleep_for)

def _call_escalation_llm(prompt: str):
    # NOTE: adjust model variable if needed
    return openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
    
def _extract_taxonomy(yaml_text: str) -> Dict[str, Dict]:
    """
    Returns the raw taxonomy dict from the policy YAML:
      { "<key>": {"group": "...", ...}, ... }
    """
    try:
        data = yaml.safe_load(yaml_text) or {}
        return (data.get("taxonomy") or {}) if isinstance(data, dict) else {}
    except Exception:
        return {}

def _build_flat_and_grouped(taxonomy: Dict[str, Dict]) -> Tuple[List[str], Dict[str, List[str]]]:
    """
    Builds:
      - flat list of keys (sorted, includes 'other' fallback)
      - grouped map: { "<Group>": [taxonomy_keys...] }
    If no 'other' taxon exists in the YAML, we synthesize it under group "Other".
    """
    grouped: Dict[str, List[str]] = {}
    flat: List[str] = []

    for key, meta in taxonomy.items():
        grp = (meta or {}).get("group") or "Other"
        grouped.setdefault(grp, []).append(key)
        flat.append(key)

    # ensure synthetic 'other' exists for safety
    if "other" not in flat:
        flat.append("other")
        grouped.setdefault("Other", []).append("other")

    # sort keys within each group and sort groups by name
    for g in grouped:
        grouped[g] = sorted(grouped[g])
    flat = sorted(flat)

    # ensure deterministic group order by recreating dict
    grouped = dict(sorted(grouped.items(), key=lambda kv: kv[0].lower()))
    return flat, grouped

def _grouped_as_obj_list(grouped: Dict[str, List[str]]) -> List[Dict]:
    """
    Converts grouped map into UI-friendly array objects:
      [{"group":"Accounts Payable","options":[...]} , ...]
    """
    return [{"group": grp, "options": opts} for grp, opts in grouped.items()]

# --- prompt ---

def escalation_prompt(email, triage: dict, action: dict, yaml_rules: str,
                      flat_options: List[str],
                      grouped_options: Dict[str, List[str]],
                      grouped_objs: List[Dict]) -> str:
    return f"""
SYSTEM:
You are the escalation agent. The action agent flagged this email as needing human review. Summarize the situation clearly, include triage and action rationales, and propose the best guess classification. Do not execute actions.

YAML_RULES:
{yaml_rules}

AVAILABLE_CLASSIFICATIONS_FLAT:
{json.dumps(flat_options, ensure_ascii=False)}

AVAILABLE_CLASSIFICATIONS_GROUPED:
{json.dumps(grouped_options, ensure_ascii=False)}

AVAILABLE_CLASSIFICATIONS_GROUPED_OBJS:
{json.dumps(grouped_objs, ensure_ascii=False)}

TRIAGE:
{json.dumps(triage, ensure_ascii=False)}

ACTION_AGENT:
{json.dumps(action, ensure_ascii=False)}

EMAIL SUBJECT: {email.subject}

OUTPUT (JSON only):
{{
  "proposed_classification": "<one key from AVAILABLE_CLASSIFICATIONS_FLAT>",
  "rationale": ["bullet evidence"],
  "guideline_options": {json.dumps(flat_options, ensure_ascii=False)},
  "guideline_options_grouped": {json.dumps(grouped_options, ensure_ascii=False)},
  "guideline_options_grouped_objs": {json.dumps(grouped_objs, ensure_ascii=False)}
}}
""".strip()

# --- main entrypoint ---

def run_escalation_agent(email, triage: dict, action: dict) -> dict:
    # Reuse the same YAML loader as triage uses
    from app.agents.triage import load_yaml_rules
    yaml_rules = load_yaml_rules()  # string contents of the policy file

    taxonomy = _extract_taxonomy(yaml_rules)
    flat_options, grouped_map = _build_flat_and_grouped(taxonomy)
    grouped_objs = _grouped_as_obj_list(grouped_map)

    prompt = escalation_prompt(
        email, triage, action, yaml_rules,
        flat_options=flat_options,
        grouped_options=grouped_map,
        grouped_objs=grouped_objs,
    )

    # --- PATCH: add retries + graceful degradation ---
    try:
        resp = _retry_call(_call_escalation_llm, prompt=prompt)
    except RETRY_EXCEPTIONS as e:
        req_id = None
        try:
            req_id = getattr(getattr(e, "response", None), "headers", {}).get("x-request-id")
        except Exception:
            pass
        logger.exception("Escalation agent failed after retries. request_id=%s", req_id)

        # Degrade gracefully: return a structured result so upstream stays 200 OK
        return {
            "status": "skipped",
            "reason": "escalation_llm_error",
            "error": str(e),
            "request_id": req_id,
            "proposed_classification": "other",
            "rationale": ["Escalation LLM error; fallback used"],
            "guideline_options": flat_options,
            "guideline_options_grouped": grouped_map,
            "guideline_options_grouped_objs": grouped_objs,
        }
    # ---------------------------------------------------

    text = resp.output_text
    try:
        parsed = json.loads(text)
        # Guardrails: make sure classification is one of our flat options
        if parsed.get("proposed_classification") not in flat_options:
            parsed["proposed_classification"] = "other"

        # Ensure all option structures are present even if the model omits them
        parsed.setdefault("guideline_options", flat_options)
        parsed.setdefault("guideline_options_grouped", grouped_map)
        parsed.setdefault("guideline_options_grouped_objs", grouped_objs)
        return parsed
    except Exception:
        return {
            "proposed_classification": "other",
            "rationale": ["Parse error"],
            "guideline_options": flat_options,
            "guideline_options_grouped": grouped_map,
            "guideline_options_grouped_objs": grouped_objs,
        }

def send_to_power_automate(payload: dict) -> dict:
    try:
        res = httpx.post(POWER_AUTOMATE_URL, json=payload)
        return {"status": "ok", "resp": res.json()}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
