from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    entity_type: str
    placeholder: str
    confidence: float
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class RedactionResult:
    sanitized_text: str
    findings: list[Finding]
    raw_sha256: str
    sanitized_sha256: str


DETECTORS: list[tuple[str, float, re.Pattern[str]]] = [
    ("EMAIL", 0.99, re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)),
    ("IP_ADDRESS", 0.95, re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("CREDENTIAL", 0.97, re.compile(r"\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*[\"']?[^\"'\s,;]{6,}", re.I)),
    ("BEARER_TOKEN", 0.98, re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{12,}", re.I)),
    ("AWS_ACCESS_KEY", 0.99, re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("PRIVATE_KEY", 0.99, re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("PHONE_NUMBER", 0.85, re.compile(r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")),
    ("ACCOUNT_ID", 0.80, re.compile(r"\b(?:(?:account|acct|merchant|customer)[_-]?(?:id)?|user[_-]?id)\s*[:#=]\s*[A-Z0-9\-]{4,}\b", re.I)),
]


def redact_text(text: str) -> RedactionResult:
    matches: list[tuple[int, int, str, float]] = []
    for entity_type, confidence, pattern in DETECTORS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), entity_type, confidence))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    counts: dict[str, int] = {}
    findings: list[Finding] = []
    chunks: list[str] = []
    cursor = 0

    for start, end, entity_type, confidence in matches:
        if start < cursor:
            continue
        counts[entity_type] = counts.get(entity_type, 0) + 1
        placeholder = f"[{entity_type}_{counts[entity_type]}]"
        chunks.append(text[cursor:start])
        chunks.append(placeholder)
        findings.append(Finding(entity_type, placeholder, confidence, start, end))
        cursor = end

    chunks.append(text[cursor:])
    sanitized = "".join(chunks)
    return RedactionResult(
        sanitized_text=sanitized,
        findings=findings,
        raw_sha256=sha256_hex(text),
        sanitized_sha256=sha256_hex(sanitized),
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
