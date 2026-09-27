"""jev-suggest plugin — automatic skill suggestion via Jev decision model.

Runs before the LLM tool loop (pre_llm_call) and injects at most one
advisory line into the current user message:

    <skill_relevance>
    Relevant to the current request: <name>. Ignore this if it does not fit...
    </skill_relevance>

Design notes:
- Roster is never modified, system prompt stays byte-stable (prefix caching safe).
- Fail-open: any error/timeout/missing key returns "" so the turn proceeds.
- Kill switch: env JEV_SUGGEST_DISABLE=1 (or "true"/"yes"/"on").
- No PII in logs: only lengths, names and scores are logged, never message text.
- Stdlib only (urllib), no extra dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# ---- Tunables (single place, easy to review) ----
MODEL = os.environ.get("JEV_SUGGEST_MODEL", "~typesafe/jev-latest")
ENDPOINT = os.environ.get(
    "JEV_SUGGEST_ENDPOINT", "https://openrouter.ai/api/alpha/decisions"
)
SHORTLIST = int(os.environ.get("JEV_SUGGEST_SHORTLIST", "3"))
EXCERPT_CHARS = int(os.environ.get("JEV_SUGGEST_EXCERPT", "700"))
GATE_THRESHOLD = float(os.environ.get("JEV_SUGGEST_GATE", "0.30"))
FITS_THRESHOLD = float(os.environ.get("JEV_SUGGEST_FITS", "0.30"))
TIMEOUT_S = float(os.environ.get("JEV_SUGGEST_TIMEOUT", "10"))
MAX_STATE_CHARS = int(os.environ.get("JEV_SUGGEST_MAX_STATE", "4000"))
MAX_SKILLS_INDEXED = int(os.environ.get("JEV_SUGGEST_MAX_SKILLS", "300"))

_TRUTHY = {"1", "true", "yes", "on"}

_ROSTER_CACHE: list[dict] | None = None
_ROSTER_MTIME: float = 0.0


def _disabled() -> bool:
    return os.environ.get("JEV_SUGGEST_DISABLE", "").lower() in _TRUTHY


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _read_frontmatter_description(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        head = text[3:end] if end != -1 else text[3:2000]
        for line in head.splitlines():
            s = line.strip()
            if s.lower().startswith("description:"):
                desc = s.split(":", 1)[1].strip().strip(">").strip("\"'")
                # collect continuation lines (indented)
                return desc[:500]
        return ""
    return ""


def _load_roster() -> list[dict]:
    """Build once per process: name + short desc + full desc + excerpt."""
    global _ROSTER_CACHE, _ROSTER_MTIME
    home = _hermes_home()
    skills_root = home / "skills"
    if _ROSTER_CACHE is not None:
        return _ROSTER_CACHE
    roster: list[dict] = []
    if not skills_root.is_dir():
        return roster
    for skill_md in sorted(skills_root.glob("*/*/SKILL.md")):
        if len(roster) >= MAX_SKILLS_INDEXED:
            break
        name = skill_md.parent.name
        try:
            full_text = skill_md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # full description: frontmatter description + first body lines
        desc = _read_frontmatter_description(skill_md) or full_text[:200].replace("\n", " ")
        body_start = full_text.find("---", 3)
        body = full_text[body_start:body_start + EXCERPT_CHARS] if body_start != -1 else full_text[:EXCERPT_CHARS]
        roster.append({
            "name": name,
            "short": desc[:60],
            "full": desc[:500],
            "excerpt": body[:EXCERPT_CHARS],
        })
    _ROSTER_CACHE = roster
    _ROSTER_MTIME = time.time()
    return roster


def _decide(state: str, questions: dict) -> dict | None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning("jev-suggest: OPENROUTER_API_KEY missing, skipping")
        return None
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())
    except Exception as exc:  # fail-open
        logger.warning("jev-suggest: decisions call failed: %s", type(exc).__name__)
        return None


def _is_trivial(text: str) -> bool:
    t = text.strip()
    if len(t) < 8:
        return True
    lowered = t.lower()
    return lowered in {"halo", "hai", "hi", "hello", "thanks", "terima kasih", "ok", "oke", "sip"}


def suggest_skill(user_text: str) -> str:
    """Return a skill name or '' (suggest nothing). Never raises."""
    try:
        if _disabled() or _is_trivial(user_text):
            return ""
        roster = _load_roster()
        if not roster:
            return ""
        state = user_text[:MAX_STATE_CHARS]

        # ---- Call 1: skim all + gate ----
        index_lines = "\n".join(f"- {s['name']}: {s['short']}" for s in roster)
        q1: dict = {
            "which_skill": {
                "type": "choice",
                "instructions": (
                    "Which skill best fits the user request? "
                    "Choose from the skill index below. "
                    "If none fits, choose the closest anyway; the need-checks decide."
                    f"\nSkill index:\n{index_lines}"
                ),
                "criteria": {s["name"]: s["short"] or s["name"] for s in roster},
            },
            "need_act": {
                "type": "noul",
                "instructions": "Does this turn need the agent to act on the user's stuff (files, tools, accounts)?",
                "criteria": {"true": "Needs tools/files/actions", "false": "Just talk, no action"},
            },
            "need_steps": {
                "type": "noul",
                "instructions": "Does this turn need following written multi-step procedures?",
                "criteria": {"true": "Needs a documented workflow", "false": "No procedure needed"},
            },
            "just_talk": {
                "type": "noul",
                "instructions": "Is this just chit-chat with no task?",
                "criteria": {"true": "Greeting/small talk only", "false": "Has a real task"},
            },
        }
        r1 = _decide(state, q1)
        if not r1:
            return ""
        answers1 = r1.get("answers", {})
        need_scores = [
            float(answers1.get(k, {}).get("noul", 0.0))
            for k in ("need_act", "need_steps")
        ]
        just_talk = float(answers1.get("just_talk", {}).get("noul", 0.0))
        gate = (sum(need_scores) / len(need_scores) if need_scores else 0.0) * (1.0 - just_talk)
        choice_ans = answers1.get("which_skill", {})
        probs = choice_ans.get("probabilities", {}) or {}
        ranked = sorted(probs.items(), key=lambda kv: float(kv[1]), reverse=True)[:SHORTLIST]
        if not ranked:
            # fall back to selected label
            sel = choice_ans.get("choice") or choice_ans.get("selected")
            if sel:
                ranked = [(sel, 1.0)]
        logger.info("jev-suggest: call1 gate=%.2f top=%s", gate, [n for n, _ in ranked])
        if gate < GATE_THRESHOLD or not ranked:
            return ""

        by_name = {s["name"]: s for s in roster}
        top = [by_name[n] for n, _ in ranked if n in by_name]
        if not top:
            return ""

        # ---- Call 2: read top-3 properly, may reject all ----
        detail = "\n\n".join(
            f"## {s['name']}\nFull: {s['full']}\nExcerpt: {s['excerpt']}" for s in top
        )
        q2: dict = {
            "best_of_shortlist": {
                "type": "choice",
                "instructions": (
                    "Given the full descriptions, which skill truly fits the request? "
                    f"\nCandidates:\n{detail}"
                ),
                "criteria": {s["name"]: s["full"] or s["name"] for s in top},
            },
        }
        for s in top:
            q2[f"fits_{s['name']}"] = {
                "type": "noul",
                "instructions": f"Does skill '{s['name']}' truly do what the request asks?",
                "criteria": {"true": "Yes, it does this", "false": "No, it does something else"},
            }
        r2 = _decide(state, q2)
        if not r2:
            return ""
        answers2 = r2.get("answers", {})
        best = answers2.get("best_of_shortlist", {}).get("choice") or answers2.get("best_of_shortlist", {}).get("selected")
        if not best:
            return ""
        fit = float(answers2.get(f"fits_{best}", {}).get("noul", 0.0))
        logger.info("jev-suggest: call2 best=%s fit=%.2f", best, fit)
        if fit < FITS_THRESHOLD:
            return ""
        return best
    except Exception as exc:  # absolute fail-open
        logger.warning("jev-suggest: suggest failed: %s", type(exc).__name__)
        return ""


def on_pre_llm_call(**kwargs) -> dict:
    """pre_llm_call hook: inject one advisory line or nothing."""
    try:
        if _disabled():
            return {}
        user_message = kwargs.get("user_message", "")
        text = user_message if isinstance(user_message, str) else str(user_message or "")
        if _is_trivial(text):
            return {}
        t0 = time.time()
        winner = suggest_skill(text)
        dt = time.time() - t0
        logger.info("jev-suggest: turn done in %.2fs winner=%s", dt, winner or "-")
        if not winner:
            return {}
        return {"context": (
            "<skill_relevance>\n"
            f"Relevant to the current request: {winner}. "
            "Ignore this if it does not fit what the user actually asked for.\n"
            "</skill_relevance>"
        )}
    except Exception as exc:
        logger.warning("jev-suggest: hook failed: %s", type(exc).__name__)
        return {}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
