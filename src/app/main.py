import os
import datetime
import json
from pathlib import Path

from dotenv import load_dotenv

from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.settings import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import crud
from app.database import get_db, init_db
from app.schemas import (
    TranscriptProcessRequest, TranscriptProcessResponse,
    MeetingQueryRequest, MeetingQueryResponse,
    SentimentAnalysisRequest, SentimentAnalysisResponse,
    MeetingSummaryRequest, MeetingSummaryResponse,
    CrossMeetingAnalysisRequest, CrossMeetingAnalysisResponse,
    IntelligenceExtractionRequest, IntelligenceExtractionResponse,
    StructuredExtractionRequest, StructuredExtractionResponse,
    CalendarEventCreate, CalendarEventResponse
)
from app.services import (
    process_transcript, answer_query, analyze_transcript_sentiment,
    generate_summary, analyze_cross_meeting_query, extract_intelligence,
    extract_structured_report
)

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Initialize database tables on startup
    init_db()
    yield


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="Meeting intelligence platform — extract decisions, actions, and sentiment from transcripts",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse



@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


# Transcript processing endpoints
@app.post("/transcripts/process", response_model=TranscriptProcessResponse)
async def process_transcript_endpoint(request: TranscriptProcessRequest):
    """Process a meeting transcript and return structured metadata."""
    result = process_transcript(request.transcript)
    return result


# Intelligence Extraction endpoint
@app.post("/transcripts/extract", response_model=IntelligenceExtractionResponse)
async def extract_intelligence_endpoint(request: IntelligenceExtractionRequest):
    """Extract high-value, deduplicated, prioritized intelligence from a transcript."""
    processed = process_transcript(request.transcript)
    result = extract_intelligence(processed)
    return result


# Structured (evidence-anchored) extraction endpoints
@app.post("/transcripts/analyze", response_model=StructuredExtractionResponse)
async def analyze_transcript_endpoint(request: StructuredExtractionRequest):
    """Extract the structured report from a typed/pasted transcript.

    Returns only what the transcript explicitly supports; assignee and
    due_date are null unless the transcript states them.
    """
    return extract_structured_report(request.transcript)


# Transcript file extensions accepted by the upload endpoint
_TRANSCRIPT_SUFFIXES = (".txt", ".vtt", ".md")


@app.post("/transcripts/analyze/file", response_model=StructuredExtractionResponse)
async def analyze_transcript_file_endpoint(file: UploadFile = File(...)):
    """Extract the structured report from an uploaded transcript file."""
    filename = (file.filename or "").lower()
    if filename and not filename.endswith(_TRANSCRIPT_SUFFIXES):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{filename}'. Use .txt, .vtt or .md.",
        )

    raw_bytes = await file.read()
    transcript = raw_bytes.decode("utf-8", errors="replace")
    return extract_structured_report(transcript)


# Meeting Q&A endpoints
@app.post("/meetings/query", response_model=MeetingQueryResponse)
async def meeting_query_endpoint(request: MeetingQueryRequest, db: Session = Depends(get_db)):
    """Answer questions based on meeting transcript context, enriched with knowledge graph facts."""
    from app.services.knowledge_service import get_knowledge_facts_for_query, build_knowledge_evidence_context

    kg_facts = get_knowledge_facts_for_query(db, request.query)
    enhanced_context = list(request.context)
    if kg_facts:
        kg_context_chunk = build_knowledge_evidence_context(kg_facts)
        if kg_context_chunk:
            enhanced_context.insert(0, kg_context_chunk)

    result = answer_query(request.query, enhanced_context)

    # Append verified knowledge graph evidence quotes to source citations
    if kg_facts:
        existing_snippets = {
            s["snippet"] if isinstance(s, dict) else s.snippet
            for s in result.get("sources", [])
        }
        for f in kg_facts:
            ev = f.get("evidence")
            if ev and ev not in existing_snippets:
                meeting_label = (
                    f"Knowledge Graph (Meeting #{f['source_meeting_id']})"
                    if f.get("source_meeting_id")
                    else "Knowledge Graph"
                )
                result["sources"].append({
                    "meeting": meeting_label,
                    "snippet": ev
                })
                existing_snippets.add(ev)

    return result


# Sentiment Analysis endpoints
@app.post("/meetings/sentiment", response_model=SentimentAnalysisResponse)
async def sentiment_analysis_endpoint(request: SentimentAnalysisRequest):
    """Analyze sentiment and tone in a meeting transcript."""
    result = analyze_transcript_sentiment(request.transcript)
    return result


# Meeting Summary endpoints
@app.post("/meetings/summary", response_model=MeetingSummaryResponse)
async def meeting_summary_endpoint(request: MeetingSummaryRequest):
    """Generate a high-level summary of a meeting."""
    result = generate_summary(
        decisions=request.decisions,
        action_items=request.action_items,
        sentiment_timeline=request.sentiment_timeline,
        speaker_summary=request.speaker_summary
    )
    return result


