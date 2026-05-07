from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import clickhouse_connect

from app.config import Settings


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    number: str
    short_description: str
    description: str
    sanitized_text: str
    category: str
    assignment_group: str
    resolution: str
    urgency: int
    impact: int
    embedding: list[float]
    source: str


@dataclass(frozen=True)
class RetrievedTicket:
    ticket_id: str
    short_description: str
    sanitized_text: str
    category: str
    assignment_group: str
    resolution: str
    similarity: float
    source: str


class ClickHouseRepository:
    _schema_checked = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            autogenerate_session_id=False,
        )
        if not ClickHouseRepository._schema_checked:
            self.ensure_operational_schema()
            ClickHouseRepository._schema_checked = True

    def ensure_operational_schema(self) -> None:
        columns = [
            ("sla_risk_score", "Float32 DEFAULT 0"),
            ("sla_risk_level", "String DEFAULT ''"),
            ("resolver_group", "String DEFAULT ''"),
            ("resolver_confidence", "Float32 DEFAULT 0"),
            ("knowledge_gap", "UInt8 DEFAULT 0"),
            ("knowledge_gap_reason", "String DEFAULT ''"),
            ("route_explanation", "String DEFAULT ''"),
        ]
        for name, definition in columns:
            self.client.command(
                f"ALTER TABLE routing_decisions ADD COLUMN IF NOT EXISTS {name} {definition}"
            )
        self.client.command(
            """
            CREATE TABLE IF NOT EXISTS review_events
            (
                review_id String,
                ticket_id String,
                decision String,
                reviewer String,
                notes String,
                corrected_category String DEFAULT '',
                corrected_assignment_group String DEFAULT '',
                corrected_resolution String DEFAULT '',
                created_at DateTime DEFAULT now()
            )
            ENGINE = MergeTree
            ORDER BY (ticket_id, created_at)
            """
        )

    def ping(self) -> bool:
        return self.client.command("SELECT 1") == 1

    def insert_tickets(self, tickets: list[TicketRecord]) -> None:
        if not tickets:
            return
        rows = [
            [
                t.ticket_id,
                t.number,
                t.short_description,
                t.description,
                t.sanitized_text,
                t.category,
                t.assignment_group,
                t.resolution,
                t.urgency,
                t.impact,
                t.embedding,
                t.source,
            ]
            for t in tickets
        ]
        self.client.insert(
            "tickets",
            rows,
            column_names=[
                "ticket_id",
                "number",
                "short_description",
                "description",
                "sanitized_text",
                "category",
                "assignment_group",
                "resolution",
                "urgency",
                "impact",
                "embedding",
                "source",
            ],
        )

    def find_similar(self, query_embedding: list[float], limit: int) -> list[RetrievedTicket]:
        result = self.client.query(
            """
            SELECT
                ticket_id,
                short_description,
                sanitized_text,
                category,
                assignment_group,
                resolution,
                source,
                1 - cosineDistance(embedding, %(embedding)s) AS similarity,
                if(source != 'api' AND assignment_group != 'Pending Review' AND length(trim(resolution)) > 0, 1, 0) AS approved,
                if(similarity >= %(fast_threshold)s AND approved = 1, 1, 0) AS fast_candidate
            FROM tickets
            WHERE length(embedding) = %(dim)s
              AND NOT (source = 'api' AND assignment_group = 'Pending Review')
            ORDER BY fast_candidate DESC, similarity DESC, approved DESC, created_at ASC, ticket_id ASC
            LIMIT %(limit)s
            """,
            parameters={
                "embedding": query_embedding,
                "dim": self.settings.embedding_dim,
                "limit": limit,
                "fast_threshold": self.settings.fast_path_similarity_threshold,
            },
        )
        return [
            RetrievedTicket(
                ticket_id=str(row[0]),
                short_description=str(row[1]),
                sanitized_text=str(row[2]),
                category=str(row[3]),
                assignment_group=str(row[4]),
                resolution=str(row[5]),
                source=str(row[6]),
                similarity=float(row[7]),
            )
            for row in result.result_rows
        ]

    def insert_routing_decision(self, decision: dict[str, Any]) -> None:
        self.client.insert(
            "routing_decisions",
            [[
                decision["ticket_id"],
                decision["assigned_category"],
                "\n".join(decision["suggested_resolution"]),
                decision["confidence_score"],
                decision["confidence_components"]["classification"],
                decision["confidence_components"]["retrieval_similarity"],
                decision["confidence_components"]["verifier_score"],
                decision["confidence_components"]["privacy_risk"],
                1 if decision["escalation_required"] else 0,
                decision["route_path"],
                1 if decision["semantic_cache_hit"] else 0,
                decision.get("matched_ticket_id") or "",
                decision["sla_risk"]["score"],
                decision["sla_risk"]["level"],
                decision["resolver_recommendation"]["group"],
                decision["resolver_recommendation"]["confidence"],
                1 if decision["knowledge_gap"]["is_gap"] else 0,
                decision["knowledge_gap"]["reason"],
                json.dumps(decision["route_explanation"], separators=(",", ":")),
                decision["model_name"],
                decision["routing_latency_ms"],
            ]],
            column_names=[
                "ticket_id",
                "assigned_category",
                "suggested_resolution",
                "confidence_score",
                "classification_confidence",
                "retrieval_similarity",
                "verifier_score",
                "privacy_risk",
                "escalation_required",
                "route_path",
                "semantic_cache_hit",
                "matched_ticket_id",
                "sla_risk_score",
                "sla_risk_level",
                "resolver_group",
                "resolver_confidence",
                "knowledge_gap",
                "knowledge_gap_reason",
                "route_explanation",
                "model_name",
                "latency_ms",
            ],
        )

    def insert_review_event(self, decision: dict[str, Any]) -> str:
        review_id = str(uuid4())
        self.client.insert(
            "review_events",
            [[
                review_id,
                decision["ticket_id"],
                decision["decision"],
                decision["reviewer"],
                decision.get("notes") or "",
                decision.get("corrected_category") or "",
                decision.get("corrected_assignment_group") or "",
                decision.get("corrected_resolution") or "",
            ]],
            column_names=[
                "review_id",
                "ticket_id",
                "decision",
                "reviewer",
                "notes",
                "corrected_category",
                "corrected_assignment_group",
                "corrected_resolution",
            ],
        )
        return review_id

    def insert_privacy_audit(
        self,
        *,
        stream_id: str,
        ticket_id: str,
        raw_sha256: str,
        sanitized_sha256: str,
        findings: list[dict[str, Any]],
        detector_version: str,
        policy_version: str,
    ) -> None:
        if not findings:
            return
        rows = [
            [
                str(uuid4()),
                stream_id,
                ticket_id,
                raw_sha256,
                sanitized_sha256,
                detector_version,
                policy_version,
                finding["entity_type"],
                finding["placeholder"],
                float(finding["confidence"]),
                int(finding["start_offset"]),
                int(finding["end_offset"]),
            ]
            for finding in findings
        ]
        self.client.insert(
            "privacy_audit",
            rows,
            column_names=[
                "audit_id",
                "stream_id",
                "ticket_id",
                "raw_sha256",
                "sanitized_sha256",
                "detector_version",
                "policy_version",
                "entity_type",
                "placeholder",
                "confidence",
                "start_offset",
                "end_offset",
            ],
        )
