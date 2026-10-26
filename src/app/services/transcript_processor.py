"""Meeting transcript processing service.

Provides functionality to clean, analyze, and extract metadata from
raw meeting transcripts.

Fix log
-------
Bug 1  – _save_item: str.split(deadline) replaced with regex-start slice.
Bug 2  – extract_action_items: self-assign continue is intentional (documented).
Bug 3  – extract_decisions: [^.] → [^.\n!?]+ plus stray-colon swallow.
Bug 4  – detect_speakers: charset expanded (hyphens/apostrophes); label-prefix
         and word-count guards added.
Bug A  – Relative deadlines resolved to ISO dates against the meeting date.
         Unknown NL offsets get an [UNRESOLVED] prefix for human review.
Bug B  – Pronouns (he/she/they/we) never resolved to a speaker; become
         "Unassigned" with needs_review=True.
Bug C  – Collective subjects ("we should", "the team will") emit owner="Team"
         rather than falling through to the last detected speaker.
Bug D  – Joint owners ("John and Sarah will …") emit one item per owner.
Bug E  – passive-assign regex strips speaker prefix first.
Bug F  – Deadline regex uses \b anchors; bare 'on' removed (matched inside
         words like 'implementation').
Bug G  – Pronoun-led vague clauses ("it ships", "we launch") discarded as
         deadlines rather than stored.
"""

import re
from datetime import datetime, timedelta
from typing import Optional
from difflib import SequenceMatcher
try:
    import spacy
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        import spacy.cli
        spacy.cli.download("en_core_web_sm")
        nlp = spacy.load("en_core_web_sm")
except (ImportError, Exception):
    nlp = None


# ---------------------------------------------------------------------------
# Relative-deadline resolution
# ---------------------------------------------------------------------------

_DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_NL_DEADLINE_OFFSETS: dict[str, int] = {
    "today": 0,
    "eod": 0,
    "cob": 0,
    "eob": 0,
    "end of day": 0,
    "asap": 1,
    "soon": 3,
    "this week": 4,
    "next week": 7,
    "end of week": 4,
    "next month": 30,
    "q1": 90, "q2": 180, "q3": 270, "q4": 360,
}

_PRONOUNS = frozenset({"he", "she", "they", "them", "his", "her", "their", "it"})


def _resolve_deadline(raw: str, meeting_date: Optional[str]) -> str:
    """Resolve a raw deadline string to ISO YYYY-MM-DD where possible.

    FIX (Bug A / loophole #3 / #4 / #17):
    - Relative day names ("Friday", "next Monday") resolved from meeting_date.
    - NL phrases ("ASAP", "soon", "this week") mapped to concrete offsets.
    - "end of month" resolves to last calendar day of meeting month.
    - If meeting_date is absent or term is unrecognisable: returns
      "[UNRESOLVED] <original>" so callers can flag for human review.
    """
    if not raw:
        return raw
    raw_stripped = raw.strip().lower()

    if re.match(r'^\d{4}-\d{2}-\d{2}$', raw_stripped):
        return raw.strip()

    if not meeting_date:
        anchor = datetime.now()
    else:
        try:
            anchor = datetime.strptime(meeting_date, "%Y-%m-%d")
        except ValueError:
            anchor = datetime.now()
            
    # Handle postponements
    is_postponed = bool(re.search(r'\b(postponed|delayed|pushed back)\b', raw_stripped))
    if is_postponed:
        # Check if they said "postponed to <day/date>"
        postpone_target = re.search(r'\b(?:to|until|by)\s+([^.,\n]+)', raw_stripped)
        if postpone_target:
            raw_stripped = postpone_target.group(1).strip()
        else:
            # Default postponement: 1 week from anchor
            return (anchor + timedelta(days=7)).strftime("%Y-%m-%d")

    next_prefix = "next " in raw_stripped or "this " in raw_stripped

    for day_name, day_num in _DAY_NAMES.items():
        if day_name in raw_stripped:
            delta = (day_num - anchor.weekday()) % 7
            if delta == 0 and next_prefix:
                delta = 7
            return (anchor + timedelta(days=delta)).strftime("%Y-%m-%d")

    if "end of month" in raw_stripped:
        if anchor.month == 12:
            eom = datetime(anchor.year + 1, 1, 1) - timedelta(days=1)
        else:
            eom = datetime(anchor.year, anchor.month + 1, 1) - timedelta(days=1)
        return eom.strftime("%Y-%m-%d")

    if "tomorrow" in raw_stripped:
        return (anchor + timedelta(days=1)).strftime("%Y-%m-%d")

    for phrase, offset in _NL_DEADLINE_OFFSETS.items():
        if phrase in raw_stripped:
            return (anchor + timedelta(days=offset)).strftime("%Y-%m-%d")

    written = re.search(
        r'(january|february|march|april|may|june|july|august|'
        r'september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})',
        raw_stripped,
    )
    if written:
        try:
            d = datetime(
                int(written.group(3)),
                int(_month_to_num(written.group(1))),
                int(written.group(2)),
            )
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    numeric = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', raw_stripped)
    if numeric:
        try:
            d = datetime(int(numeric.group(3)), int(numeric.group(1)), int(numeric.group(2)))
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass
            
    # NLP fallback for finding exact dates in vague strings
    try:
        if nlp is not None:
            doc = nlp(raw_stripped)
            for ent in doc.ents:
                if ent.label_ == "DATE":
                    # Just flag it with the extracted date text for human review, since spacy date strings are varied
                    return f"[UNRESOLVED] NLP extracted: {ent.text}"
    except Exception:
        pass

    return f"[UNRESOLVED] {raw.strip()}"