# Cross-Meeting Analysis endpoints
@app.post("/meetings/cross-analyze", response_model=CrossMeetingAnalysisResponse)
async def cross_meeting_analysis_endpoint(request: CrossMeetingAnalysisRequest):
    """Answer complex queries requiring information from multiple meetings."""
    result = analyze_cross_meeting_query(
        query=request.query,
        meetings=request.meetings
    )
    return result


# Meeting Management
@app.get("/meetings", status_code=200)
async def list_meetings(db: Session = Depends(get_db)):
    """List all processed meetings."""
    from app.models import Meeting
    stmt = select(Meeting).order_by(Meeting.date.desc())
    meetings = list(db.execute(stmt).scalars().all())
    return meetings


@app.get("/meetings/{meeting_id}", status_code=200)
async def get_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """Get a single meeting by ID."""
    from app.models import Meeting
    meeting = db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@app.delete("/meetings/{meeting_id}", status_code=200)
async def delete_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """Delete a meeting and clean up associated calendar events."""
    from app.models import Meeting, CalendarEvent
    from sqlalchemy import delete as sa_delete
    meeting = db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    db.execute(sa_delete(CalendarEvent).where(CalendarEvent.source_meeting_id == meeting_id))
    db.delete(meeting)
    db.commit()
    return {"message": "Meeting deleted successfully", "id": meeting_id}


@app.post("/meetings", status_code=201)
async def save_meeting(data: dict, db: Session = Depends(get_db)):
    """Save a processed meeting, resolve completed calendar tasks, and auto-assign action items."""
    from app.models import Meeting, CalendarEvent
    from app.services.work_resolver import detect_work_resolutions
    from sqlalchemy import select as sa_select
    
    # 1. Normalize and Save Meeting
    raw_processed = data.get("processed_data", {})
    if isinstance(raw_processed, str):
        try:
            processed_data_dict = json.loads(raw_processed)
        except Exception:
            processed_data_dict = {}
    elif isinstance(raw_processed, dict):
        processed_data_dict = raw_processed
    else:
        processed_data_dict = {}

    raw_speakers = data.get("speakers", [])
    if isinstance(raw_speakers, str):
        try:
            speakers_list = json.loads(raw_speakers)
        except Exception:
            speakers_list = [raw_speakers] if raw_speakers else []
    elif isinstance(raw_speakers, list):
        speakers_list = raw_speakers
    else:
        speakers_list = []

    transcript_text = data.get("transcript", "")
    meeting_title = data.get("title", "Untitled Meeting")

    db_meeting = Meeting(
        title=meeting_title,
        word_count=data.get("word_count", 0),
        speakers=json.dumps(speakers_list),
        transcript=transcript_text,
        processed_data=json.dumps(processed_data_dict)
    )
    db.add(db_meeting)
    db.commit()
    db.refresh(db_meeting)

    # 2. Check for Resolutions against Existing Active Calendar Events
    active_events_query = sa_select(CalendarEvent).where(CalendarEvent.status == "ACTIVE")
    active_events = list(db.execute(active_events_query).scalars().all())

    active_event_dicts = [
        {
            "id": e.id,
            "title": e.title,
            "description": e.description,
            "owner": e.owner,
            "date": e.date,
            "priority": e.priority
        }
        for e in active_events
    ]

    decisions = processed_data_dict.get("decisions", [])
    action_items = processed_data_dict.get("action_items", [])

    detected_resolutions = detect_work_resolutions(
        active_events=active_event_dicts,
        meeting_title=meeting_title,
        decisions=decisions,
        transcript=transcript_text,
        raw_action_items=action_items
    )

    resolved_event_ids = set()
    for res in detected_resolutions:
        event_id = res["event_id"]
        crud.resolve_calendar_event(
            db=db,
            event_id=event_id,
            reason=res["reason"],
            status=res["status"]
        )
        resolved_event_ids.add(event_id)

    # 3. Auto-assign Action Items to Calendar
    for item in action_items:
        deadline = item.get("deadline") or item.get("when")
        if not deadline or str(deadline).lower() in ["tbd", "unknown", "none", "-"]:
            deadline = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        
        # Clean any [UNRESOLVED] prefix from calendar date
        clean_deadline = str(deadline).replace("[UNRESOLVED]", "").strip()
        if not clean_deadline:
            clean_deadline = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")

        prio = str(item.get("priority") or "MEDIUM").strip().upper()
        if prio not in ["HIGH", "MEDIUM", "LOW"]:
            prio = "MEDIUM"

        event_data = CalendarEventCreate(
            title=(item.get("task") or item.get("what") or "Unnamed Task").strip() or "Unnamed Task",
            description=f"Action item from meeting: {db_meeting.title}",
            date=clean_deadline,
            owner=(item.get("owner") or item.get("who") or "Unassigned").strip() or "Unassigned",
            priority=prio,
            source_meeting_id=db_meeting.id,
            status="ACTIVE"
        )
        crud.create_calendar_event(db, event_data)

    # 4. Ingest into Relational Knowledge Layer
    from app.services.knowledge_service import sync_meeting_to_knowledge_graph
    from app.services.structured_extractor import extract_structured_report
    structured_report = extract_structured_report(transcript_text)

    sync_meeting_to_knowledge_graph(
        db=db,
        meeting_id=db_meeting.id,
        meeting_title=meeting_title,
        speakers=speakers_list,
        structured_report=structured_report,
        resolutions=detected_resolutions
    )

    return {
        "meeting": {
            "id": db_meeting.id,
            "title": db_meeting.title,
            "date": db_meeting.date.isoformat() if hasattr(db_meeting.date, "isoformat") else str(db_meeting.date or ""),
            "word_count": db_meeting.word_count,
            "speakers": db_meeting.speakers,
            "transcript": db_meeting.transcript,
            "processed_data": db_meeting.processed_data,
        },
        "resolutions": detected_resolutions
    }


