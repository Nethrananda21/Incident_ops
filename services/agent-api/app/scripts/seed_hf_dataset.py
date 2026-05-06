from __future__ import annotations

import argparse
import sys
from typing import Any

import requests

from app.clickhouse_repo import ClickHouseRepository, TicketRecord
from app.config import get_settings
from app.embeddings import embed_text
from app.privacy import redact_text

DATASET = "6StringNinja/synthetic-servicenow-incidents"
CONFIG = "default"
SPLIT = "train"
BASE_URL = "https://datasets-server.huggingface.co"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed ClickHouse with sanitized ServiceNow incidents.")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()

    settings = get_settings()
    repo = ClickHouseRepository(settings)
    total = 0
    offset = 0
    while total < args.limit:
        length = min(args.batch_size, args.limit - total)
        rows = fetch_rows(offset=offset, length=length)
        if not rows:
            break
        records = [to_record(row["row"], settings.embedding_dim) for row in rows]
        repo.insert_tickets(records)
        total += len(records)
        offset += len(records)
        print(f"seeded {total} tickets", flush=True)
        if len(rows) < length:
            break
    print(f"done: seeded {total} tickets from {DATASET}", flush=True)
    return 0


def fetch_rows(offset: int, length: int) -> list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/rows",
        params={
            "dataset": DATASET,
            "config": CONFIG,
            "split": SPLIT,
            "offset": offset,
            "length": length,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def to_record(row: dict[str, Any], dim: int) -> TicketRecord:
    raw_text = f"{row['short_description']}\n\n{row['description']}"
    redacted = redact_text(raw_text)
    return TicketRecord(
        ticket_id=row["number"],
        number=row["number"],
        short_description=row["short_description"],
        description=row["description"],
        sanitized_text=redacted.sanitized_text,
        category=row["category"],
        assignment_group=row["assignment_group"],
        resolution=row["resolution"],
        urgency=int(row["urgency"]),
        impact=int(row["impact"]),
        embedding=embed_text(redacted.sanitized_text, dim),
        source=DATASET,
    )


if __name__ == "__main__":
    raise SystemExit(main())

