"""Meeting summary service.

Provides functionality to generate high-level summaries from meeting insights.

Fix log
-------
Bug Q  – _find_urgent_action: was reading action.get("who") / action.get("what")
         but intelligence_extractor now outputs "owner" / "task".  Both key
         names are tried so the function works with either upstream format.
"""

from typing import Optional


def generate_summary(
    decisions: list[dict],
    action_items: list[dict],
    sentiment_timeline: list[dict],
    speaker_summary: list[dict],
) -> dict:
    """Generate a high-level summary of a meeting."""
    return {
        "summary": {
            "total_decisions": len(decisions),
            "total_actions": len(action_items),
            "overall_sentiment": _determine_overall_sentiment(sentiment_timeline),
            "key_decision": _find_key_decision(decisions),
            "urgent_action": _find_urgent_action(action_items),
        }
    }


def _determine_overall_sentiment(sentiment_timeline: list[dict]) -> str:
    if not sentiment_timeline:
        return "Neutral"
    counts = {"Positive": 0, "Negative": 0, "Neutral": 0}
    for seg in sentiment_timeline:
        s = seg.get("sentiment", "Neutral")
        if s in counts:
            counts[s] += 1
    return max(counts, key=counts.get)


def _find_key_decision(decisions: list[dict]) -> Optional[str]:
    if not decisions:
        return None
    priority_keywords = [
        'launch', 'release', 'ship', 'deploy', 'go-live',
        'budget', 'funding', 'investment', 'approval',
        'strategy', 'direction', 'pivot', 'change',
        'hire', 'team', 'organizational',
        'deadline', 'timeline', 'schedule',
        'approved', 'decided', 'final', 'confirmed',
    ]
    scored = []
    for d in decisions:
        # Support both "decision" (processor) and "text" (extractor) keys
        text = (d.get("decision") or d.get("text") or "").lower()
        score = sum(1 for kw in priority_keywords if kw in text)
        scored.append((score, d.get("decision") or d.get("text") or ""))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored[0][0] > 0 else (
        decisions[0].get("decision") or decisions[0].get("text") or ""
    )


def _find_urgent_action(action_items: list[dict]) -> Optional[str]:
    """Find the most urgent action item.

    FIX (Bug Q / loophole #2): tries "who"/"owner" and "what"/"task" so this
    function works whether fed raw processor output or extractor output.
    """
    if not action_items:
        return None

    time_sensitivity = {
        'today': 10, 'tomorrow': 9, 'eod': 8, 'cob': 8,
        'this week': 7, 'next week': 6, 'asap': 9,
        'immediately': 10, 'urgent': 10, 'critical': 9,
        'priority': 8, 'deadline': 7, 'due': 6,
    }

    scored: list[tuple[int, dict]] = []
    for action in action_items:
        # Accept both key naming conventions (who/owner, what/task, when/deadline)
        who = action.get("who") or action.get("owner") or ""
        what = action.get("what") or action.get("task") or ""
        when = action.get("when") or action.get("deadline") or ""
        text = "{} {} {}".format(who, what, when).lower()
        score = sum(w for kw, w in time_sensitivity.items() if kw in text)
        scored.append((score, action))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    who = best.get("who") or best.get("owner") or "Unknown"
    what = best.get("what") or best.get("task") or ""
    when = best.get("when") or best.get("deadline")
    result = "{}: {}".format(who, what)
    if when:
        result += " (deadline: {})".format(when)
    return result
