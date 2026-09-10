"""Roles must cite the frozen input, not plausible invented identifiers."""
from uuid import uuid4

import pytest
from app.schemas.v1.research_team import ProfessionalResult


def result(evidence_id, quote='本期收入100亿元'):
    return {'summary': '收入需要结合期间核验', 'findings': [{'statement': '本期收入100亿元', 'basis': 'supported', 'citations': [{'evidence_link_id': str(evidence_id), 'quote': quote}]}], 'gaps': [], 'checks': [], 'limitations': ['未经人工审核']}


def test_definite_finding_cannot_omit_frozen_reference():
    body = result(uuid4())
    body['findings'][0]['citations'] = []
    with pytest.raises(ValueError):
        ProfessionalResult.model_validate(body)


@pytest.mark.parametrize('mutation', ['foreign_id', 'invented_quote', 'role_check_task', 'quality_incomplete'])
def test_output_must_match_authorized_input_and_role(mutation):
    from app.services.research_team_generation import validate_role_result
    evidence_id, parent_id = uuid4(), uuid4()
    body = result(evidence_id)
    role = 'industry'
    if mutation == 'foreign_id':
        body['findings'][0]['citations'][0]['evidence_link_id'] = str(uuid4())
    elif mutation == 'invented_quote':
        body['findings'][0]['citations'][0]['quote'] = '收入增长300%'
    elif mutation == 'role_check_task':
        body['checks'] = [{'kind': 'periods', 'status': 'warning', 'detail': '期间不同', 'task_ids': [str(uuid4())]}]
    else:
        role = 'quality'
    with pytest.raises(ValueError):
        validate_role_result(body, role=role, evidence={str(evidence_id): '本期收入100亿元，期间口径仍需核查'}, dependency_ids={str(parent_id)})


def test_grounded_result_keeps_uncertainty_and_exact_citation():
    from app.services.research_team_generation import validate_role_result
    evidence_id = uuid4()
    parsed = validate_role_result(result(evidence_id), role='finance', evidence={str(evidence_id): '本期收入100亿元，期间口径仍需核查'}, dependency_ids=set())
    assert parsed.findings[0].citations[0].quote == '本期收入100亿元'


@pytest.mark.parametrize('mutation', ['empty_each_check', 'missing_one_parent'])
def test_quality_checks_must_name_and_cover_every_dependency(mutation):
    from app.services.research_team_generation import (
        QUALITY_CHECKS,
        validate_role_result,
    )
    evidence_id = uuid4()
    parents = [str(uuid4()) for _ in range(3)]
    covered = parents if mutation == 'empty_each_check' else parents[:2]
    body = result(evidence_id)
    body['checks'] = [
        {
            'kind': kind,
            'status': 'warning',
            'detail': '仍需逐项人工核验',
            'task_ids': [] if mutation == 'empty_each_check' else covered,
        }
        for kind in sorted(QUALITY_CHECKS)
    ]
    with pytest.raises(ValueError):
        validate_role_result(
            body,
            role='quality',
            evidence={str(evidence_id): '本期收入100亿元，期间口径仍需核查'},
            dependency_ids=set(parents),
        )


def test_quality_checks_accept_complete_dependency_coverage():
    from app.services.research_team_generation import (
        QUALITY_CHECKS,
        validate_role_result,
    )
    evidence_id = uuid4()
    parents = [str(uuid4()) for _ in range(3)]
    body = result(evidence_id)
    body['checks'] = [
        {'kind': kind, 'status': 'warning', 'detail': '仍需逐项人工核验', 'task_ids': [parent]}
        for kind, parent in zip(sorted(QUALITY_CHECKS), parents * 2, strict=True)
    ]
    parsed = validate_role_result(
        body,
        role='quality',
        evidence={str(evidence_id): '本期收入100亿元，期间口径仍需核查'},
        dependency_ids=set(parents),
    )
    assert {str(task_id) for check in parsed.checks for task_id in check.task_ids} == set(parents)


def test_role_generation_uses_actual_llm_client_contract():
    import json
    from types import SimpleNamespace

    from app.ai.client import LLMClient
    from app.services.research_team_generation import generate_role
    evidence_id = uuid4()

    class Provider:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(result(evidence_id)), refusal=None))], model='offline-provider', usage=None, _request_id='offline-request')

    provider = Provider()
    client = LLMClient(model_version='offline', client=provider, max_attempts=1)
    attempts = []
    with client.capture_attempts(attempts.append):
        parsed = generate_role(client, role='finance', payload={'evidence': [{'evidence_link_id': str(evidence_id), 'quote': '本期收入100亿元，期间口径仍需核查'}], 'dependencies': []})
    assert len(provider.calls) == 1
    assert attempts[0].operation == 'professional_finance'
    assert 'ProfessionalResult' in provider.calls[0]['messages'][0]['content']
    assert parsed.findings[0].citations[0].evidence_link_id == evidence_id


@pytest.mark.parametrize('has_parent', [False, True])
def test_prompt_schema_separates_acquisition_ids_from_professional_dependencies(has_parent):
    import json

    from app.services.research_team_generation import generate_role
    evidence_id, acquisition_id, parent_id = map(str, [uuid4(), uuid4(), uuid4()])

    class Client:
        def chat_json(self, messages, **kwargs):
            schema = json.loads(messages[0]['content'].split('输出schema：', 1)[1])
            refs = schema['$defs']['ProfessionalCheck']['properties']['task_ids']
            if has_parent:
                assert refs['items']['enum'] == [parent_id]
                assert acquisition_id not in refs['items']['enum']
            else:
                assert refs['maxItems'] == 0
            assert schema['$defs']['ProfessionalCitation']['properties']['evidence_link_id']['enum'] == [evidence_id]
            return result(evidence_id)

    generate_role(Client(), role='finance', payload={
        'evidence': [{'evidence_link_id': evidence_id, 'task_id': acquisition_id, 'quote': '本期收入100亿元'}],
        'dependencies': [{'task_id': parent_id}] if has_parent else [],
    })


def test_quality_prompt_schema_requires_nonempty_six_check_parent_coverage():
    import json

    from app.services.research_team_generation import QUALITY_CHECKS, generate_role
    evidence_id, acquisition_id = map(str, [uuid4(), uuid4()])
    parent_ids = [str(uuid4()) for _ in range(3)]

    class Client:
        def chat_json(self, messages, **kwargs):
            prompt = messages[0]['content']
            schema = json.loads(prompt.split('输出schema：', 1)[1])
            refs = schema['$defs']['ProfessionalCheck']['properties']['task_ids']
            assert refs['minItems'] == 1
            assert refs['items']['enum'] == sorted(parent_ids)
            assert acquisition_id not in refs['items']['enum']
            assert schema['properties']['checks']['minItems'] == len(QUALITY_CHECKS)
            assert schema['properties']['checks']['maxItems'] == len(QUALITY_CHECKS)
            body = result(evidence_id)
            body['checks'] = [
                {'kind': kind, 'status': 'warning', 'detail': '仍需人工核验', 'task_ids': parent_ids}
                for kind in sorted(QUALITY_CHECKS)
            ]
            return body

    generate_role(Client(), role='quality', payload={
        'evidence': [{'evidence_link_id': evidence_id, 'task_id': acquisition_id, 'quote': '本期收入100亿元'}],
        'dependencies': [{'task_id': value} for value in parent_ids],
    })
