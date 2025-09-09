# app/utils/policy_edit.py
import os, io, time, re, yaml
from typing import Dict, List


POLICY_PATH = os.getenv("EMAIL_POLICY_PATH", "rules/email_policy.yaml")
HISTORY_DIR = os.path.dirname(POLICY_PATH) + "/.history"


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
# take a backup of existing file
try:
with open(POLICY_PATH, "r", encoding="utf-8") as f:
_backup_current(f.read())
except Exception:
pass
with open(POLICY_PATH, "w", encoding="utf-8") as f:
yaml.safe_dump(policy, f, sort_keys=False, allow_unicode=True)




def ensure_taxonomy(policy: Dict) -> Dict:
if "taxonomy" not in policy or not isinstance(policy["taxonomy"], dict):
policy["taxonomy"] = {}
return policy




def merge_list(dst: List[str], src: List[str]) -> List[str]:
seen = {s.strip().lower(): s for s in dst if isinstance(s, str)}
for s in src:
if not isinstance(s, str):
continue
k = s.strip().lower()
if k and k not in seen:
seen[k] = s.strip()
# keep original order; append new at end
return [seen[k] for k in seen]




def upsert_class(policy: Dict, key: str, *, description: str|None=None,
must_haves: List[str]|None=None, must_not_haves: List[str]|None=None) -> Dict:
policy = ensure_taxonomy(policy)
entry = policy["taxonomy"].get(key) or {}
if description:
if not entry.get("description"):
entry["description"] = description.strip()
elif len(entry["description"]) < 40:
entry["description"] = description.strip()
if must_haves:
entry["must_haves"] = merge_list(entry.get("must_haves", []), must_haves)
if must_not_haves:
entry["must_not_haves"] = merge_list(entry.get("must_not_haves", []), must_not_haves)
policy["taxonomy"][key] = entry
return policy