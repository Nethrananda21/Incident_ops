CREATE DATABASE IF NOT EXISTS incident_ai;

CREATE TABLE IF NOT EXISTS incident_ai.tickets
(
    ticket_id String,
    number String,
    short_description String,
    description String,
    sanitized_text String,
    category String,
    assignment_group String,
    resolution String,
    urgency UInt8,
    impact UInt8,
    embedding Array(Float32),
    source String,
    created_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (category, ticket_id);

CREATE TABLE IF NOT EXISTS incident_ai.privacy_audit
(
    audit_id String,
    stream_id String,
    ticket_id String,
    raw_sha256 String,
    sanitized_sha256 String,
    detector_version String,
    policy_version String,
    entity_type String,
    placeholder String,
    confidence Float32,
    start_offset UInt32,
    end_offset UInt32,
    created_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (stream_id, entity_type, created_at);

CREATE TABLE IF NOT EXISTS incident_ai.routing_decisions
(
    ticket_id String,
    assigned_category String,
    suggested_resolution String,
    confidence_score Float32,
    classification_confidence Float32,
    retrieval_similarity Float32,
    verifier_score Float32,
    privacy_risk Float32,
    escalation_required UInt8,
    route_path String DEFAULT 'generative_rag',
    semantic_cache_hit UInt8 DEFAULT 0,
    matched_ticket_id String DEFAULT '',
    model_name String,
    latency_ms UInt32,
    created_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (ticket_id, created_at);
