"""Tests for the Knowledge Graph & Relationship Service."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.services.knowledge_service import (
    sync_meeting_to_knowledge_graph,
    get_connected_knowledge,
    list_knowledge_entities,
    get_or_create_entity,
    create_relationship,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_sync_meeting_populates_entities_and_relationships(db_session):
    structured_report = {
        "meeting_title": "Q3 Launch Sync",
        "summary": "Priya and Marcus discussed the launch.",
        "decisions": [
            {
                "decision": "Deploy to production on Friday",
                "evidence": "Marcus: We agreed to deploy to production on Friday."
            }
        ],
        "tasks": [
            {
                "title": "Draft release notes",
                "description": "Priya will draft release notes",
                "assignee": "Priya",
                "due_date": "2026-10-15",
                "evidence": "Marcus: Priya will draft release notes by tomorrow."
            }
        ],
        "blockers": [
            {
                "issue": "Legal sign-off pending",
                "evidence": "Priya: I am blocked waiting for legal sign-off."
            }
        ]
    }

    speakers = ["Priya", "Marcus"]
    counts = sync_meeting_to_knowledge_graph(
        db=db_session,
        meeting_id=1,
        meeting_title="Q3 Launch Sync",
        speakers=speakers,
        structured_report=structured_report
    )

    assert counts["relationships"] > 0

    entities = list_knowledge_entities(db_session)
    entity_names = {e["name"] for e in entities}
    entity_types = {e["type"] for e in entities}

    # Verify all expected entities are created
    assert "Q3 Launch Sync" in entity_names
    assert "Priya" in entity_names
    assert "Marcus" in entity_names
    assert "Deploy to production on Friday" in entity_names
    assert "Draft release notes" in entity_names
    assert "Legal sign-off pending" in entity_names

    assert {"MEETING", "PERSON", "DECISION", "TASK", "BLOCKER"}.issubset(entity_types)


def test_get_connected_knowledge_with_evidence(db_session):
    structured_report = {
        "meeting_title": "Sprint Planning",
        "summary": "Team planned sprint.",
        "decisions": [],
        "tasks": [
            {
                "title": "Setup database migrations",
                "assignee": "Dana",
                "evidence": "Dana: I will setup database migrations."
            }
        ],
        "blockers": []
    }

    sync_meeting_to_knowledge_graph(
        db=db_session,
        meeting_id=2,
        meeting_title="Sprint Planning",
        speakers=["Dana"],
        structured_report=structured_report
    )

    # Find the task entity
    entities = list_knowledge_entities(db_session, entity_type="TASK")
    assert len(entities) == 1
    task_id = entities[0]["id"]

    # Inspect connections
    graph = get_connected_knowledge(db_session, task_id)
    assert graph["entity"]["name"] == "Setup database migrations"
    assert graph["connections_count"] >= 2  # CREATED_IN meeting, OWNS by Dana

    # Verify evidence provenance
    evidence_list = [c["evidence"] for c in graph["connections"]]
    assert any("Dana: I will setup database migrations." in ev for ev in evidence_list if ev)


def test_work_resolution_links_resolving_meeting(db_session):
    # Meeting 1 creates a task
    report1 = {
        "meeting_title": "Meeting 1",
        "summary": "Initial sync",
        "decisions": [],
        "tasks": [{"title": "Configure SSL certificates", "evidence": "Bob: I will configure SSL."}],
        "blockers": []
    }
    sync_meeting_to_knowledge_graph(db_session, 1, "Meeting 1", ["Bob"], report1)

    # Meeting 2 resolves the task
    report2 = {
        "meeting_title": "Meeting 2",
        "summary": "Follow-up",
        "decisions": [],
        "tasks": [],
        "blockers": []
    }
    resolutions = [
        {
            "event_id": 99,
            "title": "Configure SSL certificates",
            "status": "RESOLVED",
            "reason": "Resolved in Meeting 2 based on update."
        }
    ]
    sync_meeting_to_knowledge_graph(db_session, 2, "Meeting 2", ["Bob"], report2, resolutions=resolutions)

    # Check task connections
    tasks = list_knowledge_entities(db_session, entity_type="TASK")
    task_id = tasks[0]["id"]
    graph = get_connected_knowledge(db_session, task_id)

    rel_types = [c["relation_type"] for c in graph["connections"]]
    assert "RESOLVES" in rel_types


def test_knowledge_api_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # Ingest a meeting through the official API
    payload = {
        "title": "API Knowledge Test Meeting",
        "speakers": ["Alice", "Bob"],
        "word_count": 50,
        "transcript": "Meeting: API Knowledge Test Meeting\nAlice: We agreed to ship the mobile beta on Friday.\nBob: I will test the login flow.",
        "processed_data": {
            "decisions": [{"text": "Ship the mobile beta on Friday", "priority": "HIGH", "confidence": 0.9}],
            "action_items": [{"task": "Test the login flow", "owner": "Bob", "priority": "MEDIUM", "confidence": 0.85, "deadline": "2026-10-30"}]
        }
    }

    res = client.post("/meetings", json=payload)
    assert res.status_code == 201

    # Query entities endpoint
    entities_res = client.get("/knowledge/entities")
    assert entities_res.status_code == 200
    entities = entities_res.json()
    assert len(entities) > 0

    # Filter by PERSON
    people_res = client.get("/knowledge/entities?type=PERSON")
    assert people_res.status_code == 200
    people = people_res.json()
    names = [p["name"] for p in people]
    assert "Alice" in names or "Bob" in names

    # Query connected knowledge for first entity
    first_id = entities[0]["id"]
    conn_res = client.get(f"/knowledge/entity/{first_id}")
    assert conn_res.status_code == 200
    conn_data = conn_res.json()
    assert "entity" in conn_data
    assert "connections" in conn_data

    # Test Q&A integration with knowledge graph citations
    qa_res = client.post("/meetings/query", json={
        "query": "Who is testing the login flow?",
        "context": ["Alice: We agreed to ship the mobile beta on Friday."]
    })
    assert qa_res.status_code == 200
    qa_data = qa_res.json()
    assert "answer" in qa_data
    assert "sources" in qa_data
    # Should contain Knowledge Graph evidence in sources
    source_snippets = [s["snippet"] for s in qa_data["sources"]]
    assert any("Bob" in s or "login" in s for s in source_snippets)


def test_get_knowledge_facts_for_query(db_session):
    from app.services.knowledge_service import (
        get_knowledge_facts_for_query,
        build_knowledge_evidence_context,
    )

    report = {
        "meeting_title": "Architecture Review",
        "summary": "Team reviewed architecture.",
        "decisions": [
            {
                "decision": "Adopt PostgreSQL for primary persistence",
                "evidence": "Sarah: We decided to adopt PostgreSQL for primary persistence."
            }
        ],
        "tasks": [],
        "blockers": []
    }
    sync_meeting_to_knowledge_graph(db_session, 10, "Architecture Review", ["Sarah"], report)

    facts = get_knowledge_facts_for_query(db_session, "What was decided about PostgreSQL?")
    assert len(facts) >= 1
    assert facts[0]["relation_type"] == "DECIDED_IN"
    assert "PostgreSQL" in facts[0]["source"]["name"]
    assert "Sarah: We decided to adopt PostgreSQL" in facts[0]["evidence"]

    context_str = build_knowledge_evidence_context(facts)
    assert "Verified Knowledge Graph Facts:" in context_str
    assert "PostgreSQL" in context_str


