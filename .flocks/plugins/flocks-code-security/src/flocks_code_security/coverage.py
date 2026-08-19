"""Coverage-question normalization shared by tools, storage, and reporting."""

from __future__ import annotations

from typing import Any


OPEN_QUESTION_CATEGORIES = {
    "coverage_blocking",
    "validation_limitation",
    "security_hypothesis",
}


def normalize_open_questions(raw_items: Any) -> list[dict[str, Any]]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list) or len(raw_items) > 100:
        raise ValueError("open_questions must be an array of at most 100 items")

    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, str):
            raw = {
                "question": raw,
                "category": "coverage_blocking",
                "blocking": True,
            }
        if not isinstance(raw, dict):
            raise ValueError("Each open question must be an object")
        unknown = set(raw) - {
            "question",
            "category",
            "blocking",
            "related_paths",
            "follow_up",
        }
        if unknown:
            raise ValueError(
                "Unsupported open-question fields: " + ", ".join(sorted(unknown))
            )
        question = raw.get("question")
        category = raw.get("category")
        blocking = raw.get("blocking")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Open-question text must be a non-empty string")
        if len(question) > 1_000:
            raise ValueError("Open-question text may contain at most 1000 characters")
        if category not in OPEN_QUESTION_CATEGORIES:
            raise ValueError("Unsupported open-question category")
        expected_blocking = category == "coverage_blocking"
        if not isinstance(blocking, bool) or blocking is not expected_blocking:
            raise ValueError(
                "blocking must be true exactly when category is coverage_blocking"
            )
        related_paths = raw.get("related_paths", [])
        if not isinstance(related_paths, list) or len(related_paths) > 100:
            raise ValueError("related_paths must be an array of at most 100 paths")
        if any(not isinstance(path, str) or not path for path in related_paths):
            raise ValueError("related_paths must contain non-empty strings")
        follow_up = raw.get("follow_up")
        if follow_up is not None and (
            not isinstance(follow_up, str)
            or not follow_up.strip()
            or len(follow_up) > 1_000
        ):
            raise ValueError("follow_up must be a non-empty string of at most 1000 characters")

        item: dict[str, Any] = {
            "question": question.strip(),
            "category": category,
            "blocking": blocking,
            "related_paths": list(dict.fromkeys(related_paths)),
        }
        if follow_up is not None:
            item["follow_up"] = follow_up.strip()
        normalized.append(item)
    return normalized


def public_open_question(item: dict[str, Any]) -> dict[str, Any]:
    output = {
        "question": item["question"],
        "category": item["category"],
        "blocking": item["blocking"],
        "relatedPaths": item.get("related_paths", []),
    }
    if item.get("follow_up"):
        output["followUpPrompt"] = item["follow_up"]
    return output
