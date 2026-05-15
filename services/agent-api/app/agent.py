from __future__ import annotations

import time
import logging
from collections import Counter
from typing import TypedDict

import httpx
from langgraph.graph import END, StateGraph

from app.clickhouse_repo import ClickHouseRepository, RetrievedTicket, TicketRecord
from app.config import Settings
from app.embeddings import embed_text
from app.llm import NvidiaLLM
from app.privacy import redact_text
from app.schemas import TicketRequest

LOGGER = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    request: TicketRequest
    ticket_id: str
    raw_text: str
    sanitized_text: str
    redacted_count: int
    privacy_audit: dict
    retrieved: list[RetrievedTicket]
    fast_path_match: RetrievedTicket | None
    rag_evidence: dict
    assigned_category: str
    classification_confidence: float
    retrieval_similarity: float
    suggested_resolution: list[str]
    verifier_score: float
    confidence_score: float
    privacy_risk: float
    escalation_required: bool
    routing_latency_ms: int
    agent_state: str
    route_path: str
    semantic_cache_hit: bool
    matched_ticket_id: str | None
    review_reason: str | None
    sla_risk_score: float
    sla_risk_level: str
    knowledge_gap: dict
    resolver_recommendation: dict
    route_explanation: list[dict[str, str]]
    escalation_rationale: str


