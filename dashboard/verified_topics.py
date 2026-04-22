from __future__ import annotations

from typing import Any


class VerifiedTopicError(ValueError):
    pass


def normalize_verified_topic_entry(*, topic_id: Any, verified_name: Any, category_key: Any, verification_source: Any) -> dict[str, Any]:
    try:
        normalized_topic_id = int(topic_id)
    except (TypeError, ValueError):
        raise VerifiedTopicError("topic_id must be an integer")
    if normalized_topic_id <= 0:
        raise VerifiedTopicError("topic_id must be positive")

    normalized_name = str(verified_name or "").strip()
    if not normalized_name:
        raise VerifiedTopicError("verified_name is required")

    normalized_category = str(category_key or "").strip()
    if not normalized_category:
        raise VerifiedTopicError("category_key is required")

    normalized_source = str(verification_source or "").strip()
    if not normalized_source:
        raise VerifiedTopicError("verification_source is required")

    return {
        "topic_id": normalized_topic_id,
        "verified_name": normalized_name,
        "category_key": normalized_category,
        "verification_source": normalized_source,
    }


def build_verified_topic_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        normalized = normalize_verified_topic_entry(
            topic_id=row.get("topic_id"),
            verified_name=row.get("verified_name"),
            category_key=row.get("category_key"),
            verification_source=row.get("verification_source") or "stored",
        )
        normalized["verified_at"] = row.get("verified_at")
        normalized["last_seen_at"] = row.get("last_seen_at")
        normalized["observed_name"] = row.get("observed_name")
        index[normalized["category_key"]] = normalized
    return index


def resolve_verified_topic_id(category_key: str, rows: list[dict[str, Any]]) -> int | None:
    key = str(category_key or "").strip()
    if not key:
        return None
    return build_verified_topic_index(rows).get(key, {}).get("topic_id")


def merge_observed_and_verified_topics(observed_rows: list[dict[str, Any]], verified_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    verified_index = build_verified_topic_index(verified_rows)
    merged: list[dict[str, Any]] = []
    seen: set[int] = set()

    for row in observed_rows:
        try:
            topic_id = int(row.get("topic_id"))
        except (TypeError, ValueError):
            continue
        seen.add(topic_id)
        match = next((item for item in verified_index.values() if item["topic_id"] == topic_id), None)
        merged.append({
            "topic_id": topic_id,
            "observed_name": row.get("name"),
            "last_seen_at": row.get("last_seen_at"),
            "verified_name": match.get("verified_name") if match else None,
            "category_key": match.get("category_key") if match else None,
            "verification_source": match.get("verification_source") if match else None,
            "verified_at": match.get("verified_at") if match else None,
            "is_verified": bool(match),
        })

    for match in verified_index.values():
        topic_id = match["topic_id"]
        if topic_id in seen:
            continue
        merged.append({
            "topic_id": topic_id,
            "observed_name": None,
            "last_seen_at": None,
            "verified_name": match.get("verified_name"),
            "category_key": match.get("category_key"),
            "verification_source": match.get("verification_source"),
            "verified_at": match.get("verified_at"),
            "is_verified": True,
        })

    merged.sort(key=lambda row: (not row["is_verified"], row["topic_id"]))
    return merged
