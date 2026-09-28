"""One-shot merge: router log + suggest events -> unified history (read-only inputs).

Reads:
  <HERMES_HOME>/logs/jev-skill-router.log
  <HERMES_HOME>/logs/jev-suggest-events.jsonl
Writes (default):
  docs/history/merged-decisions.jsonl  (next to this repo)

Unified row: ts ISO, origin, event, mode, model, session_id, turn_id,
outcome, reason, winner, gate, probability, confidence, fit, top,
calls, latency_s, cost. Reasons kept verbatim (router collapses gate-class
to no_fit; suggest distinguishes gate|empty_shortlist|fits|none_selected|
api_error). Router rows: model "unknown (pre-merge, backend auto)",
session/turn null. Old suggest rows: missing new fields -> null.
Originals untouched. Stdlib only.

Run:  python3 scripts/merge_router_log.py [--router P] [--suggest P] [--out P]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _hermes_home() -> Path:
    import os
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _to_iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    s = str(ts)
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        return s


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def merge_router_row(r: dict) -> dict:
    oc = r.get("outcome")
    lat_ms = _num(r.get("latency_ms"))
    return {
        "ts": _to_iso(r.get("ts")),
        "origin": "router-" + str(r.get("source") or "?"),
        "event": "suggest",
        "mode": r.get("mode"),
        "model": "unknown (pre-merge, backend auto)",
        "session_id": None,
        "turn_id": None,
        "outcome": oc,
        "reason": r.get("reason"),
        "winner": r.get("skill"),
        "gate": _num(r.get("gate")),
        "probability": _num(r.get("probability")),
        "confidence": _num(r.get("confidence")),
        "fit": None,
        "top": None,
        "calls": r.get("calls"),
        "latency_s": round(lat_ms / 1000, 3) if lat_ms is not None else None,
        "cost": _num(r.get("cost")),
    }


def merge_suggest_row(r: dict) -> dict:
    out = {
        "ts": _to_iso(r.get("ts")),
        "origin": "jev-suggest-" + str(r.get("origin") or r.get("event") or "?"),
        "event": r.get("event"),
        "mode": r.get("mode"),
        "model": r.get("model"),
        "session_id": r.get("session_id"),
        "turn_id": r.get("turn_id"),
        "outcome": r.get("outcome"),
        "reason": r.get("reason"),
        "winner": r.get("winner"),
        "gate": _num(r.get("gate")),
        "probability": _num(r.get("probability")),
        "confidence": _num(r.get("confidence")),
        "fit": _num(r.get("fit")),
        "top": r.get("top"),
        "calls": r.get("calls"),
        "latency_s": _num(r.get("latency_s")),
        "cost": _num(r.get("cost")),
    }
    if r.get("event") == "skill_tool":  # passthrough, correlation event
        out.update({"tool": r.get("tool"), "skill": r.get("skill")})
    else:
        out["state_chars"] = r.get("state_chars")
    return out


def read_jsonl(p: Path) -> list[dict]:
    rows = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge router + suggest logs (read-only inputs).")
    ap.add_argument("--router", default=str(_hermes_home() / "logs" / "jev-skill-router.log"))
    ap.add_argument("--suggest", default=str(_hermes_home() / "logs" / "jev-suggest-events.jsonl"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                         / "docs" / "history" / "merged-decisions.jsonl"))
    a = ap.parse_args()
    router_rows = read_jsonl(Path(a.router))
    suggest_rows = read_jsonl(Path(a.suggest))
    merged = [merge_router_row(r) for r in router_rows] + \
             [merge_suggest_row(r) for r in suggest_rows]
    merged.sort(key=lambda r: r.get("ts") or "")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in merged:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    from collections import Counter
    print(f"router rows:  {len(router_rows)} "
          f"{Counter(r.get('outcome') for r in router_rows)}")
    print(f"suggest rows: {len(suggest_rows)} "
          f"{Counter((r.get('event'), r.get('outcome')) for r in suggest_rows)}")
    print(f"merged rows:  {len(merged)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
