"""Bound ORM hydration to one event page, regardless of historical event count."""
import uuid

from sqlalchemy import event

from app.models.operational import JobEvent
from app.repositories.operational import JobRepository
from app.services.jobs import JobService
from tests.event_case_factory import create_event_case


def test_event_pages_have_bounded_hydration_and_no_gaps(cmd_client, cmd_session):
    case_id = uuid.UUID(create_event_case(cmd_client))
    job = JobService(cmd_session).create(kind="propose", research_case_id=case_id)
    job_id = job.id
    repository = JobRepository(cmd_session)
    for seq in range(1, 1004):
        repository.append_event(job_id=job_id, seq=seq, status="running", message=f"event {seq}")
    cmd_session.commit()
    cmd_session.expunge_all()
    loaded = []
    def record_load(instance, context):
        loaded.append(instance.seq)
    event.listen(JobEvent, "load", record_load)
    try:
        response = cmd_client.get(f"/api/v1/jobs/{job_id}/events", params={"limit": 3})
    finally:
        event.remove(JobEvent, "load", record_load)
    assert response.status_code == 200
    assert [row["seq"] for row in response.json()["events"]] == [1, 2, 3]
    assert response.json()["next_cursor"] == "3"
    assert response.json()["has_more"] is True
    assert len(loaded) <= 4, "a page must not hydrate all historical events"
    next_page = cmd_client.get(f"/api/v1/jobs/{job_id}/events", params={"limit": 3, "after_seq": 3}).json()
    assert [row["seq"] for row in next_page["events"]] == [4, 5, 6]
    last = cmd_client.get(f"/api/v1/jobs/{job_id}/events", params={"limit": 3, "after_seq": 1000}).json()
    assert [row["seq"] for row in last["events"]] == [1001, 1002, 1003]
    assert last["has_more"] is False and last["next_cursor"] is None
    empty = cmd_client.get(f"/api/v1/jobs/{job_id}/events", params={"after_seq": 1003}).json()
    assert empty["events"] == [] and empty["has_more"] is False
