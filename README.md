# IncidentOps AI: Agentic Ticket Routing & Privacy Pipeline

IncidentOps AI is a Dockerized enterprise-grade MVP for the hackathon use case: **AI Powered Intelligent Ticket Routing & Resolution Agent**. It classifies incoming IT tickets, routes them to the right support category, retrieves similar historical incidents, suggests resolution steps, tracks confidence, escalates uncertain cases, and protects sensitive data before any LLM call.

The project is intentionally text/log/ticket first. It focuses on fast and reliable routing for ServiceNow-style incidents rather than broad document OCR.

## Problem Statement Coverage

The system directly implements the requested capabilities:

- **Classifies incoming tickets** into IT categories such as Network, Application, Infrastructure, Access Management, Security, Database, and Storage.
- **Routes to the correct department** using category and assignment-group signals.
- **Suggests resolution steps** using retrieval-augmented generation over sanitized historical tickets.
- **Escalates when uncertain** using composite confidence, verifier score, retrieval similarity, and privacy risk.
- **Tracks confidence score** with component-level explainability.
- **Retrieves similar past tickets** from ClickHouse as the RAG knowledge base.
- **Uses an agentic workflow** through LangGraph nodes for privacy, retrieval, triage, generation, verification, and escalation.
- **Bypasses the LLM for repetitive known incidents** through deterministic semantic caching.
- **Explains every routing decision** with matched-ticket evidence, route branch, resolver recommendation, SLA risk, and knowledge coverage.
- **Detects operational gaps** such as missing runbooks, high SLA risk, resolver saturation, and reviewer correction trends.
- **Protects privacy** by redacting sensitive values before AI exposure.

## Architecture

```text
Raw ticket/log/API input
        |
        v
Go Privacy Shield
- validation
- deterministic redaction
- audit metadata
        |
        v
Redpanda event stream
        |
        v
ClickHouse
- sanitized ticket corpus
- routing decisions
- privacy audit records
        |
        v
Python FastAPI + LangGraph Agent
- ClickHouse vector retrieval
- operational assessment
- semantic cache fast path
- triage
- NVIDIA LLM generation
- verifier
- escalation
        |
        v
Web Operations Console
```

## Services

| Service | Tech | Purpose |
| --- | --- | --- |
| `review-ui` | Nginx + HTML/CSS/JS | Multi-page frontend operations console |
| `api-gateway` | Python FastAPI | Public API, dashboard data, routing workflow |
| `privacy-shield` | Go | High-speed redaction, hashing, audit metadata, Redpanda publishing |
| `redpanda` | Kafka-compatible streaming | Event backbone for sanitized tickets and audit events |
| `clickhouse` | ClickHouse | Ticket store, RAG corpus, audit records, analytics |
| `prometheus` | Prometheus | Metrics scraping |
| `grafana` | Grafana | Observability dashboard shell |

## Frontend Pages

The frontend is available at:

