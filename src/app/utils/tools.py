# src/app/utils/tools.py
import os
import httpx

# Map action name -> env var with the webhook URL
ACTION_URLS = {
    "forward": os.getenv("N8N_FORWARD_URL"),
    "move": os.getenv("N8N_MOVE_URL"),
    "flag": os.getenv("N8N_FLAG_URL"),
    "delete": os.getenv("N8N_DELETE_URL"),
    "create_jira": os.getenv("N8N_CREATE_JIRA_URL"),
    # add more mappings as needed...
}

def call_tool(action: str, payload: dict) -> dict:
    """
    Call the n8n webhook for the given action with the given payload.
    Returns dict with ok flag, status, body, and url.
    """
    url = ACTION_URLS.get(action)
    if not url:
        return {"ok": False, "error": f"No URL configured for action {action}", "url": None}

    try:
        res = httpx.post(url, json=payload, timeout=30)
        return {
            "ok": res.status_code == 200,
            "status": res.status_code,
            "body": res.json() if res.headers.get("content-type", "").startswith("application/json") else res.text,
            "url": url,
        }
        return {"ok": 200 <= res.status_code < 300, "status": res.status_code, "body": body, "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}