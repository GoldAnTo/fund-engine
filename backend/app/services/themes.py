"""Theme-tag domain service (横切主题).

Theme tags are classification metadata linking a ResearchCase into
cross-cutting themes. The vocabulary is an explicit, version-controlled set:
unknown tags are rejected (422) so the theme axis stays reviewable instead
of drifting into free-form labels. Assignment changes are appended as
add/remove events (never updates), and the effective tag set is derived by
folding the *confirmed* events.

Two-stage review (SPEC §"AI/人工边界"):

* AI callers PATCH the desired tag set. The diff lands as
  ``status='pending'`` events that do not change the effective tag set.
  A proposal_id is generated server-side and returned so the proposal
  can be referenced.
* Human callers PATCH the desired tag set. If the desired set matches an
  open AI proposal, that proposal is promoted to ``status='confirmed'``
  (and the events now count toward the effective set). Otherwise the
  diff lands directly as ``status='confirmed'`` events.

The matching rule is by exact set equality on the desired tag set, so a
human PATCH is the explicit act of confirmation — silently auto-promoting
different sets would defeat the two-stage review.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from app.models.ledger import CaseThemeTagEvent, ResearchCase, ValidationError
from app.repositories.research import ResearchRepository

# Controlled vocabulary. Extend deliberately, in review, alongside the gold
# cases that exercise the theme axis.
THEME_TAG_VOCABULARY: frozenset[str] = frozenset(
    {
        "算力国产化",
        "云厂商CapEx",
        "AI服务器",
        "锂电储能",
        "光模块",
        "半导体设备国产化",
    }
)

_MAX_TAGS_PER_CASE = 10

ProposedBy = Literal["human", "ai"]


def _clean_and_validate(desired: list[str]) -> list[str]:
    """Strip / de-dup / vocabulary-check a desired tag list."""
    cleaned: list[str] = []
    for tag in desired:
        tag = tag.strip()
        if not tag:
            raise ValidationError("theme tag 不能为空")
        if len(tag) > 64:
            raise ValidationError("theme tag 长度不能超过 64 字符")
        if tag not in THEME_TAG_VOCABULARY:
            raise ValidationError(
                f"theme tag '{tag}' 不在受控词汇内；"
                f"合法值：{sorted(THEME_TAG_VOCABULARY)}"
            )
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > _MAX_TAGS_PER_CASE:
        raise ValidationError(f"单个案例的主题标签不能超过 {_MAX_TAGS_PER_CASE} 个")
    return cleaned


def effective_tags(events: list[CaseThemeTagEvent]) -> set[str]:
    """Fold confirmed add/remove events (creation order) into the effective set.

    Pending events do not contribute — by design, they encode AI proposals
    awaiting human confirmation.
    """
    tags: set[str] = set()
    for event in events:
        if event.status != "confirmed":
            continue
        if event.op == "add":
            tags.add(event.tag)
        elif event.op == "remove":
            tags.discard(event.tag)
    return tags


@dataclass
class ThemeTagsResult:
    """Outcome of a ``PATCH /research-cases/{id}/theme-tags`` call."""

    tags: list[str]            # resulting effective tags (sorted)
    events_appended: int       # how many new events landed on the ledger
    proposed_by: str           # echo of the actor type
    proposal_id: uuid.UUID | None  # set when an AI proposal was created
    promoted_proposal_id: uuid.UUID | None  # set when a human PATCH confirmed an AI proposal


class ThemeService:
    def __init__(self, repository: ResearchRepository) -> None:
        self._repo = repository

    def effective_tags_for_case(self, case_id: uuid.UUID) -> set[str]:
        return effective_tags(self._repo.theme_tag_events(case_id))

    def apply_theme_tags(
        self,
        *,
        case: ResearchCase,
        desired: list[str],
        proposed_by: ProposedBy = "human",
    ) -> ThemeTagsResult:
        """Apply a desired tag set with two-stage review semantics.

        Returns a :class:`ThemeTagsResult` describing the outcome (effective
        tags, appended count, and any proposal id created or promoted).
        Idempotent for the *effective* set: repeating the same desired set
        with ``proposed_by='human'`` appends nothing; repeating it with
        ``proposed_by='ai'`` creates another proposal, which is harmless
        (each proposal has a fresh proposal_id) but callers should avoid
        re-proposing.
        """
        cleaned = _clean_and_validate(desired)
        current = self.effective_tags_for_case(case.id)
        target = set(cleaned)

        if proposed_by == "ai":
            # AI: always append as pending, even if the diff is empty (the
            # proposal is the unit of review; if a future operator wants
            # to confirm "no change", they do it explicitly by sending
            # the same set with proposed_by=human).
            proposal_id = uuid.uuid4()
            added, removed = sorted(target - current), sorted(current - target)
            for tag in added:
                self._repo.add_theme_tag_event(
                    research_case_id=case.id,
                    tag=tag,
                    op="add",
                    proposed_by="ai",
                    status="pending",
                    proposal_id=proposal_id,
                )
            for tag in removed:
                self._repo.add_theme_tag_event(
                    research_case_id=case.id,
                    tag=tag,
                    op="remove",
                    proposed_by="ai",
                    status="pending",
                    proposal_id=proposal_id,
                )
            return ThemeTagsResult(
                tags=sorted(current),  # effective unchanged
                events_appended=len(added) + len(removed),
                proposed_by="ai",
                proposal_id=proposal_id,
                promoted_proposal_id=None,
            )

        # proposed_by == "human"
        # First: if there is an open AI proposal whose diff matches the
        # human's desired set exactly, promote it. "Matches exactly" means
        # the events the AI appended would, once confirmed, produce the
        # same target set the human just sent.
        promoted = self._repo.promote_matching_pending_proposal(
            research_case_id=case.id,
            desired_target=target,
        )
        if promoted is not None:
            return ThemeTagsResult(
                tags=sorted(target),
                events_appended=0,  # events already on the ledger, just promoted
                proposed_by="human",
                proposal_id=None,
                promoted_proposal_id=promoted,
            )

        # Otherwise: append the diff directly as confirmed human events.
        appended = 0
        for tag in sorted(target - current):
            self._repo.add_theme_tag_event(
                research_case_id=case.id,
                tag=tag,
                op="add",
                proposed_by="human",
                status="confirmed",
                proposal_id=None,
            )
            appended += 1
        for tag in sorted(current - target):
            self._repo.add_theme_tag_event(
                research_case_id=case.id,
                tag=tag,
                op="remove",
                proposed_by="human",
                status="confirmed",
                proposal_id=None,
            )
            appended += 1
        return ThemeTagsResult(
            tags=sorted(target),
            events_appended=appended,
            proposed_by="human",
            proposal_id=None,
            promoted_proposal_id=None,
        )