# Knowledge Graph Endpoints
@app.get("/knowledge/entities")
async def list_entities(type: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    """List knowledge entities with their connection counts."""
    from app.services.knowledge_service import list_knowledge_entities
    return list_knowledge_entities(db=db, entity_type=type, limit=limit)


@app.get("/knowledge/entity/{entity_id}")
async def get_entity_connections(entity_id: int, db: Session = Depends(get_db)):
    """Get an entity with all its connected neighbors and verbatim evidence."""
    from app.services.knowledge_service import get_connected_knowledge
    result = get_connected_knowledge(db=db, entity_id=entity_id)
    if not result:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


# Calendar Endpoints
@app.get("/calendar", response_model=list[CalendarEventResponse])
async def list_calendar_events(
    skip: int = 0, 
    limit: int = 100, 
    include_resolved: bool = True,
    db: Session = Depends(get_db)
):
    """List all upcoming work/calendar events."""
    return crud.get_calendar_events(db, skip=skip, limit=limit, include_resolved=include_resolved)


@app.post("/calendar", response_model=CalendarEventResponse, status_code=201)
async def create_calendar_event(event: CalendarEventCreate, db: Session = Depends(get_db)):
    """Manually add an event to the calendar."""
    return crud.create_calendar_event(db, event)


@app.patch("/calendar/{event_id}/resolve", response_model=CalendarEventResponse)
async def resolve_calendar_event(
    event_id: int, 
    body: dict | None = None, 
    db: Session = Depends(get_db)
):
    """Resolve an event with explicit reasoning."""
    body = body or {}
    reason = body.get("reason", "Resolved based on meeting update.")
    status = body.get("status", "RESOLVED")
    updated = crud.resolve_calendar_event(db, event_id, reason=reason, status=status)
    if not updated:
        raise HTTPException(status_code=404, detail="Event not found")
    return updated


@app.delete("/calendar/{event_id}", status_code=200)
async def remove_calendar_event(event_id: int, db: Session = Depends(get_db)):
    """Delete a calendar event."""
    deleted = crud.delete_calendar_event(db, event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"message": "Event deleted successfully", "id": event_id}


@app.post("/calendar/sync", status_code=200)
async def sync_calendar_from_meetings(db: Session = Depends(get_db)):
    """Rebuild calendar events from all saved meetings."""
    from app.models import Meeting, CalendarEvent
    from sqlalchemy import select as sa_select

    meetings = list(db.execute(sa_select(Meeting)).scalars().all())
    created = 0

    for meeting in meetings:
        # Check if this meeting already has calendar events
        existing = db.execute(
            sa_select(CalendarEvent).where(CalendarEvent.source_meeting_id == meeting.id)
        ).scalars().first()
        if existing:
            continue  # already synced

        try:
            processed_data_dict = json.loads(meeting.processed_data or "{}")
        except (json.JSONDecodeError, TypeError):
            continue

        action_items = processed_data_dict.get("action_items", [])
        for item in action_items:
            deadline = item.get("deadline") or item.get("when")
            if not deadline or str(deadline).lower() in ["tbd", "unknown", "none", "-"]:
                deadline = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")

            clean_deadline = str(deadline).replace("[UNRESOLVED]", "").strip()
            if not clean_deadline:
                clean_deadline = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d")

            prio = str(item.get("priority") or "MEDIUM").strip().upper()
            if prio not in ["HIGH", "MEDIUM", "LOW"]:
                prio = "MEDIUM"

            event_data = CalendarEventCreate(
                title=(item.get("task") or item.get("what") or "Unnamed Task").strip() or "Unnamed Task",
                description=f"Action item from meeting: {meeting.title}",
                date=clean_deadline,
                owner=(item.get("owner") or item.get("who") or "Unassigned").strip() or "Unassigned",
                priority=prio,
                source_meeting_id=meeting.id
            )
            crud.create_calendar_event(db, event_data)
            created += 1

    return {"synced_meetings": len(meetings), "events_created": created}

# Mount the static frontend. Resolved from the package location so the app
# boots regardless of the working directory (e.g. `cd src && uvicorn ...`).
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {"name": settings.app_name, "status": "healthy", "docs": "/docs"}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
