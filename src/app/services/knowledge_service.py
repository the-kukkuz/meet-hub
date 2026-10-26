"""Knowledge Graph & Relationship Service.

Extracts typed entities and directed relationships with verbatim evidence from meeting data,
populating the relational knowledge layer (KnowledgeEntity and KnowledgeRelationship).
"""

import json
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_

from app.models import KnowledgeEntity, KnowledgeRelationship, Meeting


def normalize_name(name: str) -> str:
    """Canonicalize entity name for resolution (lowercase, stripped)."""
    return " ".join((name or "").strip().lower().split())


def get_or_create_entity(
    db: Session,
    entity_type: str,
    name: str,
    source_meeting_id: Optional[int] = None,
    description: Optional[str] = None,
    metadata: Optional[dict] = None
) -> KnowledgeEntity:
    """Find existing entity by canonical name and type, or create a new one."""
    canon = normalize_name(name)
    stmt = select(KnowledgeEntity).where(
        KnowledgeEntity.entity_type == entity_type,
        KnowledgeEntity.canonical_name == canon
    )
    existing = db.execute(stmt).scalars().first()
    if existing:
        return existing

    new_entity = KnowledgeEntity(
        entity_type=entity_type,
        name=name.strip(),
        canonical_name=canon,
        description=description,
        source_meeting_id=source_meeting_id,
        metadata_json=json.dumps(metadata or {})
    )
    db.add(new_entity)
    db.flush()
    return new_entity


def create_relationship(
    db: Session,
    source_id: int,
    target_id: int,
    relation_type: str,
    evidence: Optional[str] = None,
    source_meeting_id: Optional[int] = None
) -> KnowledgeRelationship:
    """Create a directed relationship edge with supporting evidence."""
    stmt = select(KnowledgeRelationship).where(
        KnowledgeRelationship.source_id == source_id,
        KnowledgeRelationship.target_id == target_id,
        KnowledgeRelationship.relation_type == relation_type
    )
    existing = db.execute(stmt).scalars().first()
    if existing:
        return existing

    rel = KnowledgeRelationship(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        evidence=evidence,
        source_meeting_id=source_meeting_id
    )
    db.add(rel)
    db.flush()
    return rel