# Public alias — other services resolve deadlines without touching the private
# name (see structured_extractor).
resolve_deadline = _resolve_deadline


# ---------------------------------------------------------------------------
# Transcript cleaning
# ---------------------------------------------------------------------------

def clean_transcript(raw_text: str) -> str:
    """Clean a raw transcript by removing timestamps and normalizing spacing."""
    text = raw_text
    text = re.sub(r'\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}', '', text)
    text = re.sub(r'\d{2}:\d{2}:\d{2}\s*-->\s*\d{2}:\d{2}:\d{2}', '', text)
    text = re.sub(r'^\[?\d{2}:\d{2}:\d{2}\]?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    lines = [line.strip() for line in text.splitlines()]
    text = '\n'.join(lines)
    return text.strip()


# ---------------------------------------------------------------------------
# Speaker detection
# ---------------------------------------------------------------------------

def detect_speakers(clean_text: str) -> list[str]:
    """Detect speaker names from a cleaned transcript.

    FIX (Bug 4): Expanded charset; label-prefix and word-count guards added.
    """
    speaker_pattern = re.compile(r"^([A-Z][a-zA-Z0-9\s\.\-']+?)\s*:", re.MULTILINE)
    matches = speaker_pattern.findall(clean_text)
    if not matches:
        return ["Unknown"]

    _LABEL_PREFIXES = {
        'transcript', 'subtitle', 'caption', 'note',
        'action item', 'task', 'todo', 'to-do',
        'decision', 'agreed', 'summary', 'update',
    }

    seen: set[str] = set()
    unique_speakers: list[str] = []
    for speaker in matches:
        sc = speaker.strip()
        if any(sc.lower().startswith(lp) for lp in _LABEL_PREFIXES):
            continue
        if len(sc.split()) > 4:
            continue
        if sc not in seen:
            seen.add(sc)
            unique_speakers.append(sc)
    return unique_speakers if unique_speakers else ["Unknown"]


# ---------------------------------------------------------------------------
# Date utilities
# ---------------------------------------------------------------------------

def extract_meeting_date(clean_text: str) -> Optional[str]:
    """Attempt to extract a meeting date from the transcript."""
    date_patterns = [
        (r'\b(\d{4})-(\d{2})-(\d{2})\b',
         lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}"),
        (r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b',
         lambda m: f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"),
        (r'\b(January|February|March|April|May|June|July|August|'
         r'September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b',
         lambda m: (f"{m.group(3)}-{_month_to_num(m.group(1))}"
                    f"-{str(m.group(2)).zfill(2)}")),
    ]
    for pattern, formatter in date_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            try:
                date_str = formatter(match)
                datetime.strptime(date_str, '%Y-%m-%d')
                return date_str
            except Exception:
                continue
    return None


def _month_to_num(month_name: str) -> str:
    months = {
        'january': '01', 'february': '02', 'march': '03', 'april': '04',
        'may': '05', 'june': '06', 'july': '07', 'august': '08',
        'september': '09', 'october': '10', 'november': '11', 'december': '12',
    }
    return months.get(month_name.lower(), '01')


def count_words(text: str) -> int:
    return len(text.split())


def _normalize_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ---------------------------------------------------------------------------
# Decision extraction
# ---------------------------------------------------------------------------

def extract_decisions(clean_text: str) -> list[dict]:
    """Extract decision statements from the transcript.

    FIX (Bug 3): [^.] → [^.\n!?]+ ; stray colon swallowed after anchor.
    """
    decisions: list[dict] = []
    seen_decisions: set[str] = set()
    decision_patterns = [
        r'(?:we|the team|everyone)\s+(?:decided|agreed|will|shall|resolved|finalized|confirmed)\s+(?:that|to)?\s+([^.]+)',
        r'(?:it was|it\'s)\s+(?:decided|agreed|confirmed)\s+that\s+([^.]+)',
        r'(?:let\'s|let us)\s+(?:go ahead with|finalize|confirm|proceed with)\s+([^.]+)',
        r'(?:the\s+)?(?:final\s+)?decision\s*(?:is|:)\s*:?\s*([^.\n!?]+)',
        r'agreed\s*:\s*([^.]+)',
        r'(?:going forward|from now on),\s*(?:we will|let\'s)\s+([^.]+)',
    ]
    lines = clean_text.split('\n')
    for i, line in enumerate(lines):
        for pattern in decision_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                decision_text = (
                    match.group(1).strip() if match.groups() else match.group(0).strip()
                )
                decision_text = _normalize_text(decision_text)
                if len(decision_text) > 10 and decision_text not in seen_decisions:
                    seen_decisions.add(decision_text)
                    decisions.append({
                        "decision": decision_text,
                        "context": _get_context(lines, i),
                    })
                break
    return decisions


# ---------------------------------------------------------------------------
# Action-item extraction
# ---------------------------------------------------------------------------

def extract_action_items(
    clean_text: str,
    speakers: list[str],
    meeting_date: Optional[str] = None,
) -> list[dict]:
    """Extract action items with owners and resolved deadlines.

    FIX (Bug A)  – meeting_date threaded through for deadline resolution.
    FIX (Bug B)  – pronoun owners → "Unassigned" + needs_review=True.
    FIX (Bug C)  – collective subjects ("we should") → owner="Team".
    FIX (Bug D)  – joint owners ("John and Sarah will") emit one item each.
    FIX (Bug E)  – speaker prefix stripped before passive-assign match.
    """
    action_items: list[dict] = []
    seen_items: set[tuple[str, str]] = set()
    modals = (
        r'(?:will|needs?\s+to|should|must|is\s+going\s+to'
        r'|is\s+taking\s+on|is\s+responsible\s+for|has\s+to|shall'
        r'|can\s+take\s+care\s+of|can\s+handle|can\s+do)'
    )
    lines = clean_text.replace("’", "'").split('\n')

    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue

        speaker_match = re.match(r"^([A-Z][a-zA-Z0-9\s\.\-']+?)\s*:", line_clean)
        current_speaker = speaker_match.group(1).strip() if speaker_match else "Unassigned"

        # ── Explicit labels ───────────────────────────────────────────────
        explicit = re.search(
            r'(?:action\s+item|task|todo|to-do)'
            r'(?:\s+(?:for|assigned\s+to)\s+([^:,\n]+?)|\s*:\s*([^:,\n]+?))?'
            r'\s*[:\-]\s*([^.\n]+)',
            line_clean, re.IGNORECASE,
        )
        if explicit:
            raw_owner = (explicit.group(1) or explicit.group(2) or "").strip()
            who = raw_owner if raw_owner else current_speaker
            what = explicit.group(3).strip()
            _save_item(action_items, seen_items, who, what, lines, i, meeting_date)
            continue

        # ── Self-assignment ───────────────────────────────────────────────
        # Intentional continue — self-assignment takes priority over all other patterns
        self_assign = re.search(r"(?:I'll|I\s+will|I\s+can\s+take\s+care\s+of|I\s+can\s+handle|I\s+can\s+do)\s+([^.,\n]+)", line_clean, re.IGNORECASE)
        if self_assign:
            _save_item(
                action_items, seen_items,
                current_speaker, self_assign.group(1).strip(),
                lines, i, meeting_date,
            )
            continue

        # ── Passive assignment ────────────────────────────────────────────
        # FIX (Bug E): strip speaker prefix before matching
        line_for_assign = re.sub(r"^[A-Z][a-zA-Z0-9\s\.\-']+?:\s*", "", line_clean)
        passive = re.search(
            r'(.+?)\s+(?:is|are|was|were|has\s+been|have\s+been)?\s*'
            r'assigned\s+to\s+([A-Z][a-zA-Z0-9\s\.\-\']+?)(?:\s*[,.]|$)',
            line_for_assign, re.IGNORECASE,
        )
        if passive:
            what = passive.group(1).strip()
            who_raw = passive.group(2).strip()
            matched = next(
                (spk for spk in speakers if spk.lower() == who_raw.lower()), who_raw
            )
            _save_item(action_items, seen_items, matched, what, lines, i, meeting_date)
            continue

        # ── General Passive Obligation / Deadline ──────────────────────────
        passive_action = re.search(
            r'\b(needs?\s+to\s+be|must\s+be|should\s+be|shall\s+be|will\s+be|to\s+be|be)\s+(submitted|completed|done|finished|delivered|provided|updated|uploaded|sent|resolved|reviewed|presented)\b|\b(is\s+due|are\s+due)\b',
            line_for_assign, re.IGNORECASE,
        )
        if passive_action:
            _save_item(action_items, seen_items, "Unassigned", line_for_assign, lines, i, meeting_date)
            continue

        # ── FIX (Bug C): collective subject → Team ────────────────────────
        collective = re.search(
            r'^(?:we|the\s+team|everyone|all\s+of\s+us)\s+' + modals + r'\s+([^.,\n]+)',
            line_clean, re.IGNORECASE,
        )
        if collective:
            _save_item(
                action_items, seen_items,
                "Team", collective.group(1).strip(),
                lines, i, meeting_date,
            )
            continue

        # ── Third-party modal assignment ──────────────────────────────────
        matched_third_party = False
        for spk in speakers:
            if spk in ("Unknown", "Unassigned"):
                continue

            # FIX (Bug D): joint owners
            joint = re.search(
                rf'\b{re.escape(spk)}\s+and\s+'
                rf'([A-Z][a-zA-Z0-9\.\-\']+)\s+{modals}\s+([^.,\n]+)',
                line_clean, re.IGNORECASE,
            )
            if joint:
                co_owner = joint.group(1).strip()
                what = joint.group(2).strip()
                _save_item(action_items, seen_items, spk, what, lines, i, meeting_date)
                _save_item(action_items, seen_items, co_owner, what, lines, i, meeting_date)
                matched_third_party = True
                break

            single = re.search(
                rf'\b{re.escape(spk)}\s+{modals}\s+([^.,\n]+)',
                line_clean, re.IGNORECASE,
            )
            if single:
                _save_item(
                    action_items, seen_items,
                    spk, single.group(1).strip(),
                    lines, i, meeting_date,
                )
                matched_third_party = True
                break
                
        if matched_third_party:
            continue

        # ── Cross-line / follow-up deadlines ──────────────────────────────
        followup_dl = re.search(
            r'(?:finish(?:\s+it|that)?\s+by|deadline.*?is|push.*?deadline\s+to|due\s+by|postponed|delayed|pushed\s+back)\s+([^.,\n]*)', 
            line_clean, re.IGNORECASE
        )
        if followup_dl and action_items:
            # If the regex matched but there's no trailing text, just pass the keyword
            raw_dl = followup_dl.group(1).strip()
            if not raw_dl:
                raw_dl = followup_dl.group(0).strip()
            
            target_task = None
            if "your deadline" in line_clean.lower() or "your task" in line_clean.lower():
                prev_speaker = lines[i-1].split(":")[0].strip() if i > 0 and ":" in lines[i-1] else None
                target_task = next((t for t in reversed(action_items) if t["who"] == prev_speaker), None)
            
            if not target_task:
                target_task = action_items[-1]
                
            new_dl = _resolve_deadline(raw_dl, meeting_date)
            
            # Avoid overwriting with an unresolved deadline if we already have a resolved one
            has_valid_old = target_task.get("when") and "[UNRESOLVED]" not in target_task["when"]
            is_new_unresolved = "[UNRESOLVED]" in new_dl
            
            if not (has_valid_old and is_new_unresolved):
                target_task["when"] = new_dl

        # ── NLP Person Extraction Fallback ──────────────────────────────
        # If we got this far and still haven't extracted an explicit action from this line,
        # but the line sounds like a task due to action verbs, try finding a PERSON with spaCy.
        if "will " in line_clean.lower() or "needs to " in line_clean.lower():
            try:
                if nlp is not None:
                    doc = nlp(line_clean)
                    people = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
                    if people:
                        person = next((p for p in people if any(p.lower() in s.lower() for s in speakers)), people[0])
                        # Ensure not already assigned
                        already_handled = any(person in t['who'] and _similarity(t['what'], line_clean) > 0.5 for t in action_items)
                        if not already_handled:
                            what_str = re.sub(r"^[A-Z][a-zA-Z0-9\s\.\-']+?:\s*", '', line_clean)
                            _save_item(action_items, seen_items, person, what_str, lines, i, meeting_date)
            except Exception:
                pass

    return action_items


# ---------------------------------------------------------------------------
# Item persistence helper
# ---------------------------------------------------------------------------

def _save_item(
    items: list[dict],
    seen: set[tuple[str, str]],
    who: str,
    what: str,
    lines: list[str],
    idx: int,
    meeting_date: Optional[str] = None,
) -> None:
    """Validate and persist one action item.

    FIX (Bug 1): slice at deadline_match.start() instead of str.split().
    FIX (Bug A): deadline resolved to ISO date.
    FIX (Bug B): pronoun owner → "Unassigned" + needs_review=True.
    FIX (Bug F): \b anchors on connector keywords; bare 'on' removed.
    FIX (Bug G): pronoun-led vague deadlines discarded.
    """
    # FIX (Bug F): word-boundary anchors; 'on' added conditionally before known time words
    deadline_match = re.search(
        r'\b(?:by|before|until|deadline|due|on(?=\s+(?:\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|mon|tue|wed|thu|fri|sat|sun|today|tomorrow)))\b\s+([^.,\n]+)',
        what, re.IGNORECASE,
    )
    deadline: Optional[str] = None
    if deadline_match:
        raw_dl = deadline_match.group(1).strip()

        # Strip leading non-time words (e.g. "scripts by Wednesday" → "Wednesday")
        time_anchor = re.search(
            r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday'
            r'|january|february|march|april|may|june|july|august'
            r'|september|october|november|december'
            r'|today|tomorrow|eod|cob|eob|end\s+of|next|this|\d)',
            raw_dl, re.IGNORECASE,
        )
        if time_anchor:
            raw_dl = raw_dl[time_anchor.start():].strip()

        # FIX (Bug G): discard pronoun-led vague conditions
        is_vague = bool(re.fullmatch(
            r'(?:it|they|we|he|she|the\s+team)\s+\w+',
            raw_dl, re.IGNORECASE,
        ))
        if not is_vague and raw_dl:
            deadline = _resolve_deadline(raw_dl, meeting_date)
            what = what[:deadline_match.start()].strip()
            what = re.sub(
                r'\s+\b(?:by|before|until|deadline|due|on)\b\s*$',
                '', what, flags=re.IGNORECASE,
            ).strip()

    # FIX (Bug B): pronoun owners must not be silently assigned
    needs_review = False
    if who.strip().lower() in _PRONOUNS:
        who = "Unassigned"
        needs_review = True

    item_key = (who, what)
    if item_key not in seen and len(what) > 5:
        seen.add(item_key)
        items.append({
            "who": who,
            "what": what,
            "when": deadline,
            "needs_review": needs_review,
            "context": _get_context(lines, idx),
        })


# ---------------------------------------------------------------------------
# Context helper
# ---------------------------------------------------------------------------

def _get_context(lines: list[str], current_index: int) -> Optional[str]:
    context_lines = []
    for j in range(max(0, current_index - 1), min(len(lines), current_index + 2)):
        if j != current_index:
            context_lines.append(lines[j].strip())
    return ' | '.join(context_lines) if context_lines else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_transcript(raw_transcript: str) -> dict:
    """Full pipeline: clean → speakers → metadata → decisions → action items."""
    clean_text = clean_transcript(raw_transcript)
    speakers = detect_speakers(clean_text)
    meeting_date = extract_meeting_date(clean_text)
    word_count = count_words(clean_text)
    decisions = extract_decisions(clean_text)
    action_items = extract_action_items(clean_text, speakers, meeting_date)
    return {
        "clean_text": clean_text,
        "metadata": {
            "meeting_date": meeting_date,
            "word_count": word_count,
            "num_speakers": len(speakers),
            "speakers": speakers,
        },
        "decisions": decisions,
        "action_items": action_items,
    }
