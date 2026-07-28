"""Turn-driven Skill evolution and durable proposal application."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from flocks.config import Config
from flocks.memory.config import MemoryConfig

from .common import (
    EvolutionCheckpointStore,
    SkillProposalStore,
    _PIPELINE_LOCKS,
    _apply_pending_proposal,
    _build_turn_review,
    _chat_json,
    _prepare_skill_proposal,
    _related_skill_contents,
    _skill_catalog,
    _truncate_tail,
    _user_skill_root,
    log,
)


SKILL_REVIEW_SYSTEM_PROMPT = """
Review a successful tool turn for a reusable workflow.

- Prefer improving an existing skill over creating a duplicate.
- Select at most the few skills whose full contents are needed.
- A new workflow may use an empty skill_names list.

Return strict JSON only:
{"action":"skip","reason":"..."}
or
{"action":"evolve","skill_names":["existing-name"],"reason":"..."}.
""".strip()

SKILL_REVIEW_USER_PROMPT = """
Trigger evidence: {trigger_reasons}

{review_content}

Available skills:
{skill_catalog}
""".strip()

SKILL_PROPOSAL_SYSTEM_PROMPT = """
Create one validated Skill proposal from proven behavior.

Return strict JSON only. Supported actions:
- skip
- create(skill_name, content=complete SKILL.md)
- edit(skill_name, content=complete SKILL.md)
- patch(skill_name, path="SKILL.md", old, new), where old matches exactly once

The SKILL.md frontmatter must contain the matching name and a description that
states both what the Skill does and when it should trigger. Keep the document
under 500 lines, generalize the workflow, explain why steps matter, and do not
encode secrets or transient facts. Only selected source="user" Skills may be
edited; never shadow Project, bundled, or installed Skills.
""".strip()

SKILL_PROPOSAL_USER_PROMPT = """
{review_content}

Selected existing Skills:
{related_skills}
""".strip()


async def recover_pending_skill_proposals(
    *,
    skill_root: Optional[Path] = None,
) -> int:
    """Idempotently finish proposals interrupted before their DB finalization."""
    root = skill_root or _user_skill_root()
    recovered = 0
    for proposal in await SkillProposalStore.list_pending():
        try:
            if await _apply_pending_proposal(proposal, skill_root=root):
                recovered += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warn(
                "skill_proposal.recovery_failed",
                {"proposal_id": proposal.id, "error": str(exc)},
            )
    return recovered


async def process_skill_turn(
    *,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
    provider_id: str,
    model_id: str,
    skill_root: Optional[Path] = None,
) -> bool:
    """Review one successful turn and apply a validated Skill proposal."""
    app_config = await Config.get()
    config = getattr(app_config, "memory", None)
    if not isinstance(config, MemoryConfig):
        return False
    if not config.enabled or not config.evolution.enabled or not config.evolution.skill.enabled:
        return False

    from flocks.session.session import Session

    session = await Session.get_by_id(session_id)
    if session is None or session.category != "user" or session.status == "deleted":
        return False

    async with _PIPELINE_LOCKS["skill"]:
        checkpoint = await EvolutionCheckpointStore.get(
            "skill",
            "session",
            session_id,
        )
        last_reviewed = checkpoint.get("last_message_id") if checkpoint else None
        if last_reviewed and last_reviewed >= assistant_message_id:
            return False

        existing = await SkillProposalStore.get_by_assistant_message(assistant_message_id)
        root = skill_root or _user_skill_root()
        if existing is not None:
            if existing.status == "pending":
                return await _apply_pending_proposal(
                    existing,
                    skill_root=root,
                )
            return existing.status == "applied"

        review = await _build_turn_review(
            session_id=session_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            config=config,
        )
        if review is None:
            return False

        catalog = _skill_catalog()
        selection = await _chat_json(
            provider_id=provider_id,
            model_id=model_id,
            max_tokens=1000,
            system=SKILL_REVIEW_SYSTEM_PROMPT,
            user=SKILL_REVIEW_USER_PROMPT.format(
                trigger_reasons=", ".join(review.trigger_reasons),
                review_content=review.content,
                skill_catalog=_truncate_tail(
                    json.dumps(catalog, ensure_ascii=False),
                    config.evolution.max_input_chars // 3,
                ),
            ),
        )
        if selection.get("action") == "skip":
            await EvolutionCheckpointStore.commit("skill", [review.source])
            return False
        if selection.get("action") != "evolve":
            raise ValueError("skill review action must be skip or evolve")
        names = selection.get("skill_names")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError("skill_names must be a string list")
        related = _related_skill_contents(
            names,
            catalog,
            config.evolution.skill.max_related_skills,
        )

        response = await _chat_json(
            provider_id=provider_id,
            model_id=model_id,
            max_tokens=config.evolution.max_output_tokens,
            system=SKILL_PROPOSAL_SYSTEM_PROMPT,
            user=SKILL_PROPOSAL_USER_PROMPT.format(
                review_content=review.content,
                related_skills=_truncate_tail(
                    json.dumps(related, ensure_ascii=False),
                    config.evolution.max_input_chars // 3,
                ),
            ),
        )
        proposal = _prepare_skill_proposal(
            response=response,
            review=review,
            catalog=catalog,
            related=related,
            skill_root=root,
        )
        if proposal is None:
            await EvolutionCheckpointStore.commit("skill", [review.source])
            return False
        stored = await SkillProposalStore.create_pending(proposal)
        return await _apply_pending_proposal(stored, skill_root=root)
