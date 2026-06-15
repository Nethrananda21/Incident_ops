from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from openai import APITimeoutError, OpenAI, RateLimitError

from app.clickhouse_repo import RetrievedTicket
from app.config import Settings

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Verification:
    score: float
    rationale: str


@dataclass(frozen=True)
class ResolutionDecision:
    resolution_steps: list[str]
    escalation_required: bool
    confidence_score: float
    rationale: str


class NvidiaLLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = bool(settings.nvidia_api_key.strip())
        self.client = (
            OpenAI(
                api_key=settings.nvidia_api_key,
                base_url=settings.nvidia_base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            )
            if self.enabled
            else None
        )

    def generate_resolution(self, ticket_text: str, category: str, retrieved: list[RetrievedTicket]) -> list[str]:
        if not self.enabled:
            return self._fallback_resolution(category, retrieved)

        ticket_text = clip_text(ticket_text, self.settings.llm_max_input_chars)
        context = build_context(retrieved, self.settings.llm_max_context_items, self.settings.llm_max_context_chars)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an enterprise IT support resolution agent. Use only sanitized input and retrieved "
                    "ticket context. Return concise, actionable steps. Do not invent secrets, personal data, "
                    "or system identifiers."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Category: {category}\n\nCurrent sanitized ticket:\n{ticket_text}\n\n"
                    f"Retrieved sanitized context:\n{context}\n\n"
                    "Draft 3 to 6 resolution steps."
                ),
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.settings.nvidia_llm_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
            )
            text = response.choices[0].message.content or ""
            return normalize_steps(text)
        except (APITimeoutError, RateLimitError) as exc:
            LOGGER.warning(
                "NVIDIA resolution generation transient failure (%s); using deterministic fallback",
                exc.__class__.__name__,
            )
            return self._fallback_resolution(category, retrieved)
        except Exception:
            LOGGER.exception("NVIDIA resolution generation failed; using deterministic fallback")
            return self._fallback_resolution(category, retrieved)

    def resolve_and_decide(
        self,
        ticket_text: str,
        category: str,
        retrieved: list[RetrievedTicket],
        rag_evidence: dict | None,
        urgency: int,
        impact: int,
        policy_signal: str | None = None,
    ) -> ResolutionDecision:
        if not self.enabled:
            return self._fallback_decision(category, retrieved, policy_signal)

        ticket_text = clip_text(ticket_text, self.settings.llm_max_input_chars)
        context = build_context(retrieved, self.settings.llm_max_context_items, self.settings.llm_max_context_chars)
        evidence_summary = build_evidence_summary(rag_evidence)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an enterprise IT incident-routing and resolution agent. You receive sanitized ticket "
                    "text, a scored RAG evidence pack, and the top retrieved historical tickets. Use the retrieved "
                    "tickets as evidence, draft safe resolution steps, and decide whether this ticket needs human "
                    "escalation. Similarity is only a context signal; do not escalate solely because a numeric "
                    "similarity is low. Treat weak evidence as a grounding warning, then make the final decision "
                    "from ticket scope, risk, and whether a safe triage playbook can be proposed. Do not escalate "
                    "ordinary low-risk IT incidents solely because no historical ticket was retrieved. Escalate when "
                    "the issue is outside supported IT scope, the action needs human authorization, "
                    "or the ticket has production, security, compliance, "
                    "payment, privacy, or data-integrity risk. The supported scope is IT incident handling for "
                    "applications, access, customer or merchant portals, infrastructure, network, hardware, "
                    "database, storage, and security. Do not directly solve non-IT requests such as cafeteria, "
                    "lunch, events, facilities, HR, travel, procurement, or office-admin tasks; mark those for "
                    "human review or triage handoff. Respond as JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Category hint: {category}\nUrgency: P{urgency}\nImpact: P{impact}\n"
                    f"Policy/risk signal: {policy_signal or 'none'}\n\n"
                    f"Current sanitized ticket:\n{ticket_text}\n\n"
                    f"RAG evidence summary:\n{evidence_summary}\n\n"
                    f"Top retrieved tickets and resolutions:\n{context or 'No retrieved historical tickets.'}\n\n"
                    "Return JSON with exactly these keys: resolution_steps (array of 3 to 6 concise user-actionable "
                    "strings), escalation_required (boolean), confidence_score (0.0 to 1.0), rationale (short string). "
                    "If the ticket is outside supported IT incident scope, escalation_required must be true."
                ),
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.settings.nvidia_llm_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
                response_format={"type": "json_object"},
            )
            payload = response.choices[0].message.content or "{}"
            data = load_json_object(payload)
            steps = normalize_resolution_steps(data.get("resolution_steps"))
            confidence = max(0.0, min(1.0, float(data.get("confidence_score", 0.0))))
            escalation_required = bool(data.get("escalation_required", True))
            rationale = str(data.get("rationale", ""))[:1000]
            if not steps:
                steps = (
                    self._fallback_escalation_steps(rationale)
                    if escalation_required
                    else self._fallback_resolution(category, retrieved)
                )
            return ResolutionDecision(
                resolution_steps=steps,
                escalation_required=escalation_required,
                confidence_score=confidence,
                rationale=rationale,
            )
        except (APITimeoutError, RateLimitError) as exc:
            LOGGER.warning(
                "NVIDIA resolution decision transient failure (%s); using conservative fallback",
                exc.__class__.__name__,
            )
            return self._fallback_decision(category, retrieved, policy_signal)
        except Exception:
            LOGGER.exception("NVIDIA resolution decision failed; using conservative fallback")
            return self._fallback_decision(category, retrieved, policy_signal)

    def verify(self, ticket_text: str, resolution: list[str], retrieved: list[RetrievedTicket]) -> Verification:
        if not self.enabled:
            top_similarity = retrieved[0].similarity if retrieved else 0.0
            score = 0.72 if top_similarity >= 0.35 and resolution else 0.45
            return Verification(score=score, rationale="Heuristic verifier used because NVIDIA_API_KEY is not configured.")

        ticket_text = clip_text(ticket_text, self.settings.llm_max_input_chars)
        context = build_resolution_context(
            retrieved,
            self.settings.llm_max_context_items,
            self.settings.llm_max_context_chars,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You judge whether a proposed IT resolution is grounded in retrieved ticket context. "
                    "Respond as JSON with keys score and rationale. Score must be 0.0 to 1.0."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Sanitized ticket:\n{ticket_text}\n\nRetrieved resolutions:\n{context}\n\n"
                    f"Proposed resolution:\n{json.dumps(resolution)}"
                ),
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.settings.nvidia_llm_model,
                messages=messages,
                temperature=0,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            payload = response.choices[0].message.content or "{}"
            data = load_json_object(payload)
            return Verification(
                score=max(0.0, min(1.0, float(data.get("score", 0.0)))),
                rationale=str(data.get("rationale", ""))[:1000],
            )
        except (APITimeoutError, RateLimitError) as exc:
            LOGGER.warning(
                "NVIDIA verifier transient failure (%s); using heuristic verifier fallback",
                exc.__class__.__name__,
            )
            top_similarity = retrieved[0].similarity if retrieved else 0.0
            score = 0.70 if top_similarity >= 0.50 and resolution else 0.42
            return Verification(score=score, rationale="Verifier fallback used after model timeout or rate limit.")
        except Exception:
            LOGGER.exception("NVIDIA verifier failed; using heuristic verifier fallback")
            top_similarity = retrieved[0].similarity if retrieved else 0.0
            score = 0.70 if top_similarity >= 0.50 and resolution else 0.42
            return Verification(score=score, rationale="Verifier fallback used after model timeout or invalid response.")

    def _fallback_resolution(self, category: str, retrieved: list[RetrievedTicket]) -> list[str]:
        if retrieved:
            top = retrieved[0]
            return [
                f"Classify as {category} and compare with similar incident {top.ticket_id}.",
                top.resolution,
                "Validate the fix with the affected user and monitor for recurrence.",
            ]
        playbooks = {
            "Network": [
                "Classify as Network based on the reported connectivity symptoms.",
                "Collect affected user, location, VPN or network path, timestamps, and recent network changes.",
                "Check VPN gateway, DNS, routing, packet loss, and interface health before applying remediation.",
                "Route to Network Ops with the collected diagnostics and monitor for recurrence after the fix.",
            ],
            "Access Management": [
                "Classify as Access Management based on the login, permission, or MFA symptoms.",
                "Verify the requester identity, affected account, application, and required access level.",
                "Check identity provider status, group membership, MFA enrollment, and recent access-policy changes.",
                "Route to Identity and Access Management with the validation notes and confirm access restoration.",
            ],
            "Security": [
                "Classify as Security based on the reported security signal.",
                "Preserve relevant logs, timestamps, affected identities, and indicators of compromise.",
                "Check whether the issue is contained and whether any credentials or tokens need rotation.",
                "Route to Security Operations with evidence and recommended containment steps.",
            ],
            "Database": [
                "Classify as Database based on the data-store symptoms.",
                "Collect query errors, affected tables or services, timestamps, replication status, and recent changes.",
                "Check connection health, locks, slow queries, storage pressure, and replica lag.",
                "Route to the DBA team with diagnostics and validate the affected transaction or workflow.",
            ],
            "Storage": [
                "Classify as Storage based on disk, volume, backup, or capacity symptoms.",
                "Collect affected host, mount, volume, backup job, timestamps, and current capacity metrics.",
                "Check volume health, available capacity, recent expansion or backup failures, and related alerts.",
                "Route to Storage Operations with remediation notes and verify service recovery.",
            ],
            "Infrastructure": [
                "Classify as Infrastructure based on server, host, CPU, memory, or platform symptoms.",
                "Collect affected host, service, timestamps, resource metrics, and recent deployment or patch history.",
                "Check process health, capacity, logs, and platform alerts before applying remediation.",
                "Route to Platform Engineering with diagnostics and monitor the host after recovery.",
            ],
            "Application": [
                "Classify as Application based on the reported service or workflow symptoms.",
                "Collect affected workflow, user scope, timestamps, error messages, and recent release history.",
                "Check application logs, dependency health, queues, and configuration changes before remediation.",
                "Route to Application Operations with diagnostics and verify the workflow after recovery.",
            ],
        }
        normalized_category = category if category in playbooks else "Application"
        return playbooks[normalized_category]

    def _fallback_decision(
        self,
        category: str,
        retrieved: list[RetrievedTicket],
        policy_signal: str | None,
    ) -> ResolutionDecision:
        has_grounded_context = bool(retrieved and retrieved[0].resolution.strip())
        escalation_required = bool(policy_signal)
        steps = self._fallback_escalation_steps(policy_signal) if escalation_required else self._fallback_resolution(category, retrieved)
        if escalation_required:
            confidence = 0.42
            rationale = "Fallback decision used because the LLM decision call was unavailable."
        elif has_grounded_context:
            confidence = 0.62
            rationale = "Deterministic fallback used an approved historical resolution because the LLM decision call was unavailable."
        else:
            confidence = 0.58
            rationale = "Deterministic fallback used a category playbook because the LLM decision call was unavailable and no high-risk signal matched."
        return ResolutionDecision(
            resolution_steps=steps,
            escalation_required=escalation_required,
            confidence_score=confidence,
            rationale=rationale,
        )

    def _fallback_escalation_steps(self, reason: str | None = None) -> list[str]:
        return [
            f"Route this ticket to human support triage{f': {reason}' if reason else '.'}",
            "Do not apply a historical incident fix until a reviewer confirms the request is in scope.",
            "Collect the requester, business context, affected service, timestamps, and any supporting evidence.",
        ]


