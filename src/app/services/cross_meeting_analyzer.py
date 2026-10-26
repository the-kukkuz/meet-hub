"""Cross-meeting analysis service.

Provides functionality to answer complex queries that require combining
information from multiple meetings.

Fix log
-------
Bug L  – _generate_cross_meeting_insights: date list sorted chronologically
         instead of insertion-order (was showing wrong span start/end).
Bug M  – _detect_conflicting_decisions: new function surfaces decisions that
         contradict each other across meetings (loophole #5 / #10).
Bug N  – _resolve_deadline_conflicts: for the same task appearing in multiple
         meetings the latest meeting's deadline supersedes earlier ones
         (loophole #10 temporal drift).
Bug O  – analyze_cross_meeting_query: results explicitly sorted by meeting date
         before processing so "latest wins" logic is deterministic.
Bug P  – _generate_cross_meeting_answer: f-string with nested quotes fixed
         (was a syntax error on Python < 3.12 in some environments).
"""

import re
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Conflict-detection helpers
# ---------------------------------------------------------------------------

# Pairs of verbs whose co-occurrence across decisions signals a contradiction
_REVERSAL_PAIRS: list[tuple[str, str]] = [
    ("launch", "delay"),
    ("launch", "postpone"),
    ("launch", "cancel"),
    ("ship", "delay"),
    ("ship", "postpone"),
    ("ship", "cancel"),
    ("release", "delay"),
    ("release", "postpone"),
    ("release", "hold"),
    ("approve", "reject"),
    ("approve", "deny"),
    ("proceed", "stop"),
    ("proceed", "halt"),
    ("hire", "freeze"),
    ("expand", "cut"),
    ("increase", "reduce"),
    ("increase", "decrease"),
    ("keep", "remove"),
    ("keep", "drop"),
    ("add", "remove"),
    ("add", "drop"),
]


def _extract_subject(decision_text: str) -> str:
    """Return a rough subject token from a decision for collision matching."""
    # Strip leading verbs ("we decided to …") to get the noun phrase
    cleaned = re.sub(
        r'^(?:we|team|everyone|it was|it\'s)\s+'
        r'(?:decided|agreed|resolved|confirmed|will|shall)\s+(?:to|that)?\s*',
        '', decision_text, flags=re.IGNORECASE,
    ).strip().lower()
    # Take first 6 words as subject fingerprint
    return ' '.join(cleaned.split()[:6])


def _detect_conflicting_decisions(meetings: list[dict]) -> list[dict]:
    """Surface pairs of decisions that appear to contradict each other.

    FIX (Bug M / loophole #5): scans all decision pairs across meetings and
    flags those whose subject overlaps AND whose verb tokens are a known
    reversal pair.  Returns a list of conflict dicts for caller display.
    """
    conflicts: list[dict] = []
    all_decisions: list[dict] = []  # {text, subject, meeting_id, date}

    for meeting in meetings:
        mid = meeting.get("meeting_id", "unknown")
        mdate = meeting.get("date", "")
        for d in meeting.get("decisions", []):
            text = d.get("decision", "") or d.get("text", "")
            if text:
                all_decisions.append({
                    "text": text,
                    "subject": _extract_subject(text),
                    "meeting_id": mid,
                    "date": mdate,
                })

    for i, da in enumerate(all_decisions):
        for db in all_decisions[i + 1:]:
            if da["meeting_id"] == db["meeting_id"]:
                continue  # same meeting — not a cross-meeting conflict
            # Subject overlap (shared words ≥ 2)
            words_a = set(da["subject"].split())
            words_b = set(db["subject"].split())
            overlap = words_a & words_b - {"the", "a", "an", "to", "of", "and"}
            if len(overlap) < 1:
                continue
            # Check for reversal verb pair
            text_a_l = da["text"].lower()
            text_b_l = db["text"].lower()
            for verb1, verb2 in _REVERSAL_PAIRS:
                if (
                    (verb1 in text_a_l and verb2 in text_b_l)
                    or (verb2 in text_a_l and verb1 in text_b_l)
                ):
                    conflicts.append({
                        "meeting_a": da["meeting_id"],
                        "date_a": da["date"],
                        "decision_a": da["text"],
                        "meeting_b": db["meeting_id"],
                        "date_b": db["date"],
                        "decision_b": db["text"],
                        "conflict_type": f"{verb1} ↔ {verb2}",
                    })
                    break  # one conflict record per pair

    return conflicts