def sync_meeting_to_knowledge_graph(
    db: Session,
    meeting_id: int,
    meeting_title: str,
    speakers: list[str],
    structured_report: dict,
    resolutions: Optional[list[dict]] = None
) -> dict:
    """Ingest a meeting and its structured report into the relational knowledge layer.

    Populates:
    - MEETING entity
    - PERSON entities + PARTICIPATED_IN relations
    - DECISION entities + DECIDED_IN relations
    - TASK entities + CREATED_IN relations + OWNS relations
    - BLOCKER entities + RAISED_IN relations
    - RESOLVES relations from work_resolver outputs
    """
    # 1. Meeting Node
    meeting_entity = get_or_create_entity(
        db=db,
        entity_type="MEETING",
        name=meeting_title,
        source_meeting_id=meeting_id,
        description=structured_report.get("summary")
    )

    created_counts = {"entities": 0, "relationships": 0}

    # 2. People & Attendance
    person_entities = {}
    for speaker in speakers:
        if not speaker or speaker.lower() in {"unknown", "unassigned"}:
            continue
        person = get_or_create_entity(
            db=db,
            entity_type="PERSON",
            name=speaker,
            source_meeting_id=meeting_id
        )
        person_entities[normalize_name(speaker)] = person
        create_relationship(
            db=db,
            source_id=person.id,
            target_id=meeting_entity.id,
            relation_type="PARTICIPATED_IN",
            evidence=f"{speaker} attended {meeting_title}",
            source_meeting_id=meeting_id
        )
        created_counts["relationships"] += 1

    # 3. Decisions
    for dec in structured_report.get("decisions", []):
        dec_text = dec.get("decision", "")
        if not dec_text:
            continue
        evidence = dec.get("evidence", "")
        dec_entity = get_or_create_entity(
            db=db,
            entity_type="DECISION",
            name=dec_text,
            source_meeting_id=meeting_id,
            description=evidence
        )
        create_relationship(
            db=db,
            source_id=dec_entity.id,
            target_id=meeting_entity.id,
            relation_type="DECIDED_IN",
            evidence=evidence,
            source_meeting_id=meeting_id
        )
        created_counts["relationships"] += 1

    # 4. Tasks & Ownership
    for task in structured_report.get("tasks", []):
        task_title = task.get("title") or task.get("task", "")
        if not task_title:
            continue
        evidence = task.get("evidence", "")
        assignee = task.get("assignee") or task.get("owner")
        due_date = task.get("due_date") or task.get("deadline")

        task_entity = get_or_create_entity(
            db=db,
            entity_type="TASK",
            name=task_title,
            source_meeting_id=meeting_id,
            description=task.get("description"),
            metadata={"due_date": due_date, "assignee": assignee}
        )

        create_relationship(
            db=db,
            source_id=task_entity.id,
            target_id=meeting_entity.id,
            relation_type="CREATED_IN",
            evidence=evidence,
            source_meeting_id=meeting_id
        )
        created_counts["relationships"] += 1

        if assignee and normalize_name(assignee) in person_entities:
            owner_person = person_entities[normalize_name(assignee)]
            create_relationship(
                db=db,
                source_id=owner_person.id,
                target_id=task_entity.id,
                relation_type="OWNS",
                evidence=evidence,
                source_meeting_id=meeting_id
            )
            created_counts["relationships"] += 1

    # 5. Blockers & Risks
    for blocker in structured_report.get("blockers", []):
        issue = blocker.get("issue", "")
        if not issue:
            continue
        evidence = blocker.get("evidence", "")
        blocker_entity = get_or_create_entity(
            db=db,
            entity_type="BLOCKER",
            name=issue,
            source_meeting_id=meeting_id,
            description=evidence
        )
        create_relationship(
            db=db,
            source_id=blocker_entity.id,
            target_id=meeting_entity.id,
            relation_type="RAISED_IN",
            evidence=evidence,
            source_meeting_id=meeting_id
        )
        created_counts["relationships"] += 1

    # 6. Work Resolutions
    if resolutions:
        for res in resolutions:
            res_title = res.get("title", "")
            # Find the corresponding task entity
            task_canon = normalize_name(res_title)
            stmt = select(KnowledgeEntity).where(
                KnowledgeEntity.entity_type == "TASK",
                KnowledgeEntity.canonical_name == task_canon
            )
            matched_task = db.execute(stmt).scalars().first()
            if matched_task:
                create_relationship(
                    db=db,
                    source_id=meeting_entity.id,
                    target_id=matched_task.id,
                    relation_type="RESOLVES" if res.get("status") == "RESOLVED" else "CANCELS",
                    evidence=res.get("reason"),
                    source_meeting_id=meeting_id
                )
                created_counts["relationships"] += 1

    db.commit()
    return created_counts


def get_connected_knowledge(db: Session, entity_id: int) -> dict:
    """Retrieve an entity and all its directly connected neighbors and evidence."""
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        return {}

    # Outgoing relationships
    out_stmt = select(KnowledgeRelationship).where(KnowledgeRelationship.source_id == entity_id)
    outgoing = list(db.execute(out_stmt).scalars().all())

    # Incoming relationships
    in_stmt = select(KnowledgeRelationship).where(KnowledgeRelationship.target_id == entity_id)
    incoming = list(db.execute(in_stmt).scalars().all())

    neighbor_ids = {r.target_id for r in outgoing} | {r.source_id for r in incoming}
    neighbors = {}
    if neighbor_ids:
        n_stmt = select(KnowledgeEntity).where(KnowledgeEntity.id.in_(neighbor_ids))
        for n in db.execute(n_stmt).scalars().all():
            neighbors[n.id] = {
                "id": n.id,
                "type": n.entity_type,
                "name": n.name,
                "description": n.description,
            }

    connections = []
    for r in outgoing:
        connections.append({
            "relation_id": r.id,
            "direction": "OUTGOING",
            "relation_type": r.relation_type,
            "connected_entity": neighbors.get(r.target_id),
            "evidence": r.evidence,
            "source_meeting_id": r.source_meeting_id,
        })

    for r in incoming:
        connections.append({
            "relation_id": r.id,
            "direction": "INCOMING",
            "relation_type": r.relation_type,
            "connected_entity": neighbors.get(r.source_id),
            "evidence": r.evidence,
            "source_meeting_id": r.source_meeting_id,
        })

    return {
        "entity": {
            "id": entity.id,
            "type": entity.entity_type,
            "name": entity.name,
            "description": entity.description,
            "source_meeting_id": entity.source_meeting_id,
            "metadata": json.loads(entity.metadata_json or "{}"),
        },
        "connections_count": len(connections),
        "connections": connections,
    }


