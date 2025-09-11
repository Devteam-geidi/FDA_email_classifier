# app/utils/message_id_helper.py
from typing import Optional
from supabase import Client

def replace_message_id_everywhere(
    supabase: Client,
    old_message_id: str,
    new_message_id: str,
) -> dict:
    """
    Update all rows that reference old_message_id to use new_message_id.
    Safe if called multiple times (idempotent-ish).
    Returns a small summary of the changes.
    """
    if not old_message_id or not new_message_id or old_message_id == new_message_id:
        return {"updated": False, "reason": "noop"}

    summary = {"email_logs": 0, "email_decisions": 0, "action_runs": 0}

    # 1) email_logs (could be >1 if you ever re-ingest; we target by primary key for safety)
    logs = (
        supabase.table("email_logs")
        .select("id")
        .eq("message_id", old_message_id)
        .execute()
        .data
        or []
    )
    for r in logs:
        supabase.table("email_logs").update({"message_id": new_message_id}).eq("id", r["id"]).execute()
        summary["email_logs"] += 1

    # 2) email_decisions (stage rows)
    try:
        res = (
            supabase.table("email_decisions")
            .update({"message_id": new_message_id})
            .eq("message_id", old_message_id)
            .execute()
            .data
        ) or []
        summary["email_decisions"] = len(res)
    except Exception:
        pass

    # 3) action_runs (make sure you have a message_id column here)
    try:
        res = (
            supabase.table("action_runs")
            .update({"message_id": new_message_id})
            .eq("message_id", old_message_id)
            .execute()
            .data
        ) or []
        summary["action_runs"] = len(res)
    except Exception:
        pass

    # (Optional) record the mapping for audit/debug (create a table message_id_aliases if you like)
    # supabase.table("message_id_aliases").insert({
    #     "old_message_id": old_message_id,
    #     "new_message_id": new_message_id
    # }).execute()

    return {"updated": True, "summary": summary}
