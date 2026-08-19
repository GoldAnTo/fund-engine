# Goal-Aware Evidence Replenishment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make support, contradiction, alternative-explanation, and rule-verification tasks carry distinct, enforced evidence objectives through recall, prompting, proposal validation, and audit output.

**Architecture:** Add one typed `EvidenceObjective` value object as the interface between orchestration, recall, and the LLM proposer. Preserve `ResearchTask.task_type` for wire compatibility, but convert it once at the service seam and reject proposal roles that do not satisfy the task objective.

**Tech Stack:** Python 3.12, SQLAlchemy 2, pytest, existing LLM client and AIRun audit records.

---

## File map

- Create `backend/app/services/evidence_objective.py`: objective enum and role policy.
- Modify `backend/app/services/auto_research.py`: convert task type to an objective.
- Modify `backend/app/services/recall.py`: accept the objective and apply objective-specific ranking terms.
- Modify `backend/app/ai/proposal.py`: prompt and validate against the objective.
- Modify `backend/app/ai/prompts.py`: version the changed prompt contract.
- Test with unit tests and orchestration tests; no database migration is required.

### Task 1: Define the objective contract

**Files:**
- Create: `backend/app/services/evidence_objective.py`
- Create: `backend/tests/test_evidence_objective.py`

- [ ] **Step 1: Write the failing contract tests**

```python
import pytest

from app.services.evidence_objective import EvidenceObjective


@pytest.mark.parametrize(
    ("task_type", "value", "roles"),
    [
        ("support", "support", {"supports"}),
        ("contradict", "contradict", {"contradicts"}),
        ("alternative", "alternative_explanation", {"contextualizes", "contradicts"}),
        ("verify_rule", "verify_rule", {"supports", "contradicts"}),
    ],
)
def test_objective_maps_task_type_and_allowed_roles(task_type, value, roles):
    objective = EvidenceObjective.from_task_type(task_type)
    assert objective.value == value
    assert objective.allowed_roles == roles


def test_result_task_is_not_an_evidence_objective():
    with pytest.raises(ValueError, match="not an evidence objective"):
        EvidenceObjective.from_task_type("result")
```

- [ ] **Step 2: Verify the module does not exist**

Run: `cd backend && uv run pytest tests/test_evidence_objective.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the value object**

```python
from __future__ import annotations

from enum import StrEnum


class EvidenceObjective(StrEnum):
    SUPPORT = "support"
    CONTRADICT = "contradict"
    ALTERNATIVE_EXPLANATION = "alternative_explanation"
    VERIFY_RULE = "verify_rule"

    @classmethod
    def from_task_type(cls, task_type: str) -> "EvidenceObjective":
        mapping = {
            "support": cls.SUPPORT,
            "contradict": cls.CONTRADICT,
            "alternative": cls.ALTERNATIVE_EXPLANATION,
            "verify_rule": cls.VERIFY_RULE,
        }
        try:
            return mapping[task_type]
        except KeyError as exc:
            raise ValueError(f"{task_type!r} is not an evidence objective") from exc

    @property
    def allowed_roles(self) -> set[str]:
        return {
            self.SUPPORT: {"supports"},
            self.CONTRADICT: {"contradicts"},
            self.ALTERNATIVE_EXPLANATION: {"contextualizes", "contradicts"},
            self.VERIFY_RULE: {"supports", "contradicts"},
        }[self]

    @property
    def instruction(self) -> str:
        return {
            self.SUPPORT: "只寻找能够直接支持待验证陈述的材料。",
            self.CONTRADICT: "主动寻找与待验证陈述冲突或能证伪它的材料。",
            self.ALTERNATIVE_EXPLANATION: "寻找能够独立解释观察结果的替代机制，不把背景相关性当成支持。",
            self.VERIFY_RULE: "按已冻结验证规则寻找可以通过或否决规则的观测。",
        }[self]
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_evidence_objective.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/evidence_objective.py backend/tests/test_evidence_objective.py
git commit -m "feat: define evidence research objectives"
```

### Task 2: Pass objective through orchestration and proposal audit

**Files:**
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/app/ai/proposal.py`
- Test: `backend/tests/test_auto_research_api.py`
- Test: `backend/tests/test_ai_engine.py`

- [ ] **Step 1: Write failing propagation tests**

Use a recording proposer in the auto-research test and assert its calls contain `SUPPORT`, `CONTRADICT`, and `ALTERNATIVE_EXPLANATION` for the corresponding tasks. In the proposer test, inspect the fake client's last payload and assert:

```python
assert user_data["objective"] == "contradict"
assert "主动寻找" in user_data["objective_instruction"]
```

