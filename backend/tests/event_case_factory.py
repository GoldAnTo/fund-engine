"""Create admitted cases through the public pasted-source intake API."""


def create_event_case(client, *, title="资本开支研究", raw_input="用户提供的研究原文：公司本季增加资本开支，订单收入的确认仍需后续核验。") -> str:
    response = client.post(
        "/api/v1/event-research",
        json={
            "raw_input": raw_input,
            "source_type": "pasted_snapshot",
            "event_title": title,
            "research_question": "资本开支能否通过订单转化为收入？",
            "candidate_factors": ["资本开支节奏", "订单转化", "收入确认"],
            "research_protocol_required": False,
            "created_by": "analyst-test",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["case_id"]
