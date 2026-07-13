"""Job API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.job import JobStatus
from tests.conftest import set_job_status


def test_create_job(client: TestClient) -> None:
    response = client.post("/api/jobs")
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["status"] == JobStatus.DRAFT.value
    assert body["progress_percent"] == 0


def test_job_defaults_to_draft(client: TestClient) -> None:
    body = client.post("/api/jobs").json()
    assert body["status"] == "DRAFT"


def test_list_jobs_newest_first(client: TestClient) -> None:
    ids = []
    for _ in range(3):
        ids.append(client.post("/api/jobs").json()["id"])
    response = client.get("/api/jobs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    listed_ids = [item["id"] for item in body["items"]]
    assert listed_ids == list(reversed(ids))


def test_get_job(client: TestClient) -> None:
    created = client.post("/api/jobs").json()
    response = client.get(f"/api/jobs/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get("/api/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_delete_draft_job(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    app.state.storage.job_directory(job_id)
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_delete_completed_job(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.COMPLETED)
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200


def test_delete_failed_job(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.FAILED)
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200


def test_active_job_cannot_be_deleted(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.PROMPT_GENERATING)
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 409
    assert client.get(f"/api/jobs/{job_id}").status_code == 200
