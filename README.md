# Email Triage System

CrewAI-driven email triage system with FastAPI, Supabase logging, n8n actions, and Power Automate human review.

## Features
- **Triage Agent**: classifies incoming emails (invoice, task, spam, etc.).
- **Action Agent**: verifies classification, executes actions via n8n webhooks.
- **Escalation Agent**: escalates low-confidence/disagreements to Power Automate.
- **Feedback Loop**: human decisions applied back to system with `/feedback`.
- **Supabase**: all classifications, actions, and feedback are logged.

## Quickstart

### 1. Clone repo & install deps
```bash
git clone <repo-url>
cd email-triage-system
```

### 2. Configure environment
```bash
cp .env.example .env
# edit .env with your Supabase URL/KEY, OpenAI API key, n8n base, Power Automate URL
```

### 3. Run locally (no Docker)
```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Run with Docker Compose
```bash
docker compose up --build
```

API will be available at `http://localhost:8000`.

### 5. Expose to internet for webhook testing
```bash
ngrok http 8000
```
Copy the HTTPS URL (e.g. `https://abc123.ngrok.io`) and use in n8n / Power Automate webhook configs.

---

## API Endpoints

### Healthcheck
```bash
GET /
```
Response:
```json
{"status": "ok"}
```

### Ingest Email
```bash
POST /ingest
Content-Type: application/json
```
Example payload:
```json
{
  "message_id": "<CAE4...>",
  "internet_message_id": "<CAE4...>",
  "subject": "Invoice #11873 – OVERDUE",
  "from_": {"name": "Alice Doe", "email": "alice@vendor.com"},
  "to": [{"name": "AP", "email": "ap@company.com"}],
  "body_text": "Hi, invoice attached...",
  "attachments": [
    {"filename": "INV-11873.pdf", "content_type": "application/pdf", "download_url": "https://..."}
  ],
  "headers": {}
}
```

### Feedback (human review)
```bash
POST /feedback
Content-Type: application/json
```
Example payload:
```json
{
  "nhr_token": "NHR_9b1b2c...",
  "human": {"email": "ops.manager@company.com", "comment": "This is overdue."},
  "final_classification": "invoice.overdue",
  "actions": [
    {"action": "move", "params": {"folder": "AP/Overdue"}},
    {"action": "create_jira", "params": {"project": "AP", "summary": "Pay vendor invoice #11873"}}
  ]
}
```

---

## File Structure
```
src/app/main.py          # FastAPI app
src/app/agents/triage.py # Triage Agent
src/app/agents/action.py # Action Agent
src/app/agents/escalation.py # Escalation Agent
rules/email_policy.yaml  # Classification taxonomy & action rules
.env.example             # Env vars template
Dockerfile               # Container build
docker-compose.yml       # Local orchestration
```

---

## Deployment
Local Development

Run with hot reload:

uvicorn app.main:app --reload --port 8000


Or with Docker:

docker compose up --build


Expose for webhook testing:

ngrok http 8000

Production (Render + GitHub Auto-Deploy)

Repo is connected to Render
.

Every push to main (or the tracked branch) triggers:

Render fetches the latest code from GitHub.

Build step runs (pyproject.toml / Dockerfile).

Service container is deployed.

Health check (GET /) ensures it’s running before going live.

Environment variables (.env) are not in GitHub.
Configure them via the Render Dashboard → Environment:

OPENAI_API_KEY

SUPABASE_URL

SUPABASE_KEY

POWER_AUTOMATE_URL

N8N_BASE_URL

…and other service-specific keys.

Render keeps a deploy history, so you can roll back to previous versions.

Workflow:

# develop locally
git checkout -b feature/fix-escalation
# commit + test
git commit -m "Fix escalation agent guideline options"
git push origin feature/fix-escalation
# open PR -> merge into main
# Render auto-builds & deploys to production
