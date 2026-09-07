"""Totals for explicitly attributed, persisted operations; never inferred billing."""
from sqlalchemy import select
from app.ai.scope_columns import AuditCaseRef, AuditRunRef
from app.models.ledger import AIRun
from app.schemas.v1.auto_research import RunAIUsageDTO, CaseAIUsageDTO


def summarize_usage(usage_records):
    result = dict(operation_count=0, reported_attempt_count=0, unavailable_attempt_count=0,
                  unavailable_operation_count=0, reported_prompt_tokens=0,
                  reported_completion_tokens=0, reported_total_tokens=0)
    for usage in usage_records:
        result['operation_count'] += 1
        if not isinstance(usage, dict) or usage.get('schema_version') != 'llm_usage.v1' or not isinstance(usage.get('attempts'), list):
            result['unavailable_operation_count'] += 1
            continue
        for attempt in usage['attempts']:
            values = [attempt.get(key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')] if isinstance(attempt, dict) else [None] * 3
            valid = isinstance(attempt, dict) and attempt.get('usage_state') == 'reported' and all(type(value) is int and 0 <= value <= 2**63 - 1 for value in values)
            if not valid or values[0] + values[1] != values[2]:
                result['unavailable_attempt_count'] += 1
                continue
            result['reported_attempt_count'] += 1
            for key, value in zip(('reported_prompt_tokens', 'reported_completion_tokens', 'reported_total_tokens'), values):
                result[key] += value
    complete = result['operation_count'] > 0 and not result['unavailable_operation_count'] and not result['unavailable_attempt_count']
    result['recorded_total_tokens'] = result['reported_total_tokens'] if complete else None
    return result


def run_ai_usage(session, run):
    # Select only usage JSON, never prompts, source text, or provider error data.
    values = session.scalars(select(AIRun.usage).where(
        AuditRunRef(AIRun.input_ref) == str(run.id),
        AuditCaseRef(AIRun.input_ref) == str(run.research_case_id),
    ).execution_options(yield_per=200))
    return RunAIUsageDTO(run_id=str(run.id), **summarize_usage(values))


def case_ai_usage(session, case_id):
    values = session.scalars(select(AIRun.usage).where(
        AuditCaseRef(AIRun.input_ref) == str(case_id),
    ).execution_options(yield_per=200))
    return CaseAIUsageDTO(case_id=str(case_id), **summarize_usage(values))