class RoutingAgent:
    def __init__(self, settings: Settings, repo: ClickHouseRepository, llm: NvidiaLLM):
        self.settings = settings
        self.repo = repo
        self.llm = llm
        self.workflow = self._build_workflow()

    async def route(self, request: TicketRequest) -> dict:
        started = time.perf_counter()
        raw_text = f"{request.short_description}\n\n{request.description}"
        initial_state: AgentState = {
            "request": request,
            "raw_text": raw_text,
            "agent_state": "started",
        }
        state = await self._run_workflow(initial_state)
        state["routing_latency_ms"] = int((time.perf_counter() - started) * 1000)
        response = self._to_response(state)
        self._store_ticket(request, state)
        self.repo.insert_routing_decision(response | {"model_name": self.settings.nvidia_llm_model})
        return response

    async def _run_workflow(self, state: AgentState) -> AgentState:
        return await self.workflow.ainvoke(state)

    def _build_workflow(self):
        graph = StateGraph(AgentState)
        graph.add_node("privacy", self._privacy_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("evidence", self._evidence_node)
        graph.add_node("assess", self._assessment_node)
        graph.add_node("semantic_cache", self._fast_path_node)
        graph.add_node("triage", self._triage_node)
        graph.add_node("llm_resolution", self._llm_resolution_node)
        graph.add_node("escalate", self._escalate_node)

        graph.set_entry_point("privacy")
        graph.add_edge("privacy", "retrieve")
        graph.add_edge("retrieve", "evidence")
        graph.add_edge("evidence", "assess")
        graph.add_conditional_edges(
            "assess",
            self._select_after_assessment,
            {
                "semantic_cache": "semantic_cache",
                "llm_resolution": "triage",
            },
        )
        graph.add_edge("semantic_cache", END)
        graph.add_edge("triage", "llm_resolution")
        graph.add_conditional_edges(
            "llm_resolution",
            self._should_escalate,
            {
                "escalate": "escalate",
                "complete": END,
            },
        )
        graph.add_edge("escalate", END)
        return graph.compile()

    async def _privacy_node(self, state: AgentState) -> AgentState:
        request = state["request"]
        raw_text = state["raw_text"]
        payload = request.model_dump()
        payload["raw_text"] = raw_text
        payload["bypass_redpanda"] = True
        payload["return_sanitized"] = True
        try:
            timeout = httpx.Timeout(self.settings.privacy_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self.settings.privacy_shield_url}/v1/ingest/stream", json=payload)
                resp.raise_for_status()
                data = resp.json()
                findings = data.get("findings") or []
                state["ticket_id"] = data["ticket_id"]
                state["sanitized_text"] = data["sanitized_text"]
                state["redacted_count"] = len(findings)
                state["privacy_audit"] = {
                    "stream_id": data["stream_id"],
                    "ticket_id": data["ticket_id"],
                    "raw_sha256": data["raw_sha256"],
                    "sanitized_sha256": data["sanitized_sha256"],
                    "findings": findings,
                    "detector_version": data["detector_version"],
                    "policy_version": data["policy_version"],
                }
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            LOGGER.warning("privacy service unavailable; using local redaction fallback: %s", exc.__class__.__name__)
            fallback = redact_text(raw_text)
            state["ticket_id"] = request.ticket_id or request.number or fallback.sanitized_sha256[:16]
            state["sanitized_text"] = fallback.sanitized_text
            state["redacted_count"] = len(fallback.findings)
            state["privacy_audit"] = {
                "stream_id": f"sync_{fallback.raw_sha256[:16]}",
                "ticket_id": state["ticket_id"],
                "raw_sha256": fallback.raw_sha256,
                "sanitized_sha256": fallback.sanitized_sha256,
                "findings": [
                    {
                        "entity_type": item.entity_type,
                        "placeholder": item.placeholder,
                        "confidence": item.confidence,
                        "start_offset": item.start_offset,
                        "end_offset": item.end_offset,
                    }
                    for item in fallback.findings
                ],
                "detector_version": "python-regex-v1",
                "policy_version": "enterprise-ticket-policy-v1",
            }
        state["agent_state"] = "privacy_complete"
        return state

    async def _retrieve_node(self, state: AgentState) -> AgentState:
        embedding = embed_text(state["sanitized_text"], self.settings.embedding_dim)
        retrieved = self.repo.find_similar(embedding, self.settings.rag_top_k)
        fast_path_match = next(
            (
                ticket
                for ticket in retrieved
                if ticket.similarity >= self.settings.fast_path_similarity_threshold
                and self._is_fast_path_eligible(ticket)
            ),
            None,
        )
        state["retrieved"] = retrieved
        state["fast_path_match"] = fast_path_match
        state["retrieval_similarity"] = round(retrieved[0].similarity, 4) if retrieved else 0.0
        state["matched_ticket_id"] = (
            fast_path_match.ticket_id
            if fast_path_match
            else retrieved[0].ticket_id if retrieved else None
        )
        state["route_path"] = "retrieved"
        state["semantic_cache_hit"] = False
        state["agent_state"] = "retrieval_complete"
        return state

    async def _evidence_node(self, state: AgentState) -> AgentState:
        state["rag_evidence"] = self._build_rag_evidence(state)
        state["agent_state"] = "evidence_complete"
        return state

    async def _assessment_node(self, state: AgentState) -> AgentState:
        review_reason = self._human_review_reason(state)
        if review_reason:
            state["review_reason"] = review_reason
        state["knowledge_gap"] = self._knowledge_gap_signal(state)
        state["resolver_recommendation"] = self._resolver_recommendation(state)
        self._refresh_operational_metadata(state)
        state["agent_state"] = "assessment_complete"
        return state

    async def _fast_path_node(self, state: AgentState) -> AgentState:
        matched = state.get("fast_path_match") or state.get("retrieved", [])[0]
        privacy_risk = self._privacy_risk(state)
        state["assigned_category"] = normalize_category(matched.category)
        state["classification_confidence"] = 1.0
        state["verifier_score"] = 1.0
        state["privacy_risk"] = privacy_risk
        state["confidence_score"] = round(max(0.0, min(1.0, 0.99 - privacy_risk * 0.10)), 4)
        state["suggested_resolution"] = [matched.resolution]
        state["escalation_required"] = False
        state["route_path"] = "semantic_cache"
        state["semantic_cache_hit"] = True
        state["matched_ticket_id"] = matched.ticket_id
        self._refresh_operational_metadata(state)
        state["agent_state"] = "semantic_cache_hit"
        return state

    async def _triage_node(self, state: AgentState) -> AgentState:
        retrieved = state.get("retrieved", [])
        if retrieved:
            weights: Counter[str] = Counter()
            for item in retrieved:
                weights[item.category] += max(item.similarity, 0.0)
            category, score = weights.most_common(1)[0]
            total = sum(weights.values()) or 1.0
            confidence = max(0.35, min(0.98, score / total))
        else:
            category, confidence = keyword_category(state["sanitized_text"])
        state["assigned_category"] = normalize_category(category)
        state["classification_confidence"] = round(float(confidence), 4)
        state["route_path"] = "generative_rag"
        state["semantic_cache_hit"] = False
        state["agent_state"] = "triage_complete"
        return state

    async def _llm_resolution_node(self, state: AgentState) -> AgentState:
        request = state["request"]
        decision = self.llm.resolve_and_decide(
            ticket_text=state["sanitized_text"],
            category=state["assigned_category"],
            retrieved=state.get("retrieved", []),
            rag_evidence=state.get("rag_evidence", {}),
            urgency=request.urgency,
            impact=request.impact,
            policy_signal=state.get("review_reason"),
        )
        privacy_risk = self._privacy_risk(state)
        decision_confidence = round(decision.confidence_score, 4)
        confidence = (
            0.20 * state.get("classification_confidence", 0.0)
            + 0.20 * max(0.0, state.get("retrieval_similarity", 0.0))
            + 0.50 * decision_confidence
            + 0.10 * (1.0 - privacy_risk)
        )
        state["suggested_resolution"] = decision.resolution_steps
        state["escalation_required"] = decision.escalation_required
        state["escalation_rationale"] = decision.rationale
        state["verifier_score"] = decision_confidence
        state["privacy_risk"] = round(privacy_risk, 4)
        state["confidence_score"] = round(max(0.0, min(1.0, confidence)), 4)
        state["route_path"] = "human_review_required" if decision.escalation_required else "generative_rag"
        state["semantic_cache_hit"] = False
        state["knowledge_gap"] = self._knowledge_gap_signal(state)
        self._refresh_operational_metadata(state)
        state["agent_state"] = "llm_decision_complete"
        return state

    async def _escalate_node(self, state: AgentState) -> AgentState:
        state["agent_state"] = "escalated"
        self._refresh_operational_metadata(state)
        return state

    def _select_after_assessment(self, state: AgentState) -> str:
        if state.get("fast_path_match") is not None:
            return "semantic_cache"
        return "llm_resolution"

    def _is_fast_path_eligible(self, ticket: RetrievedTicket) -> bool:
        return (
            ticket.source in self.settings.approved_knowledge_source_values
            and ticket.assignment_group != "Pending Review"
            and bool(ticket.resolution.strip())
        )

    def _should_escalate(self, state: AgentState) -> str:
        return "escalate" if state.get("escalation_required") else "complete"

    def _privacy_risk(self, state: AgentState) -> float:
        return round(min(0.4, state.get("redacted_count", 0) * 0.05), 4)

    def _knowledge_gap_signal(self, state: AgentState) -> dict:
        evidence = state.get("rag_evidence", {})
        quality_band = evidence.get("quality_band")
        similarity = state.get("retrieval_similarity", 0.0)
        verifier = state.get("verifier_score")
        route_path = state.get("route_path", "retrieved")
        if quality_band in {"none", "weak"}:
            severity = "critical" if quality_band == "none" or similarity < 0.50 else "elevated"
            return {
                "is_gap": True,
                "reason": f"RAG evidence quality is {quality_band}; LLM must treat retrieved context as low-confidence support",
                "severity": severity,
            }
        if verifier is not None and verifier < 0.70 and route_path == "generative_rag":
            return {
                "is_gap": True,
                "reason": "LLM decision confidence is below grounding threshold",
                "severity": "elevated",
            }
        return {
            "is_gap": False,
            "reason": "approved historical context has enough quality for LLM-grounded reasoning",
            "severity": "normal",
        }

    def _build_rag_evidence(self, state: AgentState) -> dict:
        retrieved = state.get("retrieved", [])
        fast_threshold = getattr(self.settings, "fast_path_similarity_threshold", 0.95)
        rag_threshold = getattr(self.settings, "rag_similarity_threshold", 0.70)
        if not retrieved:
            return {
                "quality_score": 0.0,
                "quality_band": "none",
                "top_similarity": 0.0,
                "average_similarity": 0.0,
                "category_consensus": 0.0,
                "resolution_coverage": 0.0,
                "evidence_count": 0,
                "dominant_category": None,
                "items": [],
                "policy": "No approved retrieved evidence was available; the LLM must decide from the sanitized ticket and escalate when not grounded.",
            }

        similarities = [max(0.0, min(1.0, item.similarity)) for item in retrieved]
        top_similarity = similarities[0]
        average_similarity = sum(similarities) / len(similarities)
        top3_average = sum(similarities[:3]) / min(len(similarities), 3)
        resolution_count = sum(1 for item in retrieved if item.resolution.strip())
        resolution_coverage = resolution_count / len(retrieved)

        category_weights: Counter[str] = Counter()
        for item in retrieved:
            category_weights[normalize_category(item.category)] += max(item.similarity, 0.0)
        dominant_category, dominant_weight = category_weights.most_common(1)[0]
        total_weight = sum(category_weights.values()) or 1.0
        category_consensus = dominant_weight / total_weight

        quality_score = round(
            max(
                0.0,
                min(
                    1.0,
                    0.45 * top_similarity
                    + 0.20 * top3_average
                    + 0.20 * category_consensus
                    + 0.15 * resolution_coverage,
                ),
            ),
            4,
        )
        if state.get("fast_path_match") is not None and top_similarity >= fast_threshold:
            quality_band = "cache_ready"
        elif top_similarity >= rag_threshold and category_consensus >= 0.45 and resolution_coverage >= 0.80:
            quality_band = "strong"
        elif top_similarity >= 0.50 and resolution_coverage >= 0.50:
            quality_band = "usable"
        else:
            quality_band = "weak"

        items = []
        fast_match = state.get("fast_path_match")
        for item in retrieved:
            is_cache_candidate = (
                fast_match is not None
                and item.ticket_id == fast_match.ticket_id
                and item.similarity >= fast_threshold
            )
            items.append(
                {
                    "ticket_id": item.ticket_id,
                    "category": normalize_category(item.category),
                    "assignment_group": item.assignment_group,
                    "source": item.source,
                    "similarity": round(item.similarity, 4),
                    "evidence_role": "cache_candidate" if is_cache_candidate else "supporting_context",
                    "resolution_present": bool(item.resolution.strip()),
                }
            )

        return {
            "quality_score": quality_score,
            "quality_band": quality_band,
            "top_similarity": round(top_similarity, 4),
            "average_similarity": round(average_similarity, 4),
            "category_consensus": round(category_consensus, 4),
            "resolution_coverage": round(resolution_coverage, 4),
            "evidence_count": len(retrieved),
            "dominant_category": dominant_category,
            "items": items,
            "policy": (
                "Semantic cache bypass is allowed only for an approved, resolved, non-Pending Review ticket at "
                f">={fast_threshold:.2f}; all lower-confidence evidence must be evaluated by the LLM before resolution or escalation."
            ),
        }

    def _resolver_recommendation(self, state: AgentState) -> dict:
        retrieved = state.get("retrieved", [])
        weights: Counter[str] = Counter()
        for item in retrieved:
            if item.assignment_group and item.assignment_group != "Pending Review":
                weights[item.assignment_group] += max(item.similarity, 0.0)
        if weights:
            total = sum(weights.values()) or 1.0
            ranked = weights.most_common(4)
            return {
                "group": ranked[0][0],
                "confidence": round(max(0.0, min(1.0, ranked[0][1] / total)), 4),
                "source": "retrieval_consensus",
                "alternates": [name for name, _ in ranked[1:]],
            }
        fallback = {
            "Network": "Network Ops",
            "Access Management": "IT Support",
            "Security": "Security",
            "Database": "IT Support",
            "Storage": "IT Support",
            "Infrastructure": "IT Support",
            "Application": "IT Support",
        }
        group = fallback.get(state.get("assigned_category", ""), "IT Support")
        return {
            "group": group,
            "confidence": 0.45,
            "source": "category_default",
            "alternates": [],
        }

    def _sla_risk(self, state: AgentState) -> tuple[float, str]:
        request = state["request"]
        priority_risk = ((4 - request.urgency) + (4 - request.impact)) / 6
        confidence = state.get("confidence_score")
        if confidence is None:
            resolver = state.get("resolver_recommendation", {})
            confidence = (
                0.70 * max(0.0, state.get("retrieval_similarity", 0.0))
                + 0.30 * float(resolver.get("confidence", 0.0))
            )
        uncertainty = 1.0 - max(0.0, min(1.0, float(confidence)))
        retrieval_gap = max(0.0, self.settings.rag_similarity_threshold - state.get("retrieval_similarity", 0.0)) / max(
            self.settings.rag_similarity_threshold,
            0.01,
        )
        verifier_score = state.get("verifier_score", 0.70) or 0.70
        verifier_gap = max(0.0, 0.70 - verifier_score) / 0.70
        escalation_bonus = 0.15 if state.get("escalation_required") else 0.0
        score = (
            0.45 * priority_risk
            + 0.25 * uncertainty
            + 0.15 * retrieval_gap
            + 0.10 * verifier_gap
            + 0.05 * state.get("privacy_risk", self._privacy_risk(state))
            + escalation_bonus
        )
        score = round(max(0.0, min(1.0, score)), 4)
        if score >= 0.75:
            return score, "critical"
        if score >= 0.55:
            return score, "elevated"
        if score >= 0.35:
            return score, "watch"
        return score, "normal"

    def _refresh_operational_metadata(self, state: AgentState) -> None:
        if "resolver_recommendation" not in state:
            state["resolver_recommendation"] = self._resolver_recommendation(state)
        if "knowledge_gap" not in state:
            state["knowledge_gap"] = self._knowledge_gap_signal(state)
        score, level = self._sla_risk(state)
        state["sla_risk_score"] = score
        state["sla_risk_level"] = level
        state["route_explanation"] = self._route_explanation(state)

    def _route_explanation(self, state: AgentState) -> list[dict[str, str]]:
        retrieval_similarity = state.get("retrieval_similarity", 0.0)
        resolver = state.get("resolver_recommendation", {})
        gap = state.get("knowledge_gap", {})
        evidence = state.get("rag_evidence", {})
        route_path = state.get("route_path", "retrieved")
        if state.get("semantic_cache_hit"):
            route_impact = "approved historical resolution returned without invoking the LLM"
        elif state.get("escalation_required"):
            route_impact = state.get("escalation_rationale") or "LLM decision requires human review"
        else:
            route_impact = "retrieved context was attached to the prompt and resolved by the LLM"
        return [
            {
                "label": "Privacy gate",
                "value": f"{state.get('redacted_count', 0)} redactions",
                "impact": "only sanitized text is available to retrieval and model calls",
            },
            {
                "label": "Nearest approved ticket",
                "value": f"{state.get('matched_ticket_id') or 'none'} at {retrieval_similarity:.2f}",
                "impact": "similarity only controls the >=0.95 semantic cache bypass; otherwise the LLM decides",
            },
            {
                "label": "Route branch",
                "value": route_path,
                "impact": route_impact,
            },
            {
                "label": "RAG evidence pack",
                "value": f"{evidence.get('quality_band', 'unknown')} at {float(evidence.get('quality_score', 0.0)):.2f}",
                "impact": f"{int(evidence.get('evidence_count', 0))} approved tickets; category consensus {float(evidence.get('category_consensus', 0.0)):.2f}",
            },
            {
                "label": "Resolver recommendation",
                "value": f"{resolver.get('group', 'Unassigned')} at {float(resolver.get('confidence', 0.0)):.2f}",
                "impact": f"source: {resolver.get('source', 'unknown')}",
            },
            {
                "label": "SLA risk",
                "value": f"{state.get('sla_risk_level', 'normal')} at {state.get('sla_risk_score', 0.0):.2f}",
                "impact": "priority, confidence, retrieval gap, LLM confidence gap, and privacy risk combined",
            },
            {
                "label": "Knowledge coverage",
                "value": "gap detected" if gap.get("is_gap") else "covered",
                "impact": gap.get("reason", "not evaluated"),
            },
        ]

    def _human_review_reason(self, state: AgentState) -> str | None:
        request = state["request"]
        text = state["sanitized_text"].lower()
        if request.urgency == 1 or request.impact == 1:
            return "critical urgency or impact requires human review"
        high_risk_terms = [
            "unauthorized",
            "bulk export",
            "another merchant",
            "corruption",
            "production restore",
            "privileged",
            "oauth",
            "audit logs",
            "duplicate payment",
            "duplicate charge",
            "fee calculation mismatch",
            "different total",
            "financial discrepancies",
            "incorrect processing rates",
            "outage across",
            "queue depth",
            "crashing",
            "security",
            "compliance",
        ]
        if any(term in text for term in high_risk_terms):
            return "security, compliance, payment, or production-risk signal matched"
        return None

    def _to_response(self, state: AgentState) -> dict:
        retrieved = state.get("retrieved", [])
        return {
            "ticket_id": state["ticket_id"],
            "assigned_category": state["assigned_category"],
            "suggested_resolution": state.get("suggested_resolution", []),
            "confidence_score": state["confidence_score"],
            "confidence_components": {
                "classification": state.get("classification_confidence", 0.0),
                "retrieval_similarity": state.get("retrieval_similarity", 0.0),
                "verifier_score": state.get("verifier_score", 0.0),
                "privacy_risk": state.get("privacy_risk", 0.0),
            },
            "escalation_required": state.get("escalation_required", True),
            "redacted_entities_count": state.get("redacted_count", 0),
            "routing_latency_ms": state.get("routing_latency_ms", 0),
            "agent_state": state.get("agent_state", "unknown"),
            "retrieved_ticket_ids": [item.ticket_id for item in retrieved],
            "route_path": state.get("route_path", "generative_rag"),
            "semantic_cache_hit": bool(state.get("semantic_cache_hit", False)),
            "matched_ticket_id": state.get("matched_ticket_id"),
            "sla_risk": {
                "score": state.get("sla_risk_score", 0.0),
                "level": state.get("sla_risk_level", "normal"),
            },
            "knowledge_gap": state.get("knowledge_gap", self._knowledge_gap_signal(state)),
            "resolver_recommendation": state.get("resolver_recommendation", self._resolver_recommendation(state)),
            "rag_evidence": state.get("rag_evidence", self._build_rag_evidence(state)),
            "route_explanation": state.get("route_explanation", self._route_explanation(state)),
        }

    def _store_ticket(self, request: TicketRequest, state: AgentState) -> None:
        embedding = embed_text(state["sanitized_text"], self.settings.embedding_dim)
        record = TicketRecord(
            ticket_id=state["ticket_id"],
            number=request.number or state["ticket_id"],
            short_description=request.short_description,
            description=request.description,
            sanitized_text=state["sanitized_text"],
            category=state["assigned_category"],
            assignment_group=request.assignment_group or "Pending Review",
            resolution="\n".join(state.get("suggested_resolution", [])),
            urgency=request.urgency,
            impact=request.impact,
            embedding=embedding,
            source="api",
        )
        self.repo.insert_tickets([record])
        audit = state.get("privacy_audit")
        if audit:
            self.repo.insert_privacy_audit(**audit)


def normalize_category(category: str) -> str:
    value = category.strip().lower()
    mapping = {
        "software": "Application",
        "application": "Application",
        "hardware": "Infrastructure",
        "network": "Network",
        "access": "Access Management",
        "security": "Security",
        "database": "Database",
        "storage": "Storage",
        "infrastructure": "Infrastructure",
    }
    return mapping.get(value, category.strip().title() or "Application")


def keyword_category(text: str) -> tuple[str, float]:
    lowered = text.lower()
    if any(word in lowered for word in ["vpn", "network", "latency", "timeout", "router", "switch"]):
        return "Network", 0.62
    if any(word in lowered for word in ["access", "permission", "login", "mfa", "password"]):
        return "Access Management", 0.62
    if any(word in lowered for word in ["database", "sql", "query", "replication"]):
        return "Database", 0.58
    if any(word in lowered for word in ["disk", "storage", "backup", "volume"]):
        return "Storage", 0.58
    if any(word in lowered for word in ["malware", "phishing", "vulnerability", "token"]):
        return "Security", 0.60
    if any(word in lowered for word in ["server", "cpu", "memory", "host"]):
        return "Infrastructure", 0.58
    return "Application", 0.45
