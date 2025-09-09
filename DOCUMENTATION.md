# 📖 Email Triage System – Documentation

## 1. Overview

The **Email Triage System** automatically ingests, classifies, and acts on incoming emails. It uses:

- **FastAPI** (Python) → REST API for ingesting emails and serving results  
- **OpenAI GPT-5** → Agents for triage, action, and escalation  
- **Supabase (Postgres)** → Persistent storage and audit logging  
- **n8n / Power Automate** → Workflow automation for actions  
- **Docker** → Containerized deployment  
- **Render + GitHub** → CI/CD production hosting  

The system has **three agents**:

1. **Triage Agent** → Classifies emails into taxonomy  
2. **Action Agent** → Maps classifications to actions (forward, move, Jira, etc.)  
3. **Escalation Agent** → Handles low-confidence/disagreements  

---

## 2. System Architecture

Incoming Email (via n8n webhook)
↓
[ Ingest API ] (FastAPI /ingest)
↓
Normalize + Extract PDF text
↓
Triage Agent (classification)
↓
Action Agent (map rules → actions)
↓
┌─────────────┐
│ Confidence? │
└──────┬──────┘
│
High │ Low / Disagree
│
Execute Actions Escalation Agent → Power Automate
↓
Log results in Supabase

yaml
Copy code

---

## 3. Data Flow

1. **Email arrives** → Posted by n8n to `/ingest`  
2. **Normalization** → Payload standardized into `EmailPayload`  
3. **Triage** → `run_triage()` classifies email using `email_policy.yaml`  
4. **Action Agent** → Maps classification → actions from `actions.yaml`  
5. **Execution or Escalation**  
   - Confident → `execute_actions()` → n8n webhooks  
   - Else → `run_escalation_agent()` → Power Automate  
6. **Logging** → Every stage written to Supabase  

---

## 4. Rules & Policies

### Email Policy (`rules/email_policy.yaml`)
- Defines **taxonomy** and keywords for classification.

### Action Rules (`rules/actions.yaml`)
- Maps classifications → actions.  
- Actions support placeholders for env vars and email fields.

---

## 5. Database Tables

### `email_logs` → Email intake
Stores original email + current processing status.

### `email_decisions` → Agent results
Logs triage, action, escalation decisions.

### `action_runs` → Action execution
Tracks each webhook execution with request/response payloads.

---

## 6. Agents

- **Triage Agent** → classifies email into taxonomy.  
- **Action Agent** → maps classification → list of actions (move, forward, Jira, etc.).  
- **Escalation Agent** → proposes classification when confidence is low and sends payloads to Power Automate for human review.  

---

## 7. Example Flow

📧 *"Invoice #11873 due Sept 30"*  

1. Logged → `email_logs`  
2. Triage → `invoice.unpaid` (confidence 0.92)  
3. Action Agent → actions:  
   - Forward to Xero inbox  
   - Move to AP folder  
   - Create Jira ticket  
4. Executed via n8n → logged in `action_runs`  
5. Final status → `executed` in `email_logs`  

---

## 8. Deployment

### Local Development
Run with Docker Compose:

```bash
docker compose up --build
API will be available at http://localhost:8000.
The .env file contains secrets (ignored by Git).

Production (Render + GitHub)
This repository is connected to Render for automated deployment.

Any push to the main branch in GitHub triggers Render to:

Pull the latest code

Build the Docker image defined in the Dockerfile

Apply environment variables from Render Dashboard

Start the FastAPI service

Secrets (OPENAI_API_KEY, SUPABASE_URL, SUPABASE_KEY, N8N_BASE_URL, POWER_AUTOMATE_URL, etc.) are managed in Render → Environment Variables.

Render monitors health at / and automatically rolls back if a deployment fails.

9. Reporting
Audit trail for an email:

sql
Copy code
SELECT 
    el.subject,
    el.from_email,
    el.status,
    td.classification AS triage_class,
    ad.classification AS action_class,
    ar.action,
    ar.response_status
FROM email_logs el
LEFT JOIN email_decisions td 
       ON el.email_id = td.email_id AND td.stage = 'triage'
LEFT JOIN email_decisions ad 
       ON el.email_id = ad.email_id AND ad.stage = 'action'
LEFT JOIN action_runs ar     
       ON el.email_id = ar.email_id
ORDER BY el.created_at DESC;