[http://localhost:8081](http://localhost:8081)

Pages:

- **Dashboard**: live ticket totals, routing decisions, privacy findings, category load, assignment groups, and recent tickets.
- **Intelligence**: routing quality, SLA risk queue, knowledge-gap clusters, resolver capacity, human feedback loop, and governance controls.
- **Ticket Stream**: server-sent event feed of live backend tickets from ClickHouse.
- **Search**: full ticket search with category, source, status, route, urgency, and impact filters; rows open full ticket details.
- **Routing Desk**: submit or stage a ticket, run classification/RAG/resolution/verification, and inspect the agent decision.
- **Escalations**: human-review queue for uncertain routing decisions.
- **Privacy Audit**: recent redaction findings with entity type, placeholder, confidence, policy, and detector version.
- **Knowledge Base**: sanitized RAG corpus from the Hugging Face dataset and routed API tickets.
- **Evaluation**: live quality signals such as routed count, average confidence, retrieval similarity, verifier score, escalation rate, and latency.

No ticket rows in the UI are hardcoded. Pages call the running backend APIs.

## Dataset

The project uses:

`6StringNinja/synthetic-servicenow-incidents`

Source:

[https://huggingface.co/datasets/6StringNinja/synthetic-servicenow-incidents](https://huggingface.co/datasets/6StringNinja/synthetic-servicenow-incidents)

Expected columns:

- `number`
- `short_description`
- `description`
- `urgency`
- `impact`
- `category`
- `assignment_group`
- `resolution`

The seeder downloads rows from the Hugging Face Dataset Viewer API, sanitizes ticket text locally, creates semantic embeddings with `sentence-transformers/all-MiniLM-L6-v2`, and inserts records into ClickHouse.

## Agent Workflow

The Python agent uses LangGraph with these nodes:

1. **Privacy Node**
   - Calls the Go `privacy-shield` service.
   - Redacts sensitive values such as emails, IPs, credentials, tokens, account IDs, private-key markers, and phone numbers.
   - Falls back to Python redaction if the Go service is unavailable.

2. **Retrieval Node**
   - Embeds sanitized ticket text.
   - Retrieves similar sanitized tickets from ClickHouse using native cosine similarity.
   - Excludes unreviewed `Pending Review` API submissions from routing thresholds so repeated bad inputs cannot poison retrieval.

3. **Assessment Node**
   - Runs before the expensive LLM path.
   - Computes knowledge-gap signals, resolver recommendation, SLA risk, and route explanation metadata.
   - Lets LangGraph branch early into semantic cache, policy escalation, OOD escalation, or generative RAG.

4. **Fast-Path Node**
   - Runs when the nearest approved historical ticket has similarity greater than or equal to `FAST_PATH_SIMILARITY_THRESHOLD`.
   - Bypasses the NVIDIA LLM and verifier model call.
   - Returns the matched ticket's approved resolution verbatim as a semantic cache hit.

5. **Triage Node**
   - Assigns category using retrieved historical incidents and keyword fallback.

6. **Generation Node**
   - Uses the configured NVIDIA-hosted LLM to draft resolution steps.
   - Falls back to grounded RAG-derived resolution steps if the model call times out or fails.

7. **Verifier Node**
   - Uses the LLM to judge grounding and completeness.
   - Falls back to heuristic scoring when model latency or errors would block the workflow.

8. **Escalation Node**
   - Escalates tickets when confidence is below threshold, retrieval similarity is weak, or verifier score is low.

## Semantic Caching and Fast-Path Routing

The routing agent uses strict similarity thresholds before deciding whether to spend LLM compute:

| Path | Similarity | Behavior |
| --- | --- | --- |
| Semantic cache fast path | `>= 0.95` | Return the approved historical resolution immediately and set `semantic_cache_hit=true` |
| Generative RAG path | `>= 0.70` and `< 0.95` | Send retrieved context to NVIDIA NIM generation, then verifier scoring |
| Out-of-distribution path | `< 0.70` | Halt AI generation and route to human escalation |

This keeps common Tier-1 tickets deterministic, cheap, and fast while still allowing the agentic workflow to handle nuanced incidents. In local Docker verification, a warmed semantic cache route matched `INC00491`, bypassed the LLM, and completed in tens of milliseconds; the first route after API startup is slower because the local embedding model has to warm up.

## Routing Intelligence

The current build includes a production-shaped intelligence layer on top of the routing engine:

- **SLA risk scoring** combines urgency, impact, confidence, retrieval gap, verifier gap, and privacy risk.
- **Resolver recommendation** derives the most likely owner group from retrieval consensus and fallback rules.
- **Knowledge-gap detection** flags weak coverage when retrieval or verifier signals suggest missing runbooks.
- **Route explainability** stores the privacy gate, nearest approved ticket, route branch, resolver signal, SLA risk, and knowledge coverage for every decision.
- **Human feedback persistence** writes reviewer corrections into ClickHouse as immutable `review_events` for later analytics and active-learning workflows.

The **Intelligence** page surfaces these signals live from the backend with no hardcoded UI data.

## Confidence Scoring

The final confidence score is composite:

```text
0.35 * classification confidence
+ 0.25 * retrieval similarity
+ 0.30 * verifier score
+ 0.10 * privacy safety score
```

Escalation occurs when:

- composite confidence is below `ROUTING_CONFIDENCE_THRESHOLD`
- retrieval similarity is too low
- verifier score is too low
- model fallback indicates uncertainty

## Privacy Design

Sensitive data is redacted before AI exposure.

Examples:

```text
jane@example.com -> [EMAIL_1]
10.0.4.15 -> [IP_ADDRESS_1]
password=TempPass123 -> [CREDENTIAL_1]
```

The audit trail stores:

- `audit_id`
- `stream_id`
- `ticket_id`
- raw and sanitized SHA-256 hashes
- detector version
- policy version
- entity type
- placeholder
- confidence
- source offsets

The LLM receives sanitized ticket text and sanitized retrieved context only.

## NVIDIA LLM

Configured model:

```text
meta/llama-3.3-70b-instruct
```

The API key is read from:

```text
NVIDIA_API_KEY
```

The project keeps `.env` ignored by Git. Do not commit real API keys.

The project defaults to a faster 70B NVIDIA model for live demos. The larger `mistralai/mistral-large-3-675b-instruct-2512` model can still be used for accuracy-mode experiments by changing `NVIDIA_LLM_MODEL`.

Because hosted model latency can vary, the code uses:

- bounded model timeout
- disabled client retries
- fallback generation
- fallback verification
- escalation on uncertainty

This keeps the pipeline reliable during demos.

## API Endpoints

Base API:

[http://localhost:8000](http://localhost:8000)

Important routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/v1/health` | Service, ClickHouse, and model configuration health |
| `GET` | `/v1/dashboard` | Live dashboard metrics |
| `GET` | `/v1/tickets/recent` | Recent backend tickets |
| `GET` | `/v1/tickets/stream` | SSE ticket stream |
| `GET` | `/v1/tickets/search` | Search tickets with advanced filters |
| `GET` | `/v1/tickets/detail/{ticket_id}` | Full ticket detail, resolution status, matched ticket, and privacy audit |
| `GET` | `/v1/intelligence/routing` | Route quality, SLA risk, knowledge gaps, resolver capacity, feedback, and governance |
| `POST` | `/v1/tickets/route` | Run the LangGraph routing workflow |
| `GET` | `/v1/tickets/status/{ticket_id}` | Latest routing decision for a ticket |
| `GET` | `/v1/escalations` | Human-review queue |
| `GET` | `/v1/privacy/audit/recent` | Recent redaction findings |
| `GET` | `/v1/privacy/audit/{stream_id}` | Audit trail for one stream |
| `GET` | `/v1/knowledge` | Sanitized RAG corpus |
| `GET` | `/v1/evaluation` | Live evaluation metrics |
| `POST` | `/v1/review/escalations` | Persist reviewer decision, correction, or override feedback |
| `GET` | `/v1/review/events` | Review feedback history and correction-rate metrics |

Routing responses include the deterministic routing branch:

```json
{
  "route_path": "semantic_cache | generative_rag | out_of_distribution",
  "semantic_cache_hit": true,
  "matched_ticket_id": "INC00491",
  "routing_latency_ms": 56,
  "sla_risk": { "score": 0.15, "level": "normal" },
  "knowledge_gap": { "is_gap": false, "reason": "approved historical context is sufficient for this route" },
  "resolver_recommendation": { "group": "IT Support", "confidence": 1.0, "source": "retrieval_consensus" },
  "route_explanation": [
    { "label": "Nearest approved ticket", "value": "INC00491 at 1.00", "impact": "semantic cache branch selected" }
  ]
}
```

Go privacy service:

[http://localhost:8080](http://localhost:8080)

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Privacy service health |
| `GET` | `/metrics` | Prometheus-style metrics |
| `POST` | `/v1/ingest/stream` | Redact and publish ticket/log input |

## Quick Start

### 1. Prepare environment

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set:

```env
NVIDIA_API_KEY=your_key_here
```

### 2. Start Docker stack

```powershell
docker compose up -d --build
```

### 3. Seed the ServiceNow-style dataset

```powershell
docker compose run --rm api-gateway python -m app.scripts.seed_hf_dataset --limit 500
```

### 4. Open the frontend

[http://localhost:8081](http://localhost:8081)

### 5. Check API health

```powershell
Invoke-RestMethod -Uri http://localhost:8000/v1/health
```

## Sample Ticket Routing Call

```powershell
$body = @{
  short_description = "VPN login fails for multiple users"
  description = "Users report MFA succeeds but the VPN tunnel never establishes from office network 10.0.4.15. Contact jane@example.com saw password=TempPass123 in a pasted diagnostic."
  urgency = 2
  impact = 2
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/tickets/route `
  -ContentType "application/json" `
  -Body $body
```

Expected behavior:

- IP, email, and credential are redacted.
- Ticket is categorized.
- Similar historical tickets are retrieved.
- Known incidents return the matched approved resolution through the semantic cache fast path.
- Similar but not identical incidents use generative RAG and verifier scoring.
- Out-of-distribution tickets escalate without model generation.
- Confidence components are returned.
- Ticket escalates if confidence is low.

## Development Commands

Build services:

```powershell
docker compose build
```

Restart API and UI:

```powershell
docker compose up -d api-gateway review-ui
```

View logs:

```powershell
docker compose logs -f api-gateway
docker compose logs -f privacy-shield
```

Run Go tests inside Docker:

```powershell
docker run --rm -v "${PWD}\services\privacy-shield:/src" -w /src golang:1.23-alpine sh -c "go test ./..."
```

Compile Python app:

```powershell
python -m compileall services\agent-api\app
```

Query ClickHouse:

```powershell
docker compose exec -T clickhouse clickhouse-client --password clickhouse --query "SELECT count() FROM incident_ai.tickets"
```

## LLM Routing Benchmark

The repo includes a deterministic 100-ticket benchmark generator for new merchant-onboarding incidents. The generated set follows the current ServiceNow-style schema and uses this split:

- 40 easy tickets
- 20 medium tickets
- 40 hard/severe tickets

Generate the benchmark:

```powershell
python services\agent-api\app\scripts\generate_eval_ticket_set.py --output data\eval_ticket_set_100.json
```

Evaluate the live backend:

```powershell
python services\agent-api\app\scripts\evaluate_ticket_set.py --input data\eval_ticket_set_100.json --output output\evaluation\full_100_ticket_results.json
```

The evaluator posts each ticket to `/v1/tickets/route` and compares the backend decision against the expected `auto_resolution` or `human_review` label. The latest local Docker run scored `100/100` decision accuracy: 40/40 easy, 20/20 medium, and 40/40 hard.

## Repository Layout

```text
.
+-- DESIGN.md
+-- README.md
+-- docker-compose.yml
+-- infra/
|   +-- clickhouse/init/001_schema.sql
|   +-- prometheus/prometheus.yml
+-- services/
    +-- agent-api/
    |   +-- app/
    |   |   +-- agent.py
    |   |   +-- clickhouse_repo.py
    |   |   +-- config.py
    |   |   +-- embeddings.py
    |   |   +-- llm.py
    |   |   +-- main.py
    |   |   +-- privacy.py
    |   |   +-- scripts/generate_eval_ticket_set.py
    |   |   +-- scripts/evaluate_ticket_set.py
    |   |   +-- scripts/seed_hf_dataset.py
    |   +-- Dockerfile
    +-- privacy-shield/
    |   +-- cmd/privacy-shield/main.go
    |   +-- Dockerfile
    +-- review-ui/
        +-- index.html
        +-- nginx.conf
        +-- Dockerfile
```

## Demo Flow

1. Open [http://localhost:8081](http://localhost:8081).
2. Start on **Dashboard** to show live ticket volume, category load, privacy findings, and routing decisions.
3. Open **Ticket Stream** to show tickets streaming from the live backend.
4. Stage a streamed ticket into **Routing Desk**.
5. Route it and explain the agent nodes: privacy, retrieval, triage, generation, verifier, escalation.
6. Open **Privacy Audit** to show redaction evidence.
7. Open **Knowledge Base** to show sanitized historical incidents used for RAG.
8. Open **Intelligence** to show SLA risk, route-quality trends, resolver saturation, knowledge gaps, and feedback-loop governance.
9. Open **Evaluation** to show measurable confidence/retrieval/escalation signals.

## Current Limitations

- The MVP is text-first and does not process PDF/OCR/layout documents.
- Semantic embeddings use `sentence-transformers/all-MiniLM-L6-v2` locally on CPU. For production scale, run embeddings as a separate model service.
- ClickHouse performs native cosine-similarity retrieval, but the MVP does not yet define a production HNSW/IVF vector index.
- Hosted LLM latency can vary, so the system uses timeout-based fallback and escalation.
- Reviewer feedback is persisted, but the MVP does not yet retrain or re-index from reviewer corrections automatically.
- Resolver capacity is inferred from live routing behavior; it is not yet integrated with real workforce-management or on-call systems.
- SLA risk is an explainable composite heuristic today, not a historically calibrated incident-severity model.

## Production Roadmap

- Move the local semantic embedding model into a dedicated embedding service.
- Add ClickHouse HNSW vector index queries.
- Add active-learning jobs that convert reviewer corrections into approved training labels and updated KB entries.
- Add OpenTelemetry traces across Go, Redpanda, ClickHouse, and Python.
- Add authentication, RBAC, and tenant isolation.
- Add ServiceNow/Jira/Zendesk connectors.
- Add model evaluation harness for F1, retrieval relevance, answer grounding, and reviewer override rate.
- Add PDF/OCR ingestion only after the text-ticket workflow is stable.

## Security Notes

- Never commit `.env` or real API keys.
- Rotate any API key shared outside a secret manager.
- Only sanitized text should be sent to external LLM endpoints.
- Raw payload hashes are stored for auditability without storing raw sensitive content by default.