Also assert the resulting `AIRun.input_ref` contains `"objective": "contradict"`.

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && uv run pytest tests/test_auto_research_api.py tests/test_ai_engine.py -k objective -v`

Expected: FAIL because `EvidenceProposer.propose` accepts no objective.

- [ ] **Step 3: Add the required proposer argument**

Change the signature to:

```python
def propose(
    self,
    thesis_id: uuid.UUID,
    session: Session,
    *,
    objective: EvidenceObjective,
    before_persist: Callable[[], bool] | None = None,
    allowed_source_types: set[str] | None = None,
) -> list[uuid.UUID]:
```

Add to `input_ref` and `user_data`:

```python
input_ref["objective"] = objective.value
user_data["objective"] = objective.value
user_data["objective_instruction"] = objective.instruction
user_data["allowed_roles"] = sorted(objective.allowed_roles)
```

In `_propose_for_task`, derive once:

```python
objective = EvidenceObjective.from_task_type(task.task_type)
proposed_ids = proposer.propose(
    task.thesis_id,
    self.session,
    objective=objective,
    before_persist=lambda: self._claim_task_output_slot(run, task),
    allowed_source_types=allowed_source_types or None,
)
```

- [ ] **Step 4: Run affected tests**

Run: `cd backend && uv run pytest tests/test_auto_research_api.py tests/test_ai_engine.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/auto_research.py backend/app/ai/proposal.py backend/tests/test_auto_research_api.py backend/tests/test_ai_engine.py
git commit -m "feat: propagate evidence objectives to proposals"
```

### Task 3: Reject semantically mismatched proposal roles

**Files:**
- Modify: `backend/app/ai/proposal.py`
- Modify: `backend/app/ai/prompts.py`
- Test: `backend/tests/test_ai_engine.py`

- [ ] **Step 1: Add a failing counter-evidence test**

Configure the fake LLM to return a `supports` link for a contradiction objective, then assert:

```python
created = proposer.propose(
    thesis.id,
    session,
    objective=EvidenceObjective.CONTRADICT,
)
assert created == []
assert session.scalar(select(func.count()).select_from(Proposal)) == 0
run = session.scalar(select(AIRun).order_by(AIRun.started_at.desc()))
assert "objective-role mismatch=1" in run.output_summary
```

- [ ] **Step 2: Verify the current code accepts the wrong role**

Run: `cd backend && uv run pytest tests/test_ai_engine.py -k objective_role_mismatch -v`

Expected: FAIL because the support proposal is persisted.

- [ ] **Step 3: Enforce the role policy before creating a Proposal**

Inside the result loop add:

```python
role = str(link_data.get("role", ""))
if role not in objective.allowed_roles:
    mismatched += 1
    continue
```

Include `mismatched` in the audit summary. Update `PROPOSE_SYSTEM` to state that every returned role must be one of `allowed_roles`, and increment `PROPOSE_PROMPT_VERSION` so old and new runs remain distinguishable.

- [ ] **Step 4: Run AI proposal tests**

Run: `cd backend && uv run pytest tests/test_ai_engine.py -k proposer -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/proposal.py backend/app/ai/prompts.py backend/tests/test_ai_engine.py
git commit -m "fix: reject evidence roles outside task objective"
```

### Task 4: Add objective-sensitive recall ordering

**Files:**
- Modify: `backend/app/services/recall.py`
- Test: `backend/tests/test_recall.py`

- [ ] **Step 1: Write ranking tests with the same thesis and different objectives**

Seed statements containing explicit negation/miss language and explicit confirmation/beat language. Assert the contradiction objective ranks the first group above the second, while support does the reverse:

```python
contradict = service.for_thesis(
    thesis,
    cutoff=cutoff,
    objective=EvidenceObjective.CONTRADICT,
)
support = service.for_thesis(
    thesis,
    cutoff=cutoff,
    objective=EvidenceObjective.SUPPORT,
)
assert contradict[0].id == miss_statement.id
assert support[0].id == beat_statement.id
```

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && uv run pytest tests/test_recall.py -k objective -v`

Expected: FAIL because both calls currently use identical ranking.

- [ ] **Step 3: Add bounded objective terms to the existing score**

Change `for_thesis` to accept `objective: EvidenceObjective | None = None`. Add a small deterministic bonus after the existing relevance score:

```python
_OBJECTIVE_TERMS = {
    EvidenceObjective.SUPPORT: ("确认", "达到", "高于", "增长", "兑现"),
    EvidenceObjective.CONTRADICT: ("不及", "低于", "下调", "未达到", "证伪", "下降"),
    EvidenceObjective.ALTERNATIVE_EXPLANATION: ("由于", "同时", "宏观", "估值", "情绪", "替代"),
    EvidenceObjective.VERIFY_RULE: ("实际", "披露", "审计", "同比", "环比", "完成"),
}

def _objective_bonus(text: str, objective: EvidenceObjective | None) -> float:
    if objective is None:
        return 0.0
    return min(0.15, 0.03 * sum(term in text for term in _OBJECTIVE_TERMS[objective]))
```

The bonus must not bypass cutoff, case admission, source contract, or allowed-source-type filters.

- [ ] **Step 4: Pass the objective from `EvidenceProposer` and run tests**

Run: `cd backend && uv run pytest tests/test_recall.py tests/test_ai_engine.py tests/test_auto_research_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/recall.py backend/app/ai/proposal.py backend/tests/test_recall.py
git commit -m "feat: rank recalled evidence by research objective"
```

### Task 5: Phase verification

**Files:**
- No production file changes.

- [ ] **Step 1: Run the complete evidence-research slice**

Run: `cd backend && uv run pytest tests/test_evidence_objective.py tests/test_recall.py tests/test_ai_engine.py tests/test_auto_research_api.py tests/test_proposal_review_api.py -v`

Expected: PASS.

- [ ] **Step 2: Verify audit semantics manually in a test database**

Run: `cd backend && uv run pytest tests/test_auto_research_api.py -k "support or contradict or alternative" -vv`

Expected: each task records a distinct objective and no contradiction task publishes a support-only proposal.