def normalize_steps(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[\).\s-]*)\s*", "", raw.strip())
        cleaned = cleaned.replace("**", "").replace("__", "").strip()
        if cleaned:
            lines.append(clip_text(cleaned, 800))
    if not lines and text.strip():
        lines = [clip_text(text.strip(), 800)]
    return lines[:6]


def normalize_resolution_steps(value: object) -> list[str]:
    if isinstance(value, list):
        lines = [clip_text(str(item).strip(), 800) for item in value if str(item).strip()]
        return lines[:6]
    if isinstance(value, str):
        return normalize_steps(value)
    return []


def build_context(items: list[RetrievedTicket], limit: int, max_chars: int) -> str:
    chunks = []
    remaining = max_chars
    for item in items[:limit]:
        chunk = (
            f"Past ticket {item.ticket_id} ({item.category}, score={item.similarity:.2f}):\n"
            f"Symptoms: {item.sanitized_text}\nResolution: {item.resolution}"
        )
        chunk = clip_text(chunk, max(0, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return "\n\n".join(chunks)


def build_evidence_summary(rag_evidence: dict | None) -> str:
    if not rag_evidence:
        return "No RAG evidence pack was provided."
    allowed_keys = [
        "quality_score",
        "quality_band",
        "top_similarity",
        "average_similarity",
        "category_consensus",
        "resolution_coverage",
        "evidence_count",
        "dominant_category",
        "policy",
    ]
    compact = {key: rag_evidence.get(key) for key in allowed_keys if key in rag_evidence}
    items = rag_evidence.get("items")
    if isinstance(items, list):
        compact["items"] = [
            {
                "ticket_id": item.get("ticket_id"),
                "category": item.get("category"),
                "assignment_group": item.get("assignment_group"),
                "similarity": item.get("similarity"),
                "evidence_role": item.get("evidence_role"),
                "resolution_present": item.get("resolution_present"),
            }
            for item in items[:5]
            if isinstance(item, dict)
        ]
    return json.dumps(compact, ensure_ascii=True, separators=(",", ":"))


def build_resolution_context(items: list[RetrievedTicket], limit: int, max_chars: int) -> str:
    chunks = []
    remaining = max_chars
    for item in items[:limit]:
        chunk = f"{item.ticket_id}: {item.resolution}"
        chunk = clip_text(chunk, max(0, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return "\n\n".join(chunks)


def clip_text(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    if max_chars <= 14:
        return value[:max_chars]
    return value[: max_chars - 14].rstrip() + "\n[truncated]"


def load_json_object(payload: str) -> dict:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(payload[start : end + 1])
    return data if isinstance(data, dict) else {}
