from __future__ import annotations

import json
from dataclasses import dataclass

from openai import OpenAI

from app.clickhouse_repo import RetrievedTicket
from app.config import Settings


@dataclass(frozen=True)
class Verification:
    score: float
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
                max_retries=0,
            )
            if self.enabled
            else None
        )

    def generate_resolution(self, ticket_text: str, category: str, retrieved: list[RetrievedTicket]) -> list[str]:
        if not self.enabled:
            return self._fallback_resolution(category, retrieved)

        context = "\n\n".join(
            f"Past ticket {item.ticket_id} ({item.category}, score={item.similarity:.2f}):\n"
            f"Symptoms: {item.sanitized_text}\nResolution: {item.resolution}"
            for item in retrieved[:5]
        )
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
        except Exception:
            return self._fallback_resolution(category, retrieved)

    def verify(self, ticket_text: str, resolution: list[str], retrieved: list[RetrievedTicket]) -> Verification:
        if not self.enabled:
            top_similarity = retrieved[0].similarity if retrieved else 0.0
            score = 0.72 if top_similarity >= 0.35 and resolution else 0.45
            return Verification(score=score, rationale="Heuristic verifier used because NVIDIA_API_KEY is not configured.")

        context = "\n\n".join(
            f"{item.ticket_id}: {item.resolution}"
            for item in retrieved[:5]
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
            data = json.loads(payload)
            return Verification(
                score=max(0.0, min(1.0, float(data.get("score", 0.0)))),
                rationale=str(data.get("rationale", ""))[:1000],
            )
        except Exception:
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
        return [
            f"Classify as {category} based on available symptoms.",
            "Collect logs, timestamps, affected users, and recent change history.",
            "Escalate to the owning support group because no similar resolved ticket was found.",
        ]


def normalize_steps(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        cleaned = raw.strip().lstrip("-*0123456789. )\t")
        if cleaned:
            lines.append(cleaned)
    if not lines and text.strip():
        lines = [text.strip()]
    return lines[:6]
