"""Integration tests for all backend routes and API contracts."""

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

@pytest.fixture
def client():
    """Create test client with fresh in-memory SQLite database."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_and_root(client):
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json() == {"status": "healthy"}

    r_root = client.get("/")
    assert r_root.status_code == 200


def test_transcripts_processing_pipeline(client):
    transcript = "Alice: We will launch the new UI on Friday.\nBob: I will update docs."
    
    # Process
    r_proc = client.post("/transcripts/process", json={"transcript": transcript})
    assert r_proc.status_code == 200
    pdata = r_proc.json()
    assert "clean_text" in pdata
    assert pdata["metadata"]["word_count"] > 0

    # Extract
    r_ext = client.post("/transcripts/extract", json={"transcript": transcript})
    assert r_ext.status_code == 200
    edata = r_ext.json()
    assert "decisions" in edata
    assert "action_items" in edata

    # Sentiment
    r_sent = client.post("/meetings/sentiment", json={"transcript": pdata["clean_text"]})
    assert r_sent.status_code == 200
    sdata = r_sent.json()
    assert "timeline" in sdata

    # Summary with deadlines
    r_sum = client.post("/meetings/summary", json={
        "decisions": edata["decisions"],
        "action_items": edata["action_items"],
        "sentiment_timeline": sdata["timeline"],
        "speaker_summary": sdata["speaker_summary"]
    })
    assert r_sum.status_code == 200
    assert "summary" in r_sum.json()


def test_meetings_and_calendar_lifecycle(client):
    # Save meeting
    meeting_payload = {
        "title": "Release Sprint",
        "word_count": 25,
        "speakers": ["Alice", "Bob"],
        "transcript": "Alice: Bob will finish testing by 2026-11-01.",
        "processed_data": {
            "decisions": [{"text": "Ship on Monday", "priority": "HIGH", "confidence": 0.9}],
            "action_items": [
                {"task": "Finish testing", "owner": "Bob", "deadline": "2026-11-01", "priority": "HIGH"}
            ]
        }
    }
    r_save = client.post("/meetings", json=meeting_payload)
    assert r_save.status_code == 201
    save_resp = r_save.json()
    meeting_id = save_resp["meeting"]["id"]

    # Get single meeting
    r_get = client.get(f"/meetings/{meeting_id}")
    assert r_get.status_code == 200
    assert r_get.json()["title"] == "Release Sprint"

    # List meetings
    r_list = client.get("/meetings")
    assert r_list.status_code == 200
    assert len(r_list.json()) == 1

    # Verify calendar auto-assignment
    r_cal = client.get("/calendar")
    assert r_cal.status_code == 200
    cal_events = r_cal.json()
    assert len(cal_events) == 1
    event_id = cal_events[0]["id"]
    assert cal_events[0]["title"] == "Finish testing"
    assert cal_events[0]["owner"] == "Bob"
    assert cal_events[0]["status"] == "ACTIVE"

    # Resolve event
    r_res = client.patch(f"/calendar/{event_id}/resolve", json={"reason": "Testing complete"})
    assert r_res.status_code == 200
    assert r_res.json()["status"] == "RESOLVED"

    # Resolve event without body
    r_res2 = client.patch(f"/calendar/{event_id}/resolve")
    assert r_res2.status_code == 200

    # Delete calendar event
    r_del_cal = client.delete(f"/calendar/{event_id}")
    assert r_del_cal.status_code == 200

    # Delete meeting
    r_del = client.delete(f"/meetings/{meeting_id}")
    assert r_del.status_code == 200

    # Verify not found
    r_get404 = client.get(f"/meetings/{meeting_id}")
    assert r_get404.status_code == 404


def test_calendar_sync(client):
    # Save a meeting directly
    meeting_payload = {
        "title": "Strategy Sync",
        "word_count": 10,
        "speakers": ["Dana"],
        "transcript": "Dana: I will deploy the server.",
        "processed_data": {
            "decisions": [],
            "action_items": [
                {"what": "Deploy the server", "who": "Dana", "when": "Friday", "priority": "medium"}
            ]
        }
    }
    client.post("/meetings", json=meeting_payload)

    # Sync calendar
    r_sync = client.post("/calendar/sync")
    assert r_sync.status_code == 200
    data = r_sync.json()
    assert "synced_meetings" in data


def test_cross_meeting_analyzer_iso_dates(client):
    meetings_data = [
        {
            "meeting_id": 1,
            "title": "Meeting A",
            "date": "2026-10-15T10:00:00",
            "decisions": [{"text": "Launch mobile app"}],
            "action_items": [{"owner": "Alice", "task": "Design UI"}]
        },
        {
            "meeting_id": 2,
            "title": "Meeting B",
            "date": "2026-10-25T14:30:00",
            "decisions": [{"text": "Delay mobile app"}],
            "action_items": [{"owner": "Bob", "task": "QA tests"}]
        }
    ]
    r = client.post("/meetings/cross-analyze", json={
        "query": "What is happening with the mobile app?",
        "meetings": meetings_data
    })
    assert r.status_code == 200
    res = r.json()
    assert "answer" in res
    assert "cross_meeting_insights" in res
