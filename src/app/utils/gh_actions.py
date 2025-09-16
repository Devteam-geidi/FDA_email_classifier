import os
import httpx
import logging

log = logging.getLogger(__name__)

def dispatch_policy_workflow():
    """
    Trigger the GitHub Actions workflow .github/workflows/policy-pr.yml on 'main'.
    Requires env: GITHUB_REPOSITORY (owner/repo) and GITHUB_TOKEN (PAT with Actions write).
    """
    repo = os.environ["GITHUB_REPOSITORY"]
    tok  = os.environ["GITHUB_TOKEN"]
    url  = f"https://api.github.com/repos/{repo}/actions/workflows/policy-pr.yml/dispatches"

    payload = {"ref": "main"}
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    with httpx.Client(timeout=20) as client:
        r = client.post(url, headers=headers, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            log.error(
                "Failed to dispatch GitHub workflow: %s %s | body=%s",
                e.response.status_code, e.response.reason_phrase, e.response.text
            )
            raise
    log.info("Dispatched GitHub workflow policy-pr.yml on main.")
