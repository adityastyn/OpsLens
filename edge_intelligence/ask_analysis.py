"""Ask OpsLens answered by the AI from the same facts as the diagnosis.

Used when OPSLENS_LLM_PROVIDER=groq (turn off with OPSLENS_ASK_ANALYSIS=false).
The model gets the incident facts that the diagnosis gets (readings, alarms,
event order, possible causes from the record, SOP steps and passages, recovery
criteria) plus the question and the last few messages.

The answer is checked line by line with the diagnosis record check: a line
with a number, tag or SOP step that is not in the record is removed, and the
removed lines are returned. If too much is removed, or the model fails, the
caller uses the older evidence-based answer instead.
"""
from __future__ import annotations

import logging
import os
import re
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("opslens.ask_analysis")

ASK_SYSTEM_PROMPT = (
    "You are OpsLens, an AI assistant for a wastewater plant control-room operator. You analyse ONE incident "
    "and answer the operator's question about it.\n"
    "Format (Bottom Line Up Front), plain text, no markdown headings:\n"
    "Bottom line: <one sentence that answers the question directly>\n"
    "Then, only when the question is about the cause, what to do, or the situation in general:\n"
    "Top 2 steps: <the two most important SOP steps now, each cited 'SOP step N (p.P)'>\n"
    "History: <the most relevant earlier incident from FACTS, or 'none recorded'>\n"
    "Then at most four short '- ' bullets with the evidence. For a narrow question (for example 'has this "
    "happened today?'), give the bottom line and at most two evidence bullets.\n"
    "Rules:\n"
    "- Use ONLY the FACTS. Copy numbers, units, tags and times exactly as written there. Do not calculate "
    "differences, ratios or percentages, do not convert time zones, and do not write dates.\n"
    "- Say clearly what is recorded and what is your interpretation. A cause is a hypothesis until it is "
    "confirmed in the field; the order of events does not prove cause.\n"
    "- When you give a procedure step, cite it as 'SOP step N (p.P)'. A draft SOP is DRAFT, NOT APPROVED. "
    "Checks that are not in the SOP start with 'Not in the SOP:'.\n"
    "- If the FACTS cannot answer the question, say what is missing and how the operator can find it out.\n"
    "- If the question is about other equipment or another incident, say this answer covers only the selected incident.\n"
    "- OpsLens is advisory and read-only: never say you started, stopped, reset, acknowledged or cleared anything, "
    "and say that restarts or resets follow the SOP's conditions.\n"
    "- Plain words, at most about 150 words. No walls of text."
)


# An answer is dropped only when fewer than this many checked lines survive
# or more than this share of its lines could not be checked.
MIN_KEPT_LINES = 2
MAX_REMOVED_SHARE = 0.75
# Why the last AI answer for an incident was not used, so the operator is told.
LAST_FAILURE: Dict[str, Dict[str, str]] = {}

def enabled() -> bool:
    from llm_provider import selected_provider
    flag = (os.environ.get("OPSLENS_ASK_ANALYSIS") or "true").strip().lower()
    return selected_provider() == "groq" and flag not in ("false", "0", "no")


