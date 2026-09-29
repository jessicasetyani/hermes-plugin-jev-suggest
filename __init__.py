"""jev-suggest plugin — automatic skill suggestion via a decision model on OpenRouter.

Runs before the LLM tool loop (pre_llm_call) and injects at most one
advisory line into the current user message:

    <skill_relevance>
    Relevant to the current request: <name>. Ignore this if it does not fit...
    </skill_relevance>

Design notes (ported from jev-skill-router's config pattern, OpenRouter-only):
- Settings live in config.yaml under plugins.entries.jev-suggest.settings
  (mode/gate/fits/shortlist/excerpt/timeout_s/suggest_chars/max_skills/
  openrouter_model/openrouter_base_url). Env vars remain as fallback so old
  setups keep working; config.yaml wins when a ctx is bound.
- Backend is LOCKED to OpenRouter Decisions API. No TypeSafe-direct, no
  gateway. Only the model slug is selectable, from an allowlist:
  upstage/solar-decide | ~typesafe/jev-latest | typesafe/jev-1.13.
  Anything else falls back to the default (logged, fail-open).
- Roster is never modified, system prompt stays byte-stable (prefix caching safe).
- Fail-open: any error/timeout/missing key returns "" so the turn proceeds.
- Kill switch: `mode: off` (config) or `hermes plugins disable jev-suggest`.
- No PII in logs: only lengths, names and scores are logged, never message text.
- Stdlib only (urllib), no extra dependencies.
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from datetime import timezone
from pathlib import Path

logger = logging.getLogger(__name__)

COMMAND = "jev-suggest"

# ---- Model allowlist (OpenRouter-only) ----
ALLOWED_MODELS = frozenset({
    "upstage/solar-decide",
    "~typesafe/jev-latest",
    "typesafe/jev-1.13",
})
DEFAULT_MODEL = "~typesafe/jev-latest"
DEFAULT_BASE_URL = "https://openrouter.ai/api/alpha"
# Solar Decide route caps one Choice at 26 labels TOTAL, and a chunked roster
# spends one label on `none_of_these` — so the usable chunk is 25. Verified
# live 2026-09-29: 26 options OK, 26 + none_of_these = 422, 27 = 422.
# Same probe: score is capped at 2-10 levels on BOTH vendors (Solar 422,
# Jev 400 "at most 10 levels"), so score cannot replace choice for a roster.
SOLAR_MODELS = frozenset({"upstage/solar-decide"})
SOLAR_MAX_CHOICES = 25
DEFAULT_CHUNK = 240
MAX_CHOICES = 255  # the API refuses a Choice with more options
NONE_OPTION = "none_of_these"
NONE_THRESHOLD = 0.50  # a non-best chunk with P(none) >= this nominates nobody

RATE_LIMIT_CODES = (429, 529)

# Cap for the outbound JSON payload: anything bigger fails open without
# sending (a truncated payload must never become a favorable verdict).
MAX_PAYLOAD_BYTES = 65536

# Cap for the per-process response cache: distinct turns each insert one
# entry, so size must be bounded even though entries also expire by TTL.
CACHE_MAX_ENTRIES = 256


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse 3xx: no redirect is ever followed, fail-open instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Hardened transport: no redirects, proxy env ignored.
_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _urlopen(req, timeout=None):
    return _OPENER.open(req, timeout=timeout)


# Per-process state, keyed by endpoint URL: pacing and breaker must survive
# across decisions (settings are re-read per turn, these are not settings).
_PACE_LAST: dict[str, float] = {}  # endpoint -> monotonic time of last attempt
_BREAKER: dict[str, list] = {}  # endpoint -> [consecutive 429/529s, open-until]
_RESP_CACHE: dict[str, tuple[float, dict]] = {}  # hash -> (expires_at, body)

# Bound by register(); CLI handler runs without a ctx argument.
_CTX = None

_ROSTER_CACHE: list[dict] | None = None
_ROSTER_MTIME: float = 0.0

# Per-run observability (read by on_pre_llm_call after suggest_skill returns).
_LAST_INFO: dict = {}
_RUN: dict = {"cost": 0.0, "calls": 0}

# Outcome taxonomy (superset of jev-skill-router's, merge-compatible):
# router logs only suggested|silent|error with reason no_fit for every
# silent-no-skip case. Ours distinguishes gate|empty_shortlist|fits|
# none_selected|api_error — a merge script maps them back to no_fit.
OUTCOME_SUGGESTED = "suggested"
OUTCOME_SILENT = "silent"
OUTCOME_ERROR = "error"


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _events_path() -> Path:
    """Runtime events live in the logs dir (Ali's standard, same place the
    router put its log); the plugin dir holds code only."""
    return _hermes_home() / "logs" / "jev-suggest-events.jsonl"


def _log_event(ev: dict) -> None:
    """Append one structured event. Never logs user text (PII-safe)."""
    try:
        p = _events_path()
        if p.exists() and p.stat().st_size > 5 * 1024 * 1024:
            return  # stop growing; rotate manually
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **ev}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _disabled(settings: dict | None, ctx) -> bool:
    """Runtime kill switch: mode 'off' (env var removed 2026-09-28).
    Explicit settings win; no ctx and no settings -> not disabled (fail-open)."""
    mode = (settings or {}).get("mode")
    if mode is None:
        try:
            mode = _from_ctx(ctx, "mode", None)
        except Exception:
            return False
    return str(mode or "").strip().lower() == "off"


# ---- Settings: config.yaml first (via ctx), env as fallback ----

def _from_ctx(ctx, key: str, default):
    try:
        val = ctx.get_config(key, default)
        return default if val is None or val == "" else val
    except Exception:
        return default


def _num_from_ctx(ctx, key: str, default, cast):
    try:
        return cast(_from_ctx(ctx, key, default))
    except (TypeError, ValueError):
        return default


def _settings(ctx=None) -> dict:
    """Read plugin settings. config.yaml wins when ctx is bound, else env."""
    if ctx is None:
        return {
            "mode": os.environ.get("JEV_SUGGEST_MODE", "auto").strip().lower(),
            "gate": float(os.environ.get("JEV_SUGGEST_GATE", "0.30")),
            "fits": float(os.environ.get("JEV_SUGGEST_FITS", "0.30")),
            "shortlist": int(os.environ.get("JEV_SUGGEST_SHORTLIST", "3")),
            "excerpt": int(os.environ.get("JEV_SUGGEST_EXCERPT", "700")),
            "timeout_s": float(os.environ.get("JEV_SUGGEST_TIMEOUT", "4.0")),
            "suggest_chars": int(os.environ.get("JEV_SUGGEST_MAX_STATE", "4000")),
            "max_skills": int(os.environ.get("JEV_SUGGEST_MAX_SKILLS", "300")),
            "chunk": int(os.environ.get("JEV_SUGGEST_CHUNK", str(DEFAULT_CHUNK))),
            "retry_max_wait_s": float(os.environ.get("JEV_SUGGEST_RETRY_MAX_WAIT", "2.0")),
            "breaker_threshold": int(os.environ.get("JEV_SUGGEST_BREAKER_THRESHOLD", "3")),
            "breaker_cooldown_s": float(os.environ.get("JEV_SUGGEST_BREAKER_COOLDOWN", "120")),
            "min_interval_s": float(os.environ.get("JEV_SUGGEST_MIN_INTERVAL", "0.25")),
            "cache_seconds": int(os.environ.get("JEV_SUGGEST_CACHE_SECONDS", "300")),
            "openrouter_model": os.environ.get("JEV_SUGGEST_MODEL", DEFAULT_MODEL),
            "openrouter_base_url": os.environ.get(
                "JEV_SUGGEST_ENDPOINT", DEFAULT_BASE_URL + "/decisions"
            ),
        }
    return {
        "mode": str(_from_ctx(ctx, "mode", "off") or "off").strip().lower(),
        "gate": _num_from_ctx(ctx, "gate", 0.30, float),
        "fits": _num_from_ctx(ctx, "fits", 0.30, float),
        "shortlist": _num_from_ctx(ctx, "shortlist", 3, int),
        "excerpt": _num_from_ctx(ctx, "excerpt", 700, int),
        "timeout_s": _num_from_ctx(ctx, "timeout_s", 4.0, float),
        "suggest_chars": _num_from_ctx(ctx, "suggest_chars", 4000, int),
        "max_skills": _num_from_ctx(ctx, "max_skills", 300, int),
        "chunk": _num_from_ctx(ctx, "chunk", DEFAULT_CHUNK, int),
        "retry_max_wait_s": _num_from_ctx(ctx, "retry_max_wait_s", 2.0, float),
        "breaker_threshold": _num_from_ctx(ctx, "breaker_threshold", 3, int),
        "breaker_cooldown_s": _num_from_ctx(ctx, "breaker_cooldown_s", 120, float),
        "min_interval_s": _num_from_ctx(ctx, "min_interval_s", 0.25, float),
        "cache_seconds": _num_from_ctx(ctx, "cache_seconds", 300, int),
        "openrouter_model": str(
            _from_ctx(ctx, "openrouter_model", DEFAULT_MODEL) or DEFAULT_MODEL
        ).strip(),
        "openrouter_base_url": str(
            _from_ctx(ctx, "openrouter_base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
        ).strip(),
    }


def _resolve_model(raw: str) -> str:
    """Allowlist gate: unknown slugs fall back to DEFAULT_MODEL (fail-open)."""
    model = (raw or "").strip()
    if model in ALLOWED_MODELS:
        return model
    logger.warning("jev-suggest: model %r not in allowlist, using %r", raw, DEFAULT_MODEL)
    return DEFAULT_MODEL


def _endpoint(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    if base.endswith("/decisions"):
        return base
    return base + "/decisions"


def _openrouter_key(*, select: bool = False) -> str:
    """OPENROUTER_API_KEY from env, else Hermes credential pool (auth.json)."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    try:
        from agent.credential_pool import load_pool  # type: ignore
    except (ImportError, ModuleNotFoundError):
        load_pool = None
    if load_pool is not None:
        try:
            pool = load_pool("openrouter")
            entry = pool.select() if select else pool.peek()
            if entry is not None:
                return str(getattr(entry, "runtime_api_key", "") or "").strip()
        except Exception as exc:
            logger.debug("jev-suggest: credential pool unavailable: %s", exc)
        return ""
    try:
        data = json.loads((_hermes_home() / "auth.json").read_text())
    except (OSError, ValueError, TypeError):
        return ""
    for item in (data.get("credential_pool", {}) or {}).get("openrouter", []) or []:
        token = str((item or {}).get("access_token") or "").strip()
        if token:
            return token
    return ""


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
                return desc[:500]
        return ""
    return ""


