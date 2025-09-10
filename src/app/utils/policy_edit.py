# app/utils/policy_edit.py
import os, time, yaml
from typing import Dict, List

_env_path = "rules/email_policy.yaml"

POLICY_PATH = os.path.abspath(_env_path)
POLICY_DIR = os.path.dirname(POLICY_PATH) or "."
HISTORY_DIR = os.path.join(POLICY_DIR, ".history")
SAFE_DEFAULT = {"taxonomy": {}}

def load_policy() -> Dict:
    try:
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or SAFE_DEFAULT
    except Exception:
        return SAFE_DEFAULT


def _backup_current(policy_text: str) -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    with open(f"{HISTORY_DIR}/email_policy_{stamp}.yaml", "w", encoding="utf-8") as f:
        f.write(policy_text)


def save_policy(policy: Dict) -> None:
    try:
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            _backup_current(f.read())
    except Exception:
        pass

    os.makedirs(os.path.dirname(POLICY_PATH) or ".", exist_ok=True)
    tmp_path = POLICY_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(policy, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp_path, POLICY_PATH)


def ensure_taxonomy(policy: Dict) -> Dict:
    if "taxonomy" not in policy or not isinstance(policy["taxonomy"], dict):
        policy["taxonomy"] = {}
    return policy


def merge_list(dst: List[str], src: List[str]) -> List[str]:
    seen = { (s or "").strip().lower(): s for s in dst if isinstance(s, str) }
    for s in src or []:
        if not isinstance(s, str):
            continue
        k = s.strip().lower()
        if k and k not in seen:
            seen[k] = s.strip()
    return [seen[k] for k in seen]


def upsert_class(policy: Dict, key: str, *,
                 description: str | None = None,
                 must_haves: List[str] | None = None,
                 must_not_haves: List[str] | None = None) -> Dict:
    policy = ensure_taxonomy(policy)
    entry = policy["taxonomy"].get(key) or {}
    if description:
        if not entry.get("description") or len(entry["description"]) < 40:
            entry["description"] = description.strip()
    if must_haves:
        entry["must_haves"] = merge_list(entry.get("must_haves", []), must_haves)
    if must_not_haves:
        entry["must_not_haves"] = merge_list(entry.get("must_not_haves", []), must_not_haves)
    policy["taxonomy"][key] = entry
    return policy
