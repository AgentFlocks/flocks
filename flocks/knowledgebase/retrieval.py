"""Retrieve only Datasets bound to the current Session."""

from __future__ import annotations

from typing import Any

from .client import KnowledgebaseClient
from .errors import KnowledgebaseError
from .session_datasets import dataset_ids_from_metadata, get_selection


async def retrieve_for_session(
    session_id: str,
    user: Any,
    keywords: str,
    *,
    client: KnowledgebaseClient,
    dataset: list[str] | None = None,
    top_k: int = 5,
    similarity_threshold: float = 0.2,
    vector_similarity_weight: float = 0.3,
) -> dict[str, Any]:
    if not isinstance(keywords, str) or not keywords.strip() or len(keywords) > 8000:
        raise KnowledgebaseError(422, "invalid_keywords", "Retrieval keywords are required.")
    selection = await get_selection(session_id, user)
    allowed = selection["dataset_ids"]
    # Models often send dataset: [] for "no preference". That is the whole Session selection.
    if not dataset:
        chosen = allowed
    else:
        if any(item not in allowed for item in dataset):
            raise KnowledgebaseError(403, "dataset_not_bound", "Retrieval can only use this Session's Datasets.")
        chosen = dataset
    if not chosen:
        return {"chunks": [], "total": 0}
    return await client.retrieve(
        {
            "dataset_ids": chosen,
            "keywords": keywords.strip(),
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
        }
    )


def bound_dataset_ids(metadata: Any) -> list[str]:
    return dataset_ids_from_metadata(metadata)