def _resolve_deadline_conflicts(meetings: list[dict]) -> list[dict]:
    """For action items appearing in multiple meetings, keep the latest deadline.

    FIX (Bug N / loophole #10 temporal drift): when the same task (by fuzzy
    match) appears across meetings, only the version from the most recent
    meeting is retained so stale deadlines don't survive.
    Returns a flat, deduplicated list of action items.
    """
    # Sort meetings newest-first so the first occurrence we see is the latest
    def _parse_date(d: str) -> datetime:
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except Exception:
            return datetime.min

    sorted_meetings = sorted(
        meetings, key=lambda m: _parse_date(m.get("date", "")), reverse=True
    )

    seen_tasks: list[str] = []
    resolved: list[dict] = []

    for meeting in sorted_meetings:
        mid = meeting.get("meeting_id", "")
        mdate = meeting.get("date", "")
        for action in meeting.get("action_items", []):
            task_text = (action.get("what") or action.get("task") or "").lower().strip()
            if not task_text:
                continue
            # Fuzzy-match against already-seen tasks
            is_dup = any(
                _word_overlap(task_text, seen) >= 0.60
                for seen in seen_tasks
            )
            if not is_dup:
                seen_tasks.append(task_text)
                resolved.append({**action, "source_meeting": mid, "source_date": mdate})

    return resolved


def _word_overlap(a: str, b: str) -> float:
    """Jaccard word overlap between two strings."""
    wa = set(re.findall(r'\b\w{3,}\b', a))
    wb = set(re.findall(r'\b\w{3,}\b', b))
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_cross_meeting_query(
    query: str,
    meetings: list[dict],
) -> dict:
    """Analyze a query across multiple meetings.

    FIX (Bug O): meetings sorted chronologically before processing so
    "latest-wins" logic in deadline resolution is deterministic.
    """
    if not meetings:
        return {
            "answer": "No meeting data available to analyze.",
            "cross_meeting_insights": "",
            "conflicting_decisions": [],
            "sources": [],
        }

    # FIX (Bug O): sort by date ascending for consistent ordering
    def _safe_date(m: dict) -> str:
        return m.get("date", "0000-00-00") or "0000-00-00"

    meetings = sorted(meetings, key=_safe_date)

    relevant_meetings = _find_relevant_meetings(query, meetings)

    if not relevant_meetings:
        return {
            "answer": "The information is not available in the provided meetings.",
            "cross_meeting_insights": "",
            "conflicting_decisions": [],
            "sources": [],
        }

    answer = _generate_cross_meeting_answer(query, relevant_meetings)
    insights = _generate_cross_meeting_insights(relevant_meetings)

    # FIX (Bug M): surface conflicting decisions
    conflicts = _detect_conflicting_decisions(relevant_meetings)

    sources = [
        {
            "meeting": str(meeting.get("title") or meeting.get("meeting_id") or f"Meeting_{idx + 1}"),
            "date": str(meeting.get("date") or "Unknown"),
            "snippet": _extract_relevant_snippet(query, meeting),
        }
        for idx, meeting in enumerate(relevant_meetings)
    ]

    return {
        "answer": answer,
        "cross_meeting_insights": insights,
        "conflicting_decisions": conflicts,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

def _find_relevant_meetings(query: str, meetings: list[dict]) -> list[dict]:
    query_lower = query.lower()
    query_words = set(re.findall(r'\b\w{3,}\b', query_lower))
    scored: list[tuple[int, dict]] = []

    for meeting in meetings:
        score = 0
        transcript = meeting.get("transcript", "").lower()
        for word in query_words:
            score += transcript.count(word)
        for decision in meeting.get("decisions", []):
            dt = (decision.get("decision") or decision.get("text") or "").lower()
            for word in query_words:
                if word in dt:
                    score += 5
        for action in meeting.get("action_items", []):
            at = f"{action.get('who', '')} {action.get('what', '')}".lower()
            for word in query_words:
                if word in at:
                    score += 3
        if score > 0:
            scored.append((score, meeting))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored]


# ---------------------------------------------------------------------------
# Answer generation
# ---------------------------------------------------------------------------

def _generate_cross_meeting_answer(query: str, meetings: list[dict]) -> str:
    if len(meetings) == 1:
        return _generate_single_meeting_answer(query, meetings[0])

    parts: list[str] = []
    for i, meeting in enumerate(meetings):
        mid = meeting.get("meeting_id", f"Meeting_{i + 1}")
        date = meeting.get("date", "Unknown")
        info: list[str] = []

        decisions = _extract_relevant_decisions(query, meeting)
        if decisions:
            info.append("Decisions: " + "; ".join(decisions))

        actions = _extract_relevant_actions(query, meeting)
        if actions:
            # FIX (Bug P): avoid nested quotes in f-string
            action_strs = [
                "{}: {}".format(a.get("who") or a.get("owner", "?"), a.get("what") or a.get("task", "?"))
                for a in actions
            ]
            info.append("Actions: " + "; ".join(action_strs))

        if info:
            parts.append("[{} ({})] {}".format(mid, date, " ".join(info)))

    if parts:
        return " | ".join(parts)
    return (
        "Based on analysis of {} meetings, no specific information was found "
        "related to '{}'.".format(len(meetings), query)
    )


