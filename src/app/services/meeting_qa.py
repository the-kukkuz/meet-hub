"""Meeting Q&A service.

Provides functionality to answer user queries based on meeting transcript context.

Fix log
-------
Bug S  – _answer_who_query: speaker detection regex expanded to match hyphens
         and apostrophes, consistent with transcript_processor (loophole #11).
"""

import re
from typing import Optional


def find_relevant_chunks(
    query: str,
    context: list[str],
    max_chunks: int = 5,
) -> list[dict]:
    """Find the most relevant transcript chunks for a given query."""
    if not context:
        return []

    stop_words = {
        'what', 'when', 'where', 'who', 'whom', 'which', 'why', 'how',
        'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did',
        'will', 'would', 'could', 'should', 'may', 'might',
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'about', 'into', 'through', 'during',
        'i', 'me', 'my', 'we', 'our', 'you', 'your', 'he', 'him', 'she', 'her',
        'it', 'its', 'they', 'them', 'their', 'this', 'that', 'these', 'those',
    }

    query_words = set(
        w.lower() for w in re.findall(r'\b\w+\b', query)
        if w.lower() not in stop_words and len(w) > 2
    )
    if not query_words:
        query_words = set(w.lower() for w in re.findall(r'\b\w+\b', query) if len(w) > 2)

    scored: list[dict] = []
    for i, chunk in enumerate(context):
        chunk_lower = chunk.lower()
        score = sum(chunk_lower.count(w) for w in query_words)
        if query.lower() in chunk_lower:
            score += 10
        if score > 0:
            meeting_id = f"Meeting_{i + 1}"
            m = re.search(
                r'(?:meeting|session|call|discussion)\s*(?:on|about|regarding)?\s*'
                r'([A-Za-z0-9\s,.-]+)',
                chunk, re.IGNORECASE,
            )
            if m:
                meeting_id = m.group(1).strip()[:50]
            scored.append({
                "score": score,
                "meeting": meeting_id,
                "snippet": _extract_snippet(chunk, query_words),
                "full_chunk": chunk,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_chunks]


def _extract_snippet(chunk: str, query_words: set[str], max_length: int = 200) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', chunk)
    best, best_score = None, 0
    for sentence in sentences:
        score = sum(1 for w in query_words if w in sentence.lower())
        if score > best_score:
            best_score, best = score, sentence
    if best:
        s = best.strip()
        return s[:max_length] + ("..." if len(s) > max_length else "")
    return (chunk[:max_length] + "...") if len(chunk) > max_length else chunk


def answer_query(query: str, context: list[str]) -> dict:
    """Answer a user query based on meeting transcript context."""
    if not context:
        return {
            "answer": "The information is not available in the provided transcripts.",
            "sources": [],
        }

    relevant = find_relevant_chunks(query, context)
    if not relevant:
        return {
            "answer": "The information is not available in the provided transcripts.",
            "sources": [],
        }

    answer = _generate_answer(query, relevant, query.lower())
    sources = [{"meeting": c["meeting"], "snippet": c["snippet"]} for c in relevant]
    return {"answer": answer, "sources": sources}


def _generate_answer(query: str, chunks: list[dict], query_lower: str) -> str:
    if any(w in query_lower for w in ["who", "whom", "whose"]):
        return _answer_who_query(query, chunks)
    if any(w in query_lower for w in ["when", "what time", "what date"]):
        return _answer_when_query(query, chunks)
    if any(w in query_lower for w in ["what", "which"]):
        return _answer_what_query(query, chunks)
    if "why" in query_lower:
        return _answer_why_query(query, chunks)
    if "how" in query_lower:
        return _answer_how_query(query, chunks)
    if "where" in query_lower:
        return _answer_where_query(query, chunks)
    return _answer_general_query(query, chunks)


def _answer_who_query(query: str, chunks: list[dict]) -> str:
    """Answer 'who' questions.

    FIX (Bug S / loophole #11): expanded speaker regex to match hyphens and
    apostrophes so "Mary-Jane" and "O'Brien" are recognised as speaker names.
    """
    people: set[str] = set()
    for chunk in chunks:
        # FIX (Bug S): was r'^([A-Z][a-zA-Z\s]+?)\s*:' — missed hyphens/apostrophes
        speakers = re.findall(
            r"^([A-Z][a-zA-Z\s\.\-']+?)\s*:",
            chunk["full_chunk"], re.MULTILINE,
        )
        people.update(s.strip() for s in speakers)

        action_names = re.findall(
            r'([A-Z][a-zA-Z\s\.\-\']+?)\s+'
            r'(?:will|should|must|needs?\s+to|has\s+to)\s+',
            chunk["full_chunk"],
        )
        people.update(n.strip() for n in action_names)

    if people:
        return "Based on the transcripts, the following people are mentioned: {}.".format(
            ", ".join(sorted(people)[:5])
        )
    return "No specific individuals are mentioned in relation to this query."


def _answer_when_query(query: str, chunks: list[dict]) -> str:
    dates: list[str] = []
    for chunk in chunks:
        dates += re.findall(
            r'\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|'
            r'(?:January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+\d{1,2},?\s+\d{4})\b',
            chunk["full_chunk"], re.IGNORECASE,
        )
        dates += re.findall(
            r'\b(?:morning|afternoon|evening|night|noon|midnight'
            r'|\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))\b',
            chunk["full_chunk"],
        )
    if dates:
        unique = list(set(dates))[:5]
        return "Based on the transcripts, the relevant dates/times are: {}.".format(
            ", ".join(unique)
        )
    return "No specific dates or times are mentioned in relation to this query."


def _answer_what_query(query: str, chunks: list[dict]) -> str:
    answers: list[str] = []
    for chunk in chunks:
        answers += re.findall(
            r'(?:decided|agreed|will|resolution|decision\s+is|action\s+item)'
            r'[:\s]+([^.\n]+)',
            chunk["full_chunk"], re.IGNORECASE,
        )
    if answers:
        return "Based on the transcripts: {}.".format("; ".join(sorted(set(answers))[:3]))
    return "Based on the transcripts: {}".format(chunks[0]["snippet"]) if chunks else (
        "The information is not available."
    )


def _answer_why_query(query: str, chunks: list[dict]) -> str:
    reasons: list[str] = []
    for chunk in chunks:
        reasons += re.findall(
            r'(?:because|since|therefore|thus|hence|due\s+to|as\s+a\s+result)[^.\n]+',
            chunk["full_chunk"], re.IGNORECASE,
        )
    if reasons:
        return "Based on the transcripts, the reasoning includes: {}.".format(
            "; ".join(reasons[:3])
        )
    return "Based on the transcripts: {}".format(chunks[0]["snippet"]) if chunks else (
        "The information is not available."
    )


def _answer_how_query(query: str, chunks: list[dict]) -> str:
    processes: list[str] = []
    for chunk in chunks:
        processes += re.findall(
            r'(?:by|through|using|via|method|process|steps?)[^.\n]+',
            chunk["full_chunk"], re.IGNORECASE,
        )
    if processes:
        return "Based on the transcripts: {}.".format("; ".join(processes[:3]))
    return "Based on the transcripts: {}".format(chunks[0]["snippet"]) if chunks else (
        "The information is not available."
    )


def _answer_where_query(query: str, chunks: list[dict]) -> str:
    locations: list[str] = []
    for chunk in chunks:
        locations += re.findall(
            r'(?:at|in|location|place|room|building|office|conference)[^.\n]+',
            chunk["full_chunk"], re.IGNORECASE,
        )
    if locations:
        return "Based on the transcripts: {}.".format("; ".join(locations[:3]))
    return "Based on the transcripts: {}".format(chunks[0]["snippet"]) if chunks else (
        "The information is not available."
    )


def _answer_general_query(query: str, chunks: list[dict]) -> str:
    if not chunks:
        return "The information is not available in the provided transcripts."
    combined = " ".join(c["snippet"] for c in chunks[:3])
    if len(combined) > 500:
        combined = combined[:500] + "..."
    return "Based on the transcripts: {}".format(combined)
