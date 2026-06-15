# IncidentOps AI Product User Guide

## Product Overview

IncidentOps AI is an intelligent incident-routing and resolution assistant for IT operations teams. It classifies incoming tickets, protects sensitive data before model calls, retrieves similar historical incidents, generates resolution guidance, and escalates uncertain decisions for human review.

## Key Capabilities

- Route new tickets by category, assignment group, urgency, impact, source, and evidence quality.
- Detect and redact sensitive entities before downstream AI processing.
- Use semantic cache and retrieval-augmented generation for fast, explainable routing.
- Track tickets that need human review when confidence or evidence is weak.
- Search routed tickets and inspect decision evidence.
- Review privacy audit logs and sanitized knowledge-base records.
- Monitor route quality, resolver capacity, knowledge gaps, and model quality.

## Running The Product

1. Install Docker Desktop and ensure Docker is running.
2. Copy the sample environment file:

```powershell
Copy-Item .env.example .env
```

3. Add required API keys and configuration values to `.env`.
4. Start the full stack:

```powershell
docker compose up -d --build
```

5. Open the frontend:

```text
http://localhost:8081/dashboard.html
```

6. Confirm the API is running:

```text
http://localhost:8000/docs
```

## Main Screens

### Dashboard

Use the Dashboard as the operations command center. It shows routed ticket counts, escalation rate, confidence, average response time, tickets needing attention, category load, AI impact, knowledge coverage, latest AI decision, and recent tickets.

### Routing Desk

Use Routing Desk to submit a ticket manually. Enter a short description, optional full description, urgency, impact, source, and optional ticket number, then run the routing engine. The result shows category, assignment group, route path, confidence, evidence, and resolution plan.

### Ticket Stream

Use Ticket Stream to monitor incoming tickets and live updates. Filter by category, source, urgency, and status. Click a row to inspect ticket details.

### Search

Use Search to find routed tickets by description, category, assignment group, source, route path, urgency, and result limit. Selecting a row opens the evidence drawer.

### Human Review

Use Human Review to approve, reject, or correct AI routing decisions that require human validation. Reviewer actions are saved as feedback events.

### Privacy Audit

Use Privacy Audit to review recent redaction findings, confidence scores, policy versions, detector versions, and entity placeholders.

### Knowledge Base

Use Knowledge Base to inspect sanitized historical tickets and routed API tickets used for retrieval and resolution suggestions.

### Intelligence

Use Intelligence to inspect routing quality, resolver capacity, knowledge gaps, reviewer feedback, and model quality metrics.

## Typical User Workflow

1. Start at Dashboard to check system status and ticket load.
2. Submit a ticket from Routing Desk or send a ticket to the API.
3. Review the routing decision and resolution plan.
4. Open Search to locate the routed ticket and inspect evidence.
5. Check Privacy Audit if the ticket includes sensitive information.
6. Use Human Review if the ticket was escalated.
7. Review Intelligence to understand route quality and knowledge gaps.

## API Entry Points

- Frontend: `http://localhost:8081/dashboard.html`
- API docs: `http://localhost:8000/docs`
- Route a ticket: `POST /v1/tickets/route`
- Search tickets: `GET /v1/tickets/search`
- Human review queue: `GET /v1/escalations`
- Privacy audit: `GET /v1/privacy/audit/recent`
- Knowledge base: `GET /v1/knowledge`
- Routing intelligence: `GET /v1/intelligence/routing`

## Troubleshooting

- If the frontend does not load, check `docker compose ps review-ui`.
- If API calls fail, check `docker compose ps api-gateway` and open `http://localhost:8000/docs`.
- If routing fails, confirm `.env` contains the required AI provider key and model settings.
- If data is empty, seed or route tickets first, then refresh the frontend.
- Never commit `.env`; use `.env.example` for safe configuration sharing.