def _load_roster(settings: dict) -> list[dict]:
    """Build once per process: name + short desc + full desc + excerpt."""
    global _ROSTER_CACHE, _ROSTER_MTIME
    excerpt_chars = int(settings.get("excerpt", 700) or 700)
    max_skills = int(settings.get("max_skills", 300) or 300)
    home = _hermes_home()
    skills_root = home / "skills"
    if _ROSTER_CACHE is not None:
        return _ROSTER_CACHE
    roster: list[dict] = []
    if not skills_root.is_dir():
        return roster
    for skill_md in sorted(skills_root.glob("*/*/SKILL.md")):
        if len(roster) >= max_skills:
            break
        name = skill_md.parent.name
        try:
            full_text = skill_md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        desc = _read_frontmatter_description(skill_md) or full_text[:200].replace("\n", " ")
        body_start = full_text.find("---", 3)
        body = full_text[body_start:body_start + excerpt_chars] if body_start != -1 else full_text[:excerpt_chars]
        roster.append({
            "name": name,
            "short": desc[:60],
            "full": desc[:500],
            "excerpt": body[:excerpt_chars],
        })
    _ROSTER_CACHE = roster
    _ROSTER_MTIME = time.time()
    return roster


def _retry_after_s(value, now_wall: float) -> float | None:
    """Parse a Retry-After header: seconds or an HTTP date. Garbage -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        wait = float(text)
    except ValueError:
        pass
    else:
        return wait if math.isfinite(wait) else None
    try:
        moment = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = moment.timestamp() - now_wall
    return delta if math.isfinite(delta) else None


def _breaker_open(endpoint: str, settings: dict) -> bool:
    state = _BREAKER.get(endpoint)
    return (
        state is not None
        and state[0] >= int(settings.get("breaker_threshold", 3) or 3)
        and time.monotonic() < state[1]
    )


def _note_ratelimit(endpoint: str, settings: dict) -> None:
    state = _BREAKER.get(endpoint) or [0, 0.0]
    state[0] += 1
    if state[0] >= int(settings.get("breaker_threshold", 3) or 3):
        state[1] = time.monotonic() + float(settings.get("breaker_cooldown_s", 120) or 120)
    _BREAKER[endpoint] = state


def _note_success(endpoint: str) -> None:
    _BREAKER.pop(endpoint, None)


def _pace(endpoint: str, settings: dict) -> None:
    """Space outgoing calls by min_interval_s (per process, per endpoint)."""
    gap = float(settings.get("min_interval_s", 0.25) or 0)
    now = time.monotonic()
    last = _PACE_LAST.get(endpoint)
    if gap > 0 and last is not None and now - last < gap:
        time.sleep(gap - (now - last))
        now = time.monotonic()
    _PACE_LAST[endpoint] = now


def _post_json(url: str, data: bytes, headers: dict, settings: dict):
    """POST with a single rate-limit retry. Returns the body or None."""
    timeout = float(settings.get("timeout_s", 4.0) or 4.0)
    max_wait = float(settings.get("retry_max_wait_s", 2.0) or 0)
    try:
        _pace(url, settings)
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with _urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code not in RATE_LIMIT_CODES:
            logger.debug("jev-suggest: POST failed (%s): %s", type(exc).__name__, exc)
            return None
        _note_ratelimit(url, settings)
        wait = _retry_after_s(
            exc.headers.get("retry-after") if exc.headers else None,
            time.time(),
        )
        if wait is None or wait > max_wait:
            return None  # fail-open: no (or too long a) wait instructed
        if wait > 0:
            time.sleep(wait)
        try:
            _pace(url, settings)
            req = urllib.request.Request(url, data=data, method="POST", headers=headers)
            with _urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
        except Exception as exc2:  # counted once per call; no double note
            logger.debug("jev-suggest: retry failed (%s): %s", type(exc2).__name__, exc2)
            return None
    except Exception as exc:  # timeout, network, parse — fail-open
        logger.debug("jev-suggest: POST failed (%s): %s", type(exc).__name__, exc)
        return None
    _note_success(url)
    return body


def _decide(state: str, questions: dict, settings: dict) -> dict | None:
    api_key = _openrouter_key()
    if not api_key:
        logger.warning("jev-suggest: OPENROUTER_API_KEY missing, skipping")
        return None
    _RUN["calls"] += 1
    model = _resolve_model(str(settings.get("openrouter_model", DEFAULT_MODEL)))
    url = _endpoint(str(settings.get("openrouter_base_url", DEFAULT_BASE_URL)))
    if _breaker_open(url, settings):
        logger.debug("jev-suggest: breaker open for %s; skipping", url)
        return None
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    if len(body) > MAX_PAYLOAD_BYTES:
        logger.debug("jev-suggest: payload %d bytes over cap; failing open", len(body))
        return None
    cache_seconds = int(settings.get("cache_seconds", 300) or 0)
    cache_key = ""
    if cache_seconds > 0:
        cache_key = hashlib.sha256(
            json.dumps([model, state, questions],
                       sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        hit = _RESP_CACHE.get(cache_key)
        if hit and hit[0] > time.time():
            return hit[1]
    data = _post_json(url, body, {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, settings)
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    try:
        _RUN["cost"] += float(((data.get("usage") or {}).get("cost")) or 0.0)
    except (TypeError, ValueError):
        pass
    if cache_seconds > 0:
        _RESP_CACHE[cache_key] = (time.time() + cache_seconds, data)
        if len(_RESP_CACHE) > CACHE_MAX_ENTRIES:
            _RESP_CACHE.pop(next(iter(_RESP_CACHE)))  # oldest-inserted first
    return data


def _is_trivial(text: str) -> bool:
    t = text.strip()
    if len(t) < 8:
        return True
    lowered = t.lower()
    return lowered in {"halo", "hai", "hi", "hello", "thanks", "terima kasih", "ok", "oke", "sip"}


def _skip_reason(text: str, suggest_chars: int) -> str | None:
    """Why this turn is left alone (None = eligible). Ported from jev-skill-router."""
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    if stripped.startswith("/"):
        return "slash"  # slash commands pick their own flow
    if suggest_chars and len(stripped) > suggest_chars:
        return "too_long"  # long pastes are not routing questions
    if "<skill_relevance>" in stripped:
        return "already_routed"  # already routed this turn
    if _is_trivial(stripped):
        return "trivial"
    return None


def _chunk_items(items: list, size: int) -> list[list]:
    """Split roster into groups of <= size (API caps one Choice at 255)."""
    if size < 1:
        size = DEFAULT_CHUNK
    if size > MAX_CHOICES:
        raise ValueError(f"chunk size {size} exceeds the API cap of {MAX_CHOICES}")
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def suggest_skill(user_text: str, settings: dict | None = None) -> str:
    """Return a skill name or '' (suggest nothing). Never raises."""
    try:
        _LAST_INFO.clear()
        _RUN["cost"] = 0.0
        _RUN["calls"] = 0
        st = settings or _settings(_CTX)
        if _disabled(settings, _CTX):
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "disabled"})
            return ""
        if _is_trivial(user_text):
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "trivial"})
            return ""
        roster = _load_roster(st)
        if not roster:
            return ""
        shortlist = int(st.get("shortlist", 3) or 3)
        gate_th = float(st.get("gate", 0.30))
        fits_th = float(st.get("fits", 0.30))
        suggest_chars = int(st.get("suggest_chars", 4000) or 4000)
        try:
            chunk = int(st.get("chunk", DEFAULT_CHUNK) or DEFAULT_CHUNK)
        except (TypeError, ValueError):
            chunk = DEFAULT_CHUNK
        if chunk < 1 or chunk > MAX_CHOICES:
            logger.warning("jev-suggest: chunk %r out of range, using %d", st.get("chunk"), DEFAULT_CHUNK)
            chunk = DEFAULT_CHUNK
        model_now = _resolve_model(str(st.get("openrouter_model", DEFAULT_MODEL)))
        if model_now in SOLAR_MODELS and chunk > SOLAR_MAX_CHOICES:
            logger.warning("jev-suggest: %s caps Choice at 26 labels incl. none_of_these; "
                           "clamping chunk %d -> %d", model_now, chunk, SOLAR_MAX_CHOICES)
            chunk = SOLAR_MAX_CHOICES
        if not WARN_SOLAR_GATE_ONCE["done"]:
            _warn = _solar_config_warning(model_now, st)
            if _warn:
                WARN_SOLAR_GATE_ONCE["done"] = True
                logger.warning("jev-suggest: %s", _warn)
        reason = _skip_reason(user_text, suggest_chars)
        if reason:
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": reason})
            return ""
        state = user_text.strip()[:suggest_chars]

        # ---- Call 1: skim per chunk + gate (ported from jev-skill-router) ----
        # One Choice per chunk (API cap 255); every chunk but the best may bow
        # out via none_of_these. Gate questions ride on the first chunk only.
        groups = _chunk_items(roster, chunk)
        chunked = len(groups) > 1
        valid = {s["name"] for s in roster}
        per_chunk: list[list[tuple[str, float]]] = []
        none_pressure: list[float] = []
        answers1: dict = {}
        for idx, group in enumerate(groups):
            criteria = {s["name"]: s["short"] or s["name"] for s in group}
            if chunked:
                criteria[NONE_OPTION] = "None of these skills fit the request."
            index_lines = "\n".join(f"- {s['name']}: {s['short']}" for s in group)
            q1: dict = {
                "which_skill": {
                    "type": "choice",
                    "instructions": (
                        "Which skill best fits the user request? "
                        "Choose from the skill index below. "
                        "If none fits, choose the closest anyway; the need-checks decide."
                        f"\nSkill index:\n{index_lines}"
                    ),
                    "criteria": criteria,
                },
            }
            if idx == 0:
                q1.update({
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
                })
            r1 = _decide(state, q1, st)
            if not r1:
                _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "api_error"})
                return ""
            ans = r1.get("answers", {})
            if idx == 0:
                answers1 = ans
            probs = (ans.get("which_skill", {}) or {}).get("probabilities", {}) or {}
            ranked = sorted(
                ((n, float(p)) for n, p in probs.items()
                 if n != NONE_OPTION and n in valid),
                key=lambda kv: float(kv[1]),
                reverse=True,
            )
            if not ranked:
                which = ans.get("which_skill", {}) or {}
                sel = which.get("choice") or which.get("selected")
                if sel and sel != NONE_OPTION and sel in valid:
                    ranked = [(sel, 1.0)]
            per_chunk.append(ranked)
            try:
                none_pressure.append(float(probs.get(NONE_OPTION, 0.0)))
            except (TypeError, ValueError):
                none_pressure.append(0.0)
        need_scores = [
            float(answers1.get(k, {}).get("noul", 0.0))
            for k in ("need_act", "need_steps")
        ]
        just_talk = float(answers1.get("just_talk", {}).get("noul", 0.0))
        gate = (sum(need_scores) / len(need_scores) if need_scores else 0.0) * (1.0 - just_talk)
        # Best chunk nominates first: with a small shortlist the later chunk's
        # winner would otherwise be cut by chunk order.
        best_chunk = max(
            range(len(per_chunk)),
            key=lambda i: per_chunk[i][0][1] if per_chunk[i] else -1.0,
        )
        shortlisted: list[str] = []
        for idx in [best_chunk] + [i for i in range(len(per_chunk)) if i != best_chunk]:
            if idx != best_chunk and none_pressure[idx] >= NONE_THRESHOLD:
                continue  # that chunk says nothing fits: noise
            for name, _ in per_chunk[idx][:shortlist]:
                if name not in shortlisted:
                    shortlisted.append(name)
        shortlisted = shortlisted[:shortlist]
        logger.info("jev-suggest: call1 gate=%.2f chunks=%d top=%s", gate, len(groups), shortlisted)
        _LAST_INFO.update({"gate": round(gate, 3), "chunks": len(groups), "top": shortlisted})
        if gate < gate_th:
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "gate"})
            return ""
        if not shortlisted:
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "empty_shortlist"})
            return ""
        ranked = [(n, 0.0) for n in shortlisted]

        by_name = {s["name"]: s for s in roster}
        top = [by_name[n] for n, _ in ranked if n in by_name]
        if not top:
            return ""

        # ---- Call 2: read top-N properly, may reject all ----
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
                "criteria": {**{s["name"]: s["full"] or s["name"] for s in top},
                             NONE_OPTION: "None of these skills fit the request."},
            },
        }
        for s in top:
            q2[f"fits_{s['name']}"] = {
                "type": "noul",
                "instructions": f"Does skill '{s['name']}' truly do what the request asks?",
                "criteria": {"true": "Yes, it does this", "false": "No, it does something else"},
            }
        r2 = _decide(state, q2, st)
        if not r2:
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "api_error"})
            return ""
        answers2 = r2.get("answers", {})
        best = answers2.get("best_of_shortlist", {}).get("choice") or answers2.get("best_of_shortlist", {}).get("selected")
        if not best or best == NONE_OPTION or best not in by_name:
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "none_selected"})
            return ""
        fit = float(answers2.get(f"fits_{best}", {}).get("noul", 0.0))
        best_ans = answers2.get("best_of_shortlist", {}) or {}
        try:
            probability = float((best_ans.get("probabilities", {}) or {}).get(best, 0.0))
        except (TypeError, ValueError):
            probability = 0.0
        conf_raw = best_ans.get("confidence")
        confidence = None
        try:
            confidence = float(conf_raw) if conf_raw is not None else None
        except (TypeError, ValueError):
            confidence = None
        logger.info("jev-suggest: call2 best=%s fit=%.2f", best, fit)
        _LAST_INFO.update({"best": best, "fit": round(fit, 3),
                           "probability": round(probability, 3),
                           "confidence": confidence})
        if fit < fits_th:
            _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": "fits"})
            return ""
        _LAST_INFO.update({"outcome": OUTCOME_SUGGESTED, "reason": None})
        return best
    except Exception as exc:  # absolute fail-open
        logger.warning("jev-suggest: suggest failed: %s", type(exc).__name__)
        _LAST_INFO.update({"outcome": OUTCOME_ERROR, "reason": type(exc).__name__})
        return ""


def _log_suggest(settings: dict, kwargs: dict, dt: float, text: str,
                 origin: str, winner: str | None = None) -> None:
    """Write one unified suggest event (router fields + ours). Never raises."""
    try:
        _log_event({
            "event": "suggest",
            "origin": origin,
            "mode": str(settings.get("mode", "?")),
            "model": _resolve_model(str(settings.get("openrouter_model", DEFAULT_MODEL))),
            "session_id": str(kwargs.get("session_id") or ""),
            "turn_id": str(kwargs.get("turn_id") or ""),
            "outcome": _LAST_INFO.get("outcome"),
            "reason": _LAST_INFO.get("reason"),
            "winner": winner or None,
            "gate": _LAST_INFO.get("gate"),
            "probability": _LAST_INFO.get("probability"),
            "confidence": _LAST_INFO.get("confidence"),
            "fit": _LAST_INFO.get("fit"),
            "top": _LAST_INFO.get("top"),
            "calls": _RUN.get("calls", 0),
            "chunks": _LAST_INFO.get("chunks"),
            "latency_s": round(dt, 2),
            "cost": round(_RUN.get("cost", 0.0), 8),
            "state_chars": len(text),
        })
    except Exception:
        pass


def make_hook_handler(ctx):
    """Build the pre_llm_call handler. Reads config per turn, never raises."""

    def on_pre_llm_call(**kwargs) -> dict:
        try:
            st = _settings(ctx)
            if _disabled(st, ctx):
                return {}
            if st["mode"] == "off":
                return {}
            if st["mode"] == "auto" and not _openrouter_key():
                return {}
            if st["mode"] not in ("auto", "on"):
                return {}
            user_message = kwargs.get("user_message", "")
            text = user_message if isinstance(user_message, str) else str(user_message or "")
            skip = _skip_reason(text, int(st.get("suggest_chars", 4000) or 4000))
            if skip:
                _LAST_INFO.clear()
                _LAST_INFO.update({"outcome": OUTCOME_SILENT, "reason": skip})
                _log_suggest(st, kwargs, 0.0, text, origin="hook")
                return {}
            t0 = time.time()
            winner = suggest_skill(text, st)
            dt = time.time() - t0
            logger.info("jev-suggest: turn done in %.2fs winner=%s", dt, winner or "-")
            _log_suggest(st, kwargs, dt, text, origin="hook", winner=winner)
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

    return on_pre_llm_call


# Back-compat: loader calls on_pre_llm_call without ctx in old wiring.
def on_pre_llm_call(**kwargs) -> dict:
    if _CTX is None:
        st = _settings(None)
        if _disabled(st, None) or not _openrouter_key():
            return {}
        try:
            text = kwargs.get("user_message", "")
            text = text if isinstance(text, str) else str(text or "")
            if _is_trivial(text):
                return {}
            winner = suggest_skill(text, st)
            if not winner:
                return {}
            return {"context": (
                "<skill_relevance>\n"
                f"Relevant to the current request: {winner}. "
                "Ignore this if it does not fit what the user actually asked for.\n"
                "</skill_relevance>"
            )}
        except Exception:
            return {}
    return make_hook_handler(_CTX)(**kwargs)


def on_post_tool_call(**kwargs) -> None:
    """Record every skill_view load so suggestions can be matched to outcomes."""
    try:
        if _disabled(None, _CTX):
            return
        tool_name = str(kwargs.get("tool_name") or "")
        if not tool_name.startswith("skill"):
            return
        args = kwargs.get("args") or {}
        name = args.get("name") if isinstance(args, dict) else None
        if not name:
            return
        _log_event({
            "event": "skill_tool",
            "session_id": str(kwargs.get("session_id") or ""),
            "turn_id": str(kwargs.get("turn_id") or ""),
            "tool": tool_name,
            "skill": str(name),
        })
    except Exception:
        pass


# ---- CLI (mirrors jev-skill-router: on|off|auto|status|suggest|check) ----

_CLI_HELP = "Skill suggestion via decision model on OpenRouter (on|off|auto|status|suggest|check)"
_CLI_DESCRIPTION = (
    "Names the one skill from the live roster that fits the current turn, "
    "via a decision model on OpenRouter. Says nothing when nothing fits."
)


def setup_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="jev_suggest_action")
    subs.add_parser("on", help="Suggest on every eligible turn")
    subs.add_parser("off", help="Never suggest (default; nothing leaves the machine)")
    subs.add_parser("auto", help="Suggest when OPENROUTER_API_KEY is present")
    subs.add_parser("status", help="Show mode, roster, thresholds, model, log path")
    suggest = subs.add_parser("suggest", help="Run one live suggestion on <text>")
    suggest.add_argument("text", help="The request text to route")
    suggest.add_argument("--json", dest="as_json", action="store_true",
                         help="Print the decision as JSON (for scripting)")
    subs.add_parser("check", help="Verify key, roster and log writability (no inference spent)")


WARN_SOLAR_GATE_ONCE = {"done": False}


def _solar_config_warning(model: str, settings: dict) -> str | None:
    """Solar cannot inherit Jev's calibration (probe 2026-09-29): its gate scores
    run ~2.4x lower, so a Jev-tuned gate silences real work turns."""
    if model not in SOLAR_MODELS:
        return None
    try:
        gate = float(settings.get("gate", 0.30))
    except (TypeError, ValueError):
        gate = 0.30
    if gate > 0.15:
        return (f"{model} with gate {gate:.2f} looks untuned: Solar's gate scores run "
                "~2.4x lower than Jev's (work median 0.44 vs 0.70, chit-chat ~0.01). "
                "Use gate 0.05-0.10; expect ~8s and 9 calls per turn on a 180+ skill "
                "roster (25 labels/chunk incl. none_of_these).")
    return None


def _cmd_status(ctx, settings: dict) -> int:
    skills = _load_roster(settings)
    key = "present" if _openrouter_key() else "missing"
    print(f"mode:          {settings['mode']}")
    print(f"roster:        {len(skills)} skills from {_hermes_home() / 'skills'}")
    print(f"gate/fits:     {settings['gate']}/{settings['fits']}")
    print(f"shortlist:     {settings['shortlist']}  excerpt: {settings['excerpt']}  "
          f"suggest_chars: {settings['suggest_chars']}  max_skills: {settings['max_skills']}")
    print(f"chunk:         {settings.get('chunk', 240)} (API cap {MAX_CHOICES} per Choice)")
    print(f"timeout:       {settings['timeout_s']}s")
    print(f"ratelimit:     retry_max_wait={settings.get('retry_max_wait_s', 2.0)}s "
          f"breaker={settings.get('breaker_threshold', 3)}x/{settings.get('breaker_cooldown_s', 120)}s "
          f"pace={settings.get('min_interval_s', 0.25)}s cache={settings.get('cache_seconds', 300)}s")
    print(f"model:         {_resolve_model(str(settings.get('openrouter_model', DEFAULT_MODEL)))}")
    print(f"endpoint:      {_endpoint(str(settings.get('openrouter_base_url', DEFAULT_BASE_URL)))}")
    print(f"key:           OPENROUTER_API_KEY={key}")
    print(f"allowlist:     {sorted(ALLOWED_MODELS)}")
    print(f"log:           {_events_path()}")
    warn = _solar_config_warning(
        _resolve_model(str(settings.get("openrouter_model", DEFAULT_MODEL))), settings)
    if warn:
        print(f"WARNING:       {warn}")
    return 0


def _cmd_suggest(ctx, settings: dict, args) -> int:
    as_json = bool(getattr(args, "as_json", False))
    t0 = time.time()
    winner = suggest_skill(args.text, settings)
    dt = time.time() - t0
    _log_suggest(settings, {}, dt, args.text, origin="cli-suggest", winner=winner)
    if not winner:
        if as_json:
            print(json.dumps({"skill": None, **_LAST_INFO}, ensure_ascii=False))
        else:
            print(f"no suggestion ({_LAST_INFO.get('outcome')}/{_LAST_INFO.get('reason')})")
        return 0
    if as_json:
        print(json.dumps({"skill": winner, **_LAST_INFO,
                          "latency_s": round(dt, 2)}, ensure_ascii=False))
    else:
        print(f"suggested: {winner} "
              f"(gate={_LAST_INFO.get('gate')} fit={_LAST_INFO.get('fit')} "
              f"{dt:.2f}s)")
    return 0


def _cmd_check(ctx, settings: dict) -> int:
    ok = True
    has_key = bool(_openrouter_key())
    print(f"key:    {'OK (OPENROUTER_API_KEY present)' if has_key else 'SILENT (no OPENROUTER_API_KEY)'}")
    ok = ok and has_key
    try:
        skills = _load_roster(settings)
        print(f"roster: OK ({len(skills)} skills)")
    except Exception as exc:
        print(f"roster: FAIL ({exc})")
        ok = False
    try:
        p = _events_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(p.parent, os.W_OK)
        print(f"log:    {'OK (writable ' + str(p) + ')' if writable else 'FAIL (not writable ' + str(p) + ')'}")
        ok = ok and writable
    except Exception as exc:
        print(f"log:    FAIL ({exc})")
        ok = False
    model = _resolve_model(str(settings.get("openrouter_model", DEFAULT_MODEL)))
    print(f"model:  {model} @ {_endpoint(str(settings.get('openrouter_base_url', DEFAULT_BASE_URL)))}")
    return 0 if ok else 1


def run_cli(ctx, args: argparse.Namespace) -> int:
    action = getattr(args, "jev_suggest_action", None)
    if action in ("on", "off", "auto"):
        try:
            ctx.set_config("mode", action)
        except Exception as exc:
            print(f"could not set mode: {exc}")
            return 1
        print(f"jev-suggest: mode -> {action}")
        return 0
    try:
        settings = _settings(ctx)
    except Exception as exc:
        print(f"could not read settings: {exc}")
        return 1
    if action == "status":
        return _cmd_status(ctx, settings)
    if action == "suggest":
        return _cmd_suggest(ctx, settings, args)
    if action == "check":
        return _cmd_check(ctx, settings)
    print("Usage: hermes jev-suggest {on|off|auto|status|suggest <text> [--json]|check}")
    return 2


def _cli_command(args: argparse.Namespace) -> int:
    return run_cli(_CTX, args)


def register(ctx) -> None:
    """Wire the hooks and the CLI command (called once by the plugin loader)."""
    global _CTX
    _CTX = ctx
    ctx.register_hook("pre_llm_call", make_hook_handler(ctx))
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_cli_command(COMMAND, _CLI_HELP, setup_cli, _cli_command,
                             description=_CLI_DESCRIPTION)
