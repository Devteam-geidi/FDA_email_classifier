# app/agents/policy_refiner.py


def fetch_confusing_samples(supabase: Client, *, limit_days: int = 30, min_len: int = 40) -> Dict[str, List[Dict]]:
"""Pull low-confidence & escalated samples grouped by class.
Returns {class_key: [{subject, body_text, negatives:[...]}, ...]}
"""
# 1) Get triage and action decisions with confidence < threshold or nhr=true
# We join to email_logs to get bodies.
# NOTE: adjust filter syntax to your Supabase Python client.
triage = supabase.table("email_decisions").select("email_id, classification, confidence, stage, nhr").execute().data
logs = supabase.table("email_logs").select("email_id, subject, body_text").execute().data
logs_by_id = {r["email_id"]: r for r in logs}


# derive confusing set
grouped: Dict[str, List[Dict]] = defaultdict(list)
for d in triage:
if d.get("stage") not in ("triage", "nhr"):
continue
conf = float(d.get("confidence") or 0.0)
nhr = bool(d.get("nhr"))
if conf >= 0.75 and not nhr:
continue
eid = d.get("email_id")
log = logs_by_id.get(eid) or {}
text_ok = (log.get("body_text") or "")
if len(text_ok) < min_len:
continue
grouped[d.get("classification") or "other"].append({
"subject": log.get("subject", ""),
"body_text": text_ok,
# (future) fill negatives from competitor classes if needed
"negatives": []
})
return grouped




def build_policy_edits(samples_by_class: Dict[str, List[Dict]]) -> Dict[str, Dict[str, List[str]]]:
"""Compute suggested must_haves and must_not_haves per class."""
suggestions: Dict[str, Dict[str, List[str]]] = {}
for cls, recs in samples_by_class.items():
if not recs:
continue
pos, neg = extract_top_phrases(recs)
suggestions[cls] = {
"must_haves": pos[:10],
"must_not_haves": neg[:10],
}
return suggestions




def update_policy_from_logs(supabase: Client) -> Dict:
"""Main entry: read logs → compute phrases → update YAML file."""
samples = fetch_confusing_samples(supabase)
suggestions = build_policy_edits(samples)


policy = load_policy()


for cls, s in suggestions.items():
# synthesize a short description if missing/short
desc = None
mh = s.get("must_haves") or []
if mh:
desc = f"Emails typically mention: {', '.join(mh[:3])}."
policy = upsert_class(policy, cls, description=desc, must_haves=mh, must_not_haves=s.get("must_not_haves") or [])


save_policy(policy)
return {"updated_classes": list(suggestions.keys()), "counts": {k: len(v) for k, v in samples.items()}}