"""Theme-tag domain service (横切主题).

Theme tags are classification metadata linking a ResearchCase into
cross-cutting themes. The vocabulary is an explicit, version-controlled set:
unknown tags are rejected (422) so the theme axis stays reviewable instead
of drifting into free-form labels. Assignment changes are appended as
add/remove events (never updates), and the effective tag set is derived by
folding those events.
"""

from __future__ import annotations

import uuid

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
    }
)

_MAX_TAGS_PER_CASE = 10


def effective_tags(events: list[CaseThemeTagEvent]) -> set[str]:
    """Fold add/remove events (creation order) into the effective tag set."""
    tags: set[str] = set()
    for event in events:
        if event.op == "add":
            tags.add(event.tag)
        elif event.op == "remove":
            tags.discard(event.tag)
    return tags


class ThemeService:
    def __init__(self, repository: ResearchRepository) -> None:
        self._repo = repository

    def effective_tags_for_case(self, case_id: uuid.UUID) -> set[str]:
        return effective_tags(self._repo.theme_tag_events(case_id))

    def apply_theme_tags(
        self, *, case: ResearchCase, desired: list[str]
    ) -> tuple[list[str], int]:
        """Diff *desired* against the effective set and append add/remove events.

        Returns the resulting effective tags (sorted) and the number of
        events appended. Repeating the same desired set appends nothing.
        """
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

        current = self.effective_tags_for_case(case.id)
        target = set(cleaned)
        appended = 0
        for tag in sorted(target - current):
            self._repo.add_theme_tag_event(
                research_case_id=case.id, tag=tag, op="add"
            )
            appended += 1
        for tag in sorted(current - target):
            self._repo.add_theme_tag_event(
                research_case_id=case.id, tag=tag, op="remove"
            )
            appended += 1
        return sorted(target), appended
