import os, yaml
from typing import Any, Dict, List

_RULES: Dict[str, Any] = {}

def load_action_rules(path: str = "rules/actions.yaml") -> Dict[str, Any]:
    global _RULES
    with open(path, "r", encoding="utf-8") as f:
        _RULES = yaml.safe_load(f) or {}
    return _RULES

def get_actions_for_classification(cls: str) -> List[Dict[str, Any]]:
    classes = (_RULES.get("classifications") or {})
    entry = classes.get(cls) or classes.get("default") or {"actions": []}
    return entry.get("actions", [])

def _flatten_email_for_template(email) -> Dict[str, Any]:
    # Minimal context for string templates
    return {
        "subject": getattr(email, "subject", "") or "",
        "from_email": getattr(getattr(email, "from_", None), "email", "") or "",
        "internet_message_id": getattr(email, "internet_message_id", "") or "",
        "message_id": getattr(email, "message_id", "") or "",
        "weblink": (getattr(email, "headers", {}) or {}).get("WebLink") or "",
    }

def _render_value(val: Any, ctx: Dict[str, Any]) -> Any:
    # supports {env:VAR} and {field} with Python format
    if isinstance(val, str):
        # env substitution
        if "{env:" in val:
            def repl_env(s: str) -> str:
                out = s
                # crude but effective: find {...} and replace env tokens
                import re
                for match in re.finditer(r"\{env:([A-Z0-9_]+)\}", s):
                    var = match.group(1)
                    out = out.replace(match.group(0), os.getenv(var, ""))
                return out
            val = repl_env(val)
        try:
            return val.format(**ctx)
        except Exception:
            return val
    if isinstance(val, list):
        return [_render_value(x, ctx) for x in val]
    if isinstance(val, dict):
        return {k: _render_value(v, ctx) for k, v in val.items()}
    return val

def render_action_params(raw_params: Dict[str, Any], email_ctx: Dict[str, Any]) -> Dict[str, Any]:
    return _render_value(raw_params, email_ctx)

EMAIL_POLICY_PATH = "rules/email_policy.yaml"

def load_email_policy() -> dict:
    try:
        with open(EMAIL_POLICY_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

def get_taxonomy_keys() -> list[str]:
    policy = load_email_policy()
    taxonomy = policy.get("taxonomy") or {}
    return list(taxonomy.keys())

def get_taxonomy_options() -> list[dict]:
    """
    Returns:
      [
        {"title": "<taxonomy.key>", "value": "<taxonomy.key>"},
        ...
      ]
    """
    return [{"title": k, "value": k} for k in get_taxonomy_keys()]