def build_ask_prompt(facts: Dict[str, Any], question: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
    import ai_diagnosis
    base = ai_diagnosis.build_prompt(facts).replace("Write the eight-section diagnosis now.", "").rstrip()
    lines = [base, ""]
    recent = [h for h in (history or []) if h.get("content")][-4:]
    if recent:
        lines.append("Earlier in this conversation:")
        lines += [f"- {h.get('role', 'user')}: {str(h['content'])[:400]}" for h in recent]
        lines.append("")
    lines.append(f"OPERATOR QUESTION: {question.strip()}")
    return "\n".join(lines)


def answer(full_event: Dict[str, Any], db, question: str, history: Optional[List[Dict[str, Any]]] = None,
           on_line: Optional[Callable[[str], None]] = None) -> Optional[Dict[str, Any]]:
    """Return the checked AI answer, or None when the caller should use the
    evidence-based answer. Never raises.

    on_line, if given, receives each line as soon as it is complete and has
    passed the record check, so the operator sees the answer build up.
    Unchecked text is never passed on."""
    import ai_diagnosis
    started = perf_counter()
    kept: List[str] = []
    removed: List[Dict[str, Any]] = []
    counter = {"content": 0, "first_line_ms": None}
    try:
        facts = ai_diagnosis.build_facts(full_event, db)
        check = ai_diagnosis.line_checker(facts)

        def take(line: str) -> None:
            if not line.strip():
                kept.append("")
                return
            counter["content"] += 1
            # Sentences are checked one by one, so one bad number does not cost a whole paragraph.
            sentences = (re.split(r"(?<=[.!?])\s+(?=[A-Z\-*•])", line)
                         if not line.lstrip().startswith(("-", "*", "•")) else [line])
            good = []
            for sentence in sentences:
                problems = check(sentence)
                if problems:
                    removed.append({"line": sentence.strip(), "problems": problems})
                else:
                    good.append(sentence)
            if good:
                kept.append(" ".join(good))
                if counter["first_line_ms"] is None:
                    counter["first_line_ms"] = round((perf_counter() - started) * 1000, 1)
                if on_line:
                    on_line(" ".join(good) + "\n")

        prompt = build_ask_prompt(facts, question, history)
        buffer = ""
        for token, final in ai_diagnosis.stream_groq(prompt, system=ASK_SYSTEM_PROMPT):
            if final is not None:
                break
            buffer += token
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                take(line)
        if buffer.strip():
            take(buffer)
    except Exception as exc:
        logger.warning("ask_analysis event=%s failed: %s: %s", full_event.get("event_id"), type(exc).__name__, exc)
        LAST_FAILURE[str(full_event.get("event_id"))] = {"kind": getattr(exc, "kind", "unavailable"), "detail": str(exc)[:200]}
        return None
    content = counter["content"]
    if not content:
        return None
    clean = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    for r in removed:
        logger.info("ask_analysis event=%s removed: %r [%s]", full_event.get("event_id"), r["line"][:200], "; ".join(r["problems"]))
    kept_lines = content - len(removed)
    # Shown when enough checked lines survive; only a mostly-unverifiable answer is dropped.
    if not clean or kept_lines < min(MIN_KEPT_LINES, content) or len(removed) / content > MAX_REMOVED_SHARE:
        logger.warning("ask_analysis event=%s rejected: %d of %d lines failed the record check",
                       full_event.get("event_id"), len(removed), content)
        LAST_FAILURE[str(full_event.get("event_id"))] = {"kind": "ai_answer_unchecked",
                                                        "detail": f"{len(removed)} of {content} lines could not be checked against the record"}
        return None
    LAST_FAILURE.pop(str(full_event.get("event_id")), None)
    if removed:
        clean += f"\n\n({len(removed)} statement{'s' if len(removed) != 1 else ''} removed because {'they' if len(removed) != 1 else 'it'} could not be checked against the record.)"
    return {
        "text": clean, "removed_lines": removed, "model": ai_diagnosis.groq_model(),
        "latency_ms": round((perf_counter() - started) * 1000, 1), "first_line_ms": counter["first_line_ms"],
        "sop_id": (facts.get("sop") or {}).get("sop_id"),
        "sop_approval": (facts.get("sop") or {}).get("status"),
    }


def _local_clock(value: Any) -> str:
    """'17:02:40 UTC' or an ISO time, shown in the control room's local time."""
    from datetime import datetime, timezone
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if re.match(r"\d{4}-\d{2}-\d{2}T", text):
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            clock = datetime.strptime(text.replace(" UTC", ""), "%H:%M:%S").time()
            moment = datetime.combine(datetime.now(timezone.utc).date(), clock, tzinfo=timezone.utc)
        return moment.astimezone().strftime("%H:%M:%S")
    except ValueError:
        return text


def _num(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{v:,.0f}" if v.is_integer() else f"{v:,.3f}".rstrip("0").rstrip(".")


def readable_fallback(full_event: Dict[str, Any], db, reason: str = "") -> Optional[str]:
    """A short answer built from the record without the AI: what is confirmed,
    what is only a hypothesis, and what to check first. Used when the AI answer
    is unavailable or could not be checked. Never raises."""
    try:
        import ai_diagnosis
        from incident_insight import bluf
        facts = ai_diagnosis.build_facts(full_event, db)
        primary = facts.get("primary_alarm") or {}
        sop = facts.get("sop") or {}
        out = [f"Bottom line: {primary.get('description') or primary.get('tag') or 'Alarm'} on {facts.get('equipment') or 'the equipment'}"
               f" ({str(facts.get('incident_status') or 'status not recorded').lower()})."]
        readings = sorted(facts.get("readings") or [], key=lambda r: r.get("state_at_worst") != "LIMIT EXCEEDED")[:4]
        if readings:
            out += ["", "Confirmed in the record:"]
            for r in readings:
                when = _local_clock(r.get("worst_at"))
                line = f"- {r.get('name') or r.get('tag')} reached {_num(r.get('worst'))} {r.get('unit') or ''}".rstrip()
                line += f" at {when}" if when else ""
                line += f" (normal {r['normal']})" if r.get("normal") else ""
                line += f"; now {_num(r.get('now'))} {r.get('unit') or ''}".rstrip() + "."
                out.append(line)
        candidates = (facts.get("cause_candidates") or [])[:3]
        if candidates:
            out += ["", "Possible causes (hypotheses, not confirmed):"]
            out += [f"- {c['statement'].split('. ')[0].rstrip('.')}. Basis: {c['basis']}." for c in candidates]
        steps = bluf(facts, facts.get("history") or {}).get("top_steps") or []
        if steps:
            out += ["", f"Check first, from {sop.get('sop_id')} ({sop.get('status')}):"]
            out += [f"- Step {s['step']} (p.{s['page']}): {s['text']}" for s in steps]
        elif not sop.get("sop_id"):
            out += ["", "No SOP matched this incident. Escalate to the shift supervisor."]
        if facts.get("missing"):
            out += ["", "Not in the record: " + "; ".join(str(m) for m in facts["missing"][:3]) + "."]
        out += ["", "Built from the incident record without the AI" + (f" ({reason})." if reason else ".")
                + " Timing and ranking do not prove a cause; confirm in the field."]
        return "\n".join(out)
    except Exception as exc:
        logger.warning("ask_analysis readable fallback unavailable: %s: %s", type(exc).__name__, exc)
        return None