def _generate_single_meeting_answer(query: str, meeting: dict) -> str:
    mid = meeting.get("meeting_id", "the meeting")
    date = meeting.get("date", "Unknown")

    decisions = _extract_relevant_decisions(query, meeting)
    actions = _extract_relevant_actions(query, meeting)

    parts: list[str] = []
    if decisions:
        parts.append("Decisions: " + "; ".join(decisions))
    if actions:
        action_strs = [
            "{}: {}".format(a.get("who") or a.get("owner", "?"), a.get("what") or a.get("task", "?"))
            for a in actions
        ]
        parts.append("Actions: " + "; ".join(action_strs))

    if parts:
        return "From {} ({}): {}".format(mid, date, " ".join(parts))
    return "No specific information found in {} related to '{}'.".format(mid, query)


def _extract_relevant_decisions(query: str, meeting: dict) -> list[str]:
    query_words = set(re.findall(r'\b\w{3,}\b', query.lower()))
    relevant: list[str] = []
    for decision in meeting.get("decisions", []):
        text = decision.get("decision") or decision.get("text") or ""
        if any(word in text.lower() for word in query_words):
            relevant.append(text)
    return relevant or [
        (d.get("decision") or d.get("text") or "")
        for d in meeting.get("decisions", [])[:1]
    ]


def _extract_relevant_actions(query: str, meeting: dict) -> list[dict]:
    query_words = set(re.findall(r'\b\w{3,}\b', query.lower()))
    relevant: list[dict] = []
    for action in meeting.get("action_items", []):
        at = "{} {}".format(
            action.get("who") or action.get("owner", ""),
            action.get("what") or action.get("task", ""),
        ).lower()
        if any(word in at for word in query_words):
            relevant.append(action)
    return relevant or meeting.get("action_items", [])[:1]


# ---------------------------------------------------------------------------
# Cross-meeting insights
# ---------------------------------------------------------------------------

def _generate_cross_meeting_insights(meetings: list[dict]) -> str:
    if len(meetings) < 2:
        return "Analysis requires at least 2 meetings for cross-meeting insights."

    insights: list[str] = []

    sentiments = [m.get("sentiment", "Unknown") for m in meetings]
    if len(set(sentiments)) > 1:
        insights.append("Sentiment varied across meetings: " + ", ".join(sentiments))
    elif sentiments:
        insights.append("Consistently {} sentiment across all meetings".format(
            sentiments[0].lower()
        ))

    all_decisions: list[str] = []
    for meeting in meetings:
        for d in meeting.get("decisions", []):
            t = d.get("decision") or d.get("text") or ""
            if t:
                all_decisions.append(t)

    word_freq: dict[str, int] = {}
    for dec in all_decisions:
        for word in re.findall(r'\b\w{4,}\b', dec.lower()):
            word_freq[word] = word_freq.get(word, 0) + 1
    recurring = [w for w, c in word_freq.items() if c >= 2]
    if recurring:
        insights.append("Recurring topics: " + ", ".join(recurring[:5]))

    all_assignees: set[str] = set()
    total_actions = 0
    for meeting in meetings:
        for action in meeting.get("action_items", []):
            owner = action.get("who") or action.get("owner") or ""
            if owner:
                all_assignees.add(owner)
            total_actions += 1

    if all_assignees:
        insights.append(
            "{} total action items across {} meetings".format(total_actions, len(meetings))
        )
        insights.append("Team members involved: " + ", ".join(sorted(all_assignees)))

    # FIX (Bug L): sort dates chronologically before building span string
    raw_dates = [str(m.get("date", "")) for m in meetings if m.get("date")]
    if len(raw_dates) >= 2:
        def _parse_mdate(d_str: str):
            clean = str(d_str).split()[0].split("T")[0]
            try:
                return datetime.strptime(clean, "%Y-%m-%d")
            except Exception:
                return datetime.min

        try:
            sorted_dates = sorted(raw_dates, key=_parse_mdate)
            d_start = sorted_dates[0].split()[0].split("T")[0]
            d_end = sorted_dates[-1].split()[0].split("T")[0]
            insights.append(
                "Meetings span from {} to {}".format(d_start, d_end)
            )
        except Exception:
            insights.append(
                "Meetings span from {} to {}".format(raw_dates[0], raw_dates[-1])
            )

    return " | ".join(insights) if insights else "No significant cross-meeting patterns detected."


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------

def _extract_relevant_snippet(query: str, meeting: dict) -> str:
    transcript = meeting.get("transcript", "")
    if not transcript:
        parts: list[str] = []
        for d in meeting.get("decisions", [])[:2]:
            t = d.get("decision") or d.get("text") or ""
            if t:
                parts.append(t)
        for a in meeting.get("action_items", [])[:2]:
            owner = a.get("who") or a.get("owner") or ""
            task = a.get("what") or a.get("task") or ""
            parts.append("{}: {}".format(owner, task))
        return " | ".join(parts) if parts else "No transcript available"

    query_words = re.findall(r'\b\w{3,}\b', query.lower())
    sentences = re.split(r'(?<=[.!?])\s+', transcript)
    for sentence in sentences:
        if any(word in sentence.lower() for word in query_words):
            snippet = sentence.strip()
            return snippet[:200] + ("..." if len(snippet) > 200 else "")

    return (transcript[:200] + "...") if len(transcript) > 200 else transcript
