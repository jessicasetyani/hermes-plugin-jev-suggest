"""Analyse the jev-suggest event log — funnel, gate, latency, cost, acceptance.

Usage:
  python3 scripts/log_stats.py [--log PATH] [--days N] [--json]

Reads ~/.hermes/logs/jev-suggest-events.jsonl by default, plus any rotated
jev-suggest-events-*.jsonl in the same directory. Acceptance uses the exact
`matched` flag written by skill_tool events (rows logged before 2026-09-29
predate that flag and are reported separately as "legacy").
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) \
    / "logs" / "jev-suggest-events.jsonl"


def load(path: Path, days: float = 0) -> list[dict]:
    files = [path] + [Path(p) for p in sorted(
        glob.glob(str(path.with_name("jev-suggest-events-*.jsonl"))))]
    rows: list[dict] = []
    cutoff = time.time() - days * 86400 if days else 0
    for f in files:
        if not f.exists():
            continue
        for line in f.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if cutoff and float(row.get("ts") or 0) < cutoff:
                continue
            rows.append(row)
    return sorted(rows, key=lambda r: r.get("ts") or 0)


def pct(num: int, den: int) -> str:
    return f"{100 * num / den:.0f}%" if den else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--days", type=float, default=0, help="only the last N days")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    a = ap.parse_args()

    rows = load(Path(a.log), a.days)
    suggests = [r for r in rows if r.get("event") == "suggest"]
    tools = [r for r in rows if r.get("event") == "skill_tool"]
    modern = [r for r in suggests if r.get("origin")]           # unified schema
    legacy = [r for r in suggests if not r.get("origin")]
    linked = [r for r in tools if r.get("matched") is not None]  # post-linkage rows

    gates = sorted(float(r["gate"]) for r in modern if r.get("gate") is not None)
    lat = sorted(float(r["latency_s"]) for r in modern
                 if r.get("latency_s") and float(r["latency_s"]) > 0)
    suggested = [r for r in modern if r.get("outcome") == "suggested"]
    outcomes = Counter((r.get("outcome"), r.get("reason")) for r in modern)
    accepted = sum(1 for r in linked if r.get("matched") is True)
    cost = sum(float(r.get("cost") or 0) for r in modern)

    if a.json:
        print(json.dumps({
            "rows": len(rows), "suggest_events": len(suggests),
            "modern": len(modern), "legacy": len(legacy),
            "outcomes": {f"{k[0]}/{k[1]}": v for k, v in outcomes.items()},
            "gate_p50": statistics.median(gates) if gates else None,
            "latency_p50": statistics.median(lat) if lat else None,
            "latency_p95": lat[int(len(lat) * 0.95) - 1] if lat else None,
            "suggestions": len(suggested),
            "acceptance": f"{accepted}/{len(linked)}" if linked else "no linked rows yet",
            "cost_usd": round(cost, 6), "models": dict(Counter(r.get("model") for r in modern)),
            "sessions": len({r.get("session_id") for r in modern}),
            "top_winners": Counter(r["winner"] for r in suggested if r.get("winner")).most_common(5),
        }, indent=2))
        return 0

    first = datetime.fromtimestamp(rows[0]["ts"], tz=timezone.utc).astimezone() if rows else None
    last = datetime.fromtimestamp(rows[-1]["ts"], tz=timezone.utc).astimezone() if rows else None
    print(f"log            : {a.log}")
    if rows:
        assert first is not None and last is not None
        print(f"window         : {first:%d %b %H:%M} -> {last:%d %b %H:%M} "
              f"({(last - first).days}d)")
    print(f"rows           : {len(rows)} ({len(suggests)} suggest, {len(tools)} skill_tool)")
    print(f"schema         : {len(modern)} unified, {len(legacy)} legacy (pre-v0.2.0)")
    print(f"sessions       : {len({r.get('session_id') for r in modern})}")
    print(f"models         : {dict(Counter(r.get('model') for r in modern))}")
    print("\n-- funnel --")
    for key, val in sorted(outcomes.items(), key=lambda kv: -kv[1]):
        print(f"  {str(key[0]):10s} {str(key[1]):16s} {val}")
    print("\n-- gate --")
    if gates:
        print(f"  n={len(gates)} p50={statistics.median(gates):.3f} "
              f"min={gates[0]:.3f} max={gates[-1]:.3f}")
        buckets = [(0, .1), (.1, .15), (.15, .2), (.2, .3), (.3, .55), (.55, 1.01)]
        print("  " + "  ".join(f"{lo:.2f}-{hi:.2f}:{sum(1 for g in gates if lo <= g < hi)}"
                               for lo, hi in buckets))
    print("\n-- latency --")
    if lat:
        print(f"  n={len(lat)} p50={statistics.median(lat):.2f}s "
              f"p95={lat[int(len(lat) * 0.95) - 1]:.2f}s max={lat[-1]:.2f}s")
    print(f"\ncost           : ${cost:.5f}"
          + (f" (${cost / len(modern):.6f}/turn)" if modern else ""))
    print(f"suggestions    : {len(suggested)}")
    print("acceptance     : " + (f"{accepted}/{len(linked)} = {pct(accepted, len(linked))}"
                                if linked else "no linked rows yet (needs v0.2.0+ skill_tool rows)"))
    if suggested:
        print("top winners    : " + ", ".join(
            f"{n}({c})" for n, c in Counter(r["winner"] for r in suggested if r.get("winner")).most_common(5)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())