def list_knowledge_entities(
    db: Session,
    entity_type: Optional[str] = None,
    limit: int = 100
) -> list[dict]:
    """List entities with their connection count."""
    stmt = select(KnowledgeEntity)
    if entity_type:
        stmt = stmt.where(KnowledgeEntity.entity_type == entity_type.upper())
    stmt = stmt.order_by(KnowledgeEntity.id.desc()).limit(limit)
    entities = list(db.execute(stmt).scalars().all())

    results = []
    for e in entities:
        out_stmt = select(func.count()).select_from(KnowledgeRelationship).where(KnowledgeRelationship.source_id == e.id)
        in_stmt = select(func.count()).select_from(KnowledgeRelationship).where(KnowledgeRelationship.target_id == e.id)
        out_count = db.scalar(out_stmt) or 0
        in_count = db.scalar(in_stmt) or 0
        results.append({
            "id": e.id,
            "type": e.entity_type,
            "name": e.name,
            "description": e.description,
            "source_meeting_id": e.source_meeting_id,
            "connection_count": out_count + in_count,
        })
    return results


def get_knowledge_facts_for_query(
    db: Session,
    query: str,
    max_entities: int = 5
) -> list[dict]:
    """Retrieve verified relational facts and evidence quotes for entities matching query terms."""
    if not query or not query.strip():
        return []

    import re
    stop_words = {
        "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
        "the", "and", "for", "with", "that", "this", "from", "will", "shall",
        "must", "should", "can", "our", "your", "their", "are", "was", "were",
        "been", "have", "has", "had", "did", "does", "about", "any", "all"
    }
    raw_tokens = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9\.\-']{3,}\b", query)]
    tokens = [t for t in raw_tokens if t not in stop_words]
    if not tokens:
        tokens = raw_tokens

    if not tokens:
        return []

    conditions = [KnowledgeEntity.canonical_name.like(f"%{tok}%") for tok in tokens[:8]]
    stmt = select(KnowledgeEntity).where(or_(*conditions)).limit(max_entities)
    matched_entities = list(db.execute(stmt).scalars().all())

    if not matched_entities:
        return []

    entity_ids = {e.id for e in matched_entities}
    rel_stmt = select(KnowledgeRelationship).where(
        or_(
            KnowledgeRelationship.source_id.in_(entity_ids),
            KnowledgeRelationship.target_id.in_(entity_ids)
        )
    ).limit(20)
    relationships = list(db.execute(rel_stmt).scalars().all())

    if not relationships:
        return []

    all_entity_ids = entity_ids.union({r.source_id for r in relationships}, {r.target_id for r in relationships})
    e_stmt = select(KnowledgeEntity).where(KnowledgeEntity.id.in_(all_entity_ids))
    entity_map = {e.id: e for e in db.execute(e_stmt).scalars().all()}

    facts = []
    seen = set()
    for r in relationships:
        if r.id in seen:
            continue
        seen.add(r.id)
        src = entity_map.get(r.source_id)
        tgt = entity_map.get(r.target_id)
        if not src or not tgt:
            continue
        facts.append({
            "relation_id": r.id,
            "source": {"id": src.id, "type": src.entity_type, "name": src.name},
            "relation_type": r.relation_type,
            "target": {"id": tgt.id, "type": tgt.entity_type, "name": tgt.name},
            "evidence": r.evidence,
            "source_meeting_id": r.source_meeting_id
        })

    return facts


def build_knowledge_evidence_context(facts: list[dict]) -> str:
    """Format verified knowledge facts into a grounded context block for Q&A."""
    if not facts:
        return ""

    lines = ["Verified Knowledge Graph Facts:"]
    for f in facts:
        src = f["source"]
        tgt = f["target"]
        rel = f["relation_type"]
        ev = f.get("evidence")
        line = f"- {src['name']} ({src['type']}) {rel} {tgt['name']} ({tgt['type']})"
        if ev:
            line += f' | Evidence: "{ev}"'
        lines.append(line)
    return "\n".join(lines)
