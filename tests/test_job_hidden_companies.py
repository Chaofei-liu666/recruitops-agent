"""Hidden-company filtering for the job list.

The desktop workbench gets a fresh loopback origin on every launch, so this
preference is persisted by the API instead of browser storage.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from packages.job_listing_preferences import (
    JobListingPreferenceError,
    read_hidden_companies,
    write_hidden_companies,
)
from packages.repositories.postgres import PostgresRecruitmentRepository
from packages.storage import Storage
from packages.storage.models import CompanySnapshot, JobSnapshot

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _repository(tmp_path) -> PostgresRecruitmentRepository:
    storage = Storage.from_url(f"sqlite:///{tmp_path / 'hidden-companies.db'}", initialize=True)
    with storage.engine.begin() as connection:
        connection.execute(CompanySnapshot.__table__.insert(), [
            {"id": "alpha", "name": "Alpha Tech", "organization_id": "org-alpha",
             "integration_status": "connected", "source": "fixture"},
            {"id": "beta", "name": "Beta Games", "organization_id": "org-beta",
             "integration_status": "connected", "source": "fixture"},
            # No organization id: hiding another company must not hide this row too.
            {"id": "gamma", "name": "Gamma Labs", "organization_id": None,
             "integration_status": "connected", "source": "fixture"},
        ])
        connection.execute(JobSnapshot.__table__.insert(), [
            {"id": "job-alpha", "company_id": "alpha", "title": "C++ 软件工程师",
             "city": "上海", "detail_url": "https://example.test/jobs/alpha", "cohort": 2027,
             "cohort_status": "confirmed", "batch": "formal", "capture_status": "complete",
             "match_score": 91, "first_seen_at": NOW, "source": "fixture",
             "source_ref": "job:alpha"},
            {"id": "job-beta", "company_id": "beta", "title": "游戏客户端工程师",
             "city": "广州", "detail_url": "https://example.test/jobs/beta", "cohort": 2027,
             "cohort_status": "confirmed", "batch": "formal", "capture_status": "complete",
             "match_score": 64, "first_seen_at": NOW, "source": "fixture",
             "source_ref": "job:beta"},
            {"id": "job-gamma", "company_id": "gamma", "title": "算法工程师",
             "city": "北京", "detail_url": "https://example.test/jobs/gamma", "cohort": 2027,
             "cohort_status": "confirmed", "batch": "early", "capture_status": "complete",
             "match_score": None, "first_seen_at": NOW, "source": "fixture",
             "source_ref": "job:gamma"},
        ])
    return PostgresRecruitmentRepository(storage)


def test_hidden_company_is_accepted_as_facet_key_or_company_id(tmp_path):
    repository = _repository(tmp_path)
    assert repository.browse_jobs(limit=50, include_summary=False).total == 3

    for key in ("alpha", "org-alpha", "Alpha Tech"):
        page = repository.browse_jobs(limit=50, include_summary=False, hidden_companies=[key])
        assert page.total == 2, key
        assert {item.company_id for item in page.items} == {"beta", "gamma"}, key


def test_hiding_one_company_keeps_rows_without_organization(tmp_path):
    repository = _repository(tmp_path)
    page = repository.browse_jobs(limit=50, include_summary=False,
                                  hidden_companies=["org-alpha", "alpha"])
    assert page.total == 2
    assert "job-gamma" in {item.id for item in page.items}


def test_hidden_companies_also_leave_the_featured_strip(tmp_path):
    repository = _repository(tmp_path)
    visible = repository.browse_jobs(limit=50, hidden_companies=[])
    assert [job.id for job in visible.featured] == ["job-alpha"]
    assert visible.total == 3

    hidden = repository.browse_jobs(limit=50, hidden_companies=["alpha"])
    assert hidden.featured == []
    assert hidden.total == 2


def test_hidden_companies_are_normalized_and_deduplicated(tmp_path):
    repository = _repository(tmp_path)
    page = repository.browse_jobs(limit=50, include_summary=False,
                                  hidden_companies=["  ALPHA  ", "", "alpha"])
    assert page.total == 2


def _settings(tmp_path, *, write_enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(agent_root=tmp_path, write_enabled=write_enabled)


def test_hidden_company_preference_round_trip(tmp_path):
    settings = _settings(tmp_path)
    assert read_hidden_companies(settings) == []

    saved = write_hidden_companies(settings, [" Alpha ", "beta", "ALPHA", ""])
    assert saved == ["alpha", "beta"]
    assert read_hidden_companies(settings) == ["alpha", "beta"]
    assert (tmp_path / ".data" / "settings" / "job_listing_filters.json").is_file()


def test_hidden_company_preference_rejects_bad_payloads(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(JobListingPreferenceError):
        write_hidden_companies(settings, "alpha")
    with pytest.raises(JobListingPreferenceError):
        write_hidden_companies(settings, [1, 2])


def test_unreadable_preference_file_reads_as_empty(tmp_path):
    settings = _settings(tmp_path)
    path = tmp_path / ".data" / "settings" / "job_listing_filters.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert read_hidden_companies(settings) == []
    path.write_text(json.dumps({"schema_version": 999, "hidden_companies": ["alpha"]}),
                    encoding="utf-8")
    assert read_hidden_companies(settings) == []


def test_browse_endpoint_accepts_repeated_exclude_company(tmp_path):
    from apps.api import main as api

    repository = _repository(tmp_path)
    api.app.dependency_overrides[api.repository] = lambda: repository
    try:
        client = TestClient(api.app)
        response = client.get("/api/jobs/browse?limit=50&exclude_company=org-alpha")
    finally:
        api.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert {item["company_id"] for item in payload["items"]} == {"beta", "gamma"}


def test_preference_endpoints_persist_only_when_writes_are_enabled(tmp_path, monkeypatch):
    from apps.api import main as api

    settings = _settings(tmp_path, write_enabled=False)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    client = TestClient(api.app)

    assert client.get("/api/job-listing/hidden-companies").json() == {"hidden_companies": []}
    assert client.put(
        "/api/job-listing/hidden-companies", json={"hidden_companies": ["alpha"]}
    ).status_code == 503

    settings.write_enabled = True
    saved = client.put("/api/job-listing/hidden-companies",
                       json={"hidden_companies": [" Alpha ", "alpha"]})
    assert saved.status_code == 200
    assert saved.json() == {"hidden_companies": ["alpha"]}
    assert client.get("/api/job-listing/hidden-companies").json() == {"hidden_companies": ["alpha"]}
