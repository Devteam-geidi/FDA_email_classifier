import os, httpx, logging

log = logging.getLogger(__name__)

def dispatch_policy_workflow():
    """
    Trigger the GitHub Actions workflow .github/workflows/policy-pr.yml on 'main'.
    Requires env: GITHUB_REPOSITORY and GITHUB_TOKEN (PAT with Actions: Read & write).
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    tok  = os.environ.get("GITHUB_TOKEN")

    if not repo or not tok:
        log.error("Dispatch skipped: missing env (%s, %s)",
                  "GITHUB_REPOSITORY=OK" if repo else "GITHUB_REPOSITORY=MISSING",
                  "GITHUB_TOKEN=OK" if tok else "GITHUB_TOKEN=MISSING")
        return

    url = f"https://api.github.com/repos/{repo}/actions/workflows/policy-pr.yml/dispatches"
    payload = {"ref": "main"}
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    log.info("Dispatching GitHub workflow to %s on ref=%s ...", repo, payload["ref"])
    with httpx.Client(timeout=20) as client:
        r = client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            log.error("FAILED dispatch: %s %s | body=%s", r.status_code, r.reason_phrase, r.text)
            r.raise_for_status()

    log.info("✅ Dispatched GitHub workflow policy-pr.yml on main.")
