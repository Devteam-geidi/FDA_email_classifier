# app/agents/policy_refiner.py
from collections import Counter, defaultdict
from typing import Dict, List, Tuple
import re
from supabase import Client
from app.utils.policy_edit import load_policy, save_policy, upsert_class
from app.utils.gh_actions import dispatch_policy_workflow

STOP = set("""
a an and are as at be but by for from has have if in into is it of on or our so that the their this to was were will with your you we they he she them his her its not no
""".split())

RE_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    toks = [t for t in RE_TOKEN.findall((text or "").lower()) if t not in STOP and len(t) > 2]
    return toks


def ngrams(tokens: List[str], n: int) -> List[str]:
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def extract_top_phrases(records: List[Dict], top_k: int = 12) -> Tuple[List[str], List[str]]:
    """
    Return (positives, negatives) candidate phrases.
    Assumes records are all for the same target class; each record has 'subject' and 'body_text',
    and optionally a 'negatives' list of competitor texts to mine.
    """
    pos_counter = Counter()
    neg_counter = Counter()

    for r in records:
        text = f"{r.get('subject','')}\n{r.get('body_text','')}"
        toks = tokenize(text)
        for n in (2, 3, 4):
            pos_counter.update(ngrams(toks, n))
        for neg in r.get("negatives", []):
            ntoks = tokenize(neg)
            for n in (2, 3, 4):
                neg_counter.update(ngrams(ntoks, n))

    positives = [p for p, _ in pos_counter.most_common(top_k)]
    negatives = [p for p, _ in neg_counter.most_common(top_k)]
    return positives, negatives


def fetch_confusing_samples(supabase: Client, *, limit_days: int = 30, min_len: int = 40) -> Dict[str, List[Dict]]:
    """
    Pull low-confidence & escalated samples grouped by class.
    Returns {class_key: [{subject, body_text, negatives:[...]}, ...]}
    """
    # Fetch decisions and logs (adjust fields/filters to your schema as needed)
    triage = supabase.table("email_decisions").select(
        "email_id, classification, confidence, stage, nhr"
    ).execute().data
    logs = supabase.table("email_logs").select(
        "email_id, subject, body_text"
    ).execute().data
    logs_by_id = {r["email_id"]: r for r in logs}

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
        body = log.get("body_text") or ""
        if len(body) < min_len:
            continue

        key = d.get("classification") or "other"
        grouped[key].append({
            "subject": log.get("subject", ""),
            "body_text": body,
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
        desc = None
        mh = s.get("must_haves") or []
        if mh:
            desc = f"Emails typically mention: {', '.join(mh[:3])}."
        policy = upsert_class(
            policy,
            cls,
            description=desc,
            must_haves=mh,
            must_not_haves=s.get("must_not_haves") or []
        )

    save_policy(policy)

    # ✅ NEW: Trigger GitHub Action to create branch + PR
    from app.utils.gh_actions import dispatch_policy_workflow
    dispatch_policy_workflow()

    return {
        "updated_classes": list(suggestions.keys()),
        "counts": {k: len(v) for k, v in samples.items()}
    }

