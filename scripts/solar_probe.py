"""Comprehensive Solar Decide probe suite — run through the REAL plugin path.

Batteries:
  limits      route/primitive limits per model (choice/score/noul counts, state size)
  accuracy    top-1 accuracy with planted ground truth vs roster size N
  thresholds  gate/fit distributions on work / chit-chat / ambiguous turns
  e2e         production-shape end-to-end: Jev (1 call) vs Solar (clamped) vs hybrid

Raw results are written to docs/calibration/raw/<battery>-<ts>.json so the
report can cite exact rows. Nothing here writes to the live event log.

Run (key is injected by BWS, never echoed):
  P=29cb6471-a904-45f2-aebf-b46d00742866
  bws run --project-id $P --no-inherit-env -- python3 scripts/solar_probe.py <battery>
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PLUGIN = Path.home() / ".hermes" / "plugins" / "jev-suggest" / "__init__.py"
RAW_DIR = Path(__file__).resolve().parent.parent / "docs" / "calibration" / "raw"
BASE = "https://openrouter.ai/api/alpha/decisions"
SOLAR = "upstage/solar-decide"
JEV = "typesafe/jev-1.13"

spec = importlib.util.spec_from_file_location("jevs", PLUGIN)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE_SETTINGS = {
    "mode": "on", "gate": 0.30, "fits": 0.30, "shortlist": 3, "excerpt": 700,
    "timeout_s": 30.0, "suggest_chars": 4000, "max_skills": 300, "chunk": 240,
    "retry_max_wait_s": 2.0, "breaker_threshold": 99, "breaker_cooldown_s": 1,
    "min_interval_s": 0.0, "cache_seconds": 0,
    "openrouter_base_url": "https://openrouter.ai/api/alpha",
}


def settings(model: str, **over) -> dict:
    st = dict(BASE_SETTINGS, openrouter_model=model)
    st.update(over)
    return st


def raw_call(model: str, state, questions: dict, timeout: float = 40.0) -> dict:
    """Direct API call, outside the plugin — for limit probing only."""
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(
        BASE, data=body,
        headers={"Authorization": "Bearer " + (m._openrouter_key() or ""),
                 "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with m._urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return {"ok": True, "payload_bytes": len(body),
                "latency_s": round(time.monotonic() - t0, 2),
                "answers": data.get("answers"), "usage": data.get("usage")}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode()[:400]
        except Exception:
            detail = ""
        return {"ok": False, "payload_bytes": len(body), "http": exc.code,
                "latency_s": round(time.monotonic() - t0, 2), "detail": detail}
    except Exception as exc:
        return {"ok": False, "payload_bytes": len(body),
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "latency_s": round(time.monotonic() - t0, 2)}


def names(n: int, prefix: str = "opt") -> list[str]:
    return [f"{prefix}-{i:03d}" for i in range(n)]


def write_raw(battery: str, payload: dict) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RAW_DIR / f"{battery}-{ts}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return out


# ---------------------------------------------------------------- battery: limits
def battery_limits() -> int:
    res: dict = {"ts": datetime.now(timezone.utc).isoformat(), "cases": []}

    def case(label: str, model: str, questions: dict, state: str = "deploy the site now"):
        r = raw_call(model, state, questions)
        r.update({"label": label, "model": model})
        res["cases"].append(r)
        print(f"{label:52s} {model:24s} "
              f"{'OK' if r.get('ok') else 'FAIL'} "
              f"{r.get('http') or r.get('error') or ''} {r.get('latency_s')}s",
              flush=True)

    # choice counts, with and without none_of_these
    for n in (26, 27):
        case(f"choice {n}", SOLAR, {"q": {"type": "choice", "instructions": "pick",
              "criteria": {x: x for x in names(n)}}})
    for n in (26, 255, 256):
        case(f"choice {n}", JEV, {"q": {"type": "choice", "instructions": "pick",
              "criteria": {x: x for x in names(n)}}})
    case("choice 25 + none_of_these", SOLAR, {"q": {"type": "choice", "instructions": "pick",
          "criteria": {**{x: x for x in names(25)}, "none_of_these": "None fit."}}})
    case("choice 26 + none_of_these", SOLAR, {"q": {"type": "choice", "instructions": "pick",
          "criteria": {**{x: x for x in names(26)}, "none_of_these": "None fit."}}})
    case("choice 25 + none_of_these", JEV, {"q": {"type": "choice", "instructions": "pick",
          "criteria": {**{x: x for x in names(25)}, "none_of_these": "None fit."}}})
    # score counts — does the 26 label cap apply to score too?
    for n in (26, 50, 184, 300):
        case(f"score {n}", SOLAR, {"q": {"type": "score", "instructions": "rank",
              "criteria": [f"level {i}" for i in range(n)]}})
    for n in (50, 184):
        case(f"score {n}", JEV, {"q": {"type": "score", "instructions": "rank",
              "criteria": [f"level {i}" for i in range(n)]}})
    # state size ladder
    for size in (4_000, 16_000, 32_000, 64_000, 128_000):
        case(f"state {size//1000}K chars", SOLAR, {"q": {"type": "noul",
              "instructions": "Is this text long?",
              "criteria": {"true": "long", "false": "short"}}}, state="x " * (size // 2))
    for size in (4_000, 32_000, 64_000, 128_000):
        case(f"state {size//1000}K chars", JEV, {"q": {"type": "noul",
              "instructions": "Is this text long?",
              "criteria": {"true": "long", "false": "short"}}}, state="x " * (size // 2))

    p = write_raw("limits", res)
    print(f"\nraw -> {p}")
    return 0


# -------------------------------------------------------------- battery: accuracy
PLANTS = [
    ("Our OneDrive backup job has been failing every night since the SharePoint migration",
     "backup-restore-verification"),
    ("The wildcard certificate on the API gateway expires next week",
     "ssl-cert-inspection"),
    ("SonarQube flagged 12 new blocker issues and the release gate is tomorrow",
     "security-release-triage"),
    ("Reconcile this month's ledger against the bank statement export",
     "excel-ledger-reconciliation"),
    ("Summarize the Teams meeting recording from this morning into minutes",
     "teams-meeting-summarizer"),
    ("Write these meeting notes into the Obsidian vault",
     "obsidian"),
    ("Draw a sequence diagram for the SSO login flow",
     "mermaid-diagrams"),
    ("Create a Jira project for the new strategic initiative",
     "jira-program-setup"),
]


def real_roster() -> list[dict]:
    m._ROSTER_CACHE = None
    return m._load_roster(BASE_SETTINGS)


def synthetic_roster(n: int, planted_name: str, pool: list[dict], seed: int) -> list[dict]:
    rnd = random.Random(seed)
    planted = next((s for s in pool if s["name"] == planted_name), None)
    others = [s for s in pool if s["name"] != planted_name]
    rnd.shuffle(others)
    items = others[: max(0, n - 1)] + ([planted] if planted else [])
    rnd.shuffle(items)
    return items


def run_plugin(query: str, model: str, roster: list[dict], **over) -> dict:
    m._ROSTER_CACHE = roster
    st = settings(model, **over)
    t0 = time.monotonic()
    winner = m.suggest_skill(query, st)
    wall = round(time.monotonic() - t0, 2)
    info = dict(m._LAST_INFO)
    return {"winner": winner, "wall_s": wall, "info": info,
            "api_calls": m._RUN["calls"], "cost": round(m._RUN["cost"], 8)}


def battery_accuracy(models: list[tuple[str, list[int]]]) -> int:
    pool = real_roster()
    res: dict = {"ts": datetime.now(timezone.utc).isoformat(),
                 "roster_pool": len(pool), "rows": []}
    for model, sizes in models:
        for n in sizes:
            for i, (query, planted) in enumerate(PLANTS):
                if n == 1:
                    continue
                roster = synthetic_roster(n, planted, pool, seed=i)
                r = run_plugin(query, model, roster)
                hit = r["winner"] == planted
                in_top = planted in (r["info"].get("top") or [])
                res["rows"].append({"model": model, "n": n, "plant": planted, "hit": hit,
                                    "in_top": in_top, "winner": r["winner"], **r})
                print(f"{model:22s} N={n:3d} {planted[:28]:28s} "
                      f"{'HIT ' if hit else 'miss'} top={'y' if in_top else 'n'} "
                      f"gate={r['info'].get('gate')} fit={r['info'].get('fit')} "
                      f"calls={r['api_calls']} {r['wall_s']}s", flush=True)
    # summary
    print("\n-- accuracy summary (top-1 / shortlist recall) --")
    for model, sizes in models:
        for n in sizes:
            if n == 1:
                continue
            rows = [r for r in res["rows"] if r["model"] == model and r["n"] == n]
            if not rows:
                continue
            h = sum(r["hit"] for r in rows) / len(rows)
            t = sum(r["in_top"] for r in rows) / len(rows)
            lat = [r["wall_s"] for r in rows]
            res.setdefault("summary", []).append(
                {"model": model, "n": n, "top1": round(h, 3), "recall": round(t, 3),
                 "p50_wall_s": sorted(lat)[len(lat) // 2],
                 "mean_calls": round(sum(r["api_calls"] for r in rows) / len(rows), 1)})
            print(f"{model:22s} N={n:3d} top1={h:.0%} recall={t:.0%} "
                  f"p50={sorted(lat)[len(lat)//2]}s calls={res['summary'][-1]['mean_calls']}")
    p = write_raw("accuracy", res)
    print(f"\nraw -> {p}")
    return 0


# ------------------------------------------------------------ battery: thresholds
TURNS = [
    ("work", "Tolong cek uptime dan tanggal expired SSL certificate untuk api-gateway kita"),
    ("work", "Bikin sequence diagram alur login SSO untuk dokumen arsitektur"),
    ("work", "Reconcile the monthly ledger xlsx against the bank statement export"),
    ("work", "SonarQube found 12 new blocker issues; triage them for the release gate"),
    ("work", "Add this strategic initiative to Jira and set up the board"),
    ("work", "Summarize this morning's Teams meeting recording into minutes"),
    ("work", "Bersihin disk laptop ini, storage-nya hampir penuh"),
    ("chitchat", "Halo, apa kabar hari ini?"),
    ("chitchat", "Menurutmu kenapa langit berwarna biru?"),
    ("chitchat", "Ceritakan sedikit soal sejarah kopi di Indonesia"),
    ("chitchat", "Aku lagi bosan nih, kasih ide hiburan dong"),
    ("chitchat", "Menurutmu film terbaik dekade ini apa dan kenapa?"),
    ("ambiguous", "Kira-kira apa yang harus kita lakukan soal ini ya?"),
    ("ambiguous", "Boleh minta pendapatmu tentang rencana itu?"),
    ("ambiguous", "Gimana kalau kita ubah pendekatannya?"),
]


def battery_thresholds(models: list[str]) -> int:
    pool = real_roster()
    res: dict = {"ts": datetime.now(timezone.utc).isoformat(),
                 "roster_pool": len(pool), "rows": []}
    for model in models:
        for label, turn in TURNS:
            r = run_plugin(turn, model, pool)
            row = {"model": model, "label": label, "turn": turn,
                   "gate": r["info"].get("gate"), "fit": r["info"].get("fit"),
                   "winner": r["winner"], "reason": r["info"].get("reason"),
                   "chunks": r["info"].get("chunks"), "calls": r["api_calls"],
                   "wall_s": r["wall_s"], "cost": r["cost"],
                   "probability": r["info"].get("probability"),
                   "confidence": r["info"].get("confidence")}
            res["rows"].append(row)
            print(f"{model:22s} {label:10s} gate={row['gate']} fit={row['fit']} "
                  f"conf={row['confidence']} winner={row['winner'] or '-'} "
                  f"({row['reason']}) {row['wall_s']}s", flush=True)
    print("\n-- gate spread by label --")
    for model in models:
        for label in ("work", "chitchat", "ambiguous"):
            vals = [r["gate"] for r in res["rows"]
                    if r["model"] == model and r["label"] == label and r["gate"] is not None]
            if vals:
                print(f"{model:22s} {label:10s} n={len(vals)} min={min(vals):.3f} "
                      f"max={max(vals):.3f} mean={sum(vals)/len(vals):.3f}")
    p = write_raw("thresholds", res)
    print(f"\nraw -> {p}")
    return 0


# --------------------------------------------------------------------- battery: e2e
def hybrid_decide(switch_after: int, second_model: str):
    """First call(s) on the primary model, later calls on second_model."""
    real = m._decide

    def wrapper(state, questions, settings):
        st = dict(settings)
        if m._RUN["calls"] >= switch_after:
            st["openrouter_model"] = second_model
        return real(state, questions, st)

    return wrapper


def battery_e2e() -> int:
    pool = real_roster()
    res: dict = {"ts": datetime.now(timezone.utc).isoformat(),
                 "roster_pool": len(pool), "rows": []}
    query = "The wildcard certificate on the API gateway expires next week"
    modes = [
        ("jev-1.13 (production config)", JEV, None, {}),
        ("solar + jev config copied", SOLAR, None, {}),
        ("solar tuned (gate 0.10)", SOLAR, None, {"gate": 0.10}),
        ("hybrid jev-skim + solar-verify", JEV, hybrid_decide(1, SOLAR), {}),
    ]
    real_decide = m._decide
    for label, model, override, over in modes:
        m._decide = override or real_decide
        r = run_plugin(query, model, pool, **over)
        row = {"mode": label, "model": model, **r}
        res["rows"].append(row)
        print(f"{label:34s} winner={r['winner'] or '-':28s} calls={r['api_calls']} "
              f"{r['wall_s']}s ${r['cost']:.6f} chunks={r['info'].get('chunks')}",
              flush=True)
    m._decide = real_decide
    p = write_raw("e2e", res)
    print(f"\nraw -> {p}")
    return 0


# --------------------------------------------------------------- battery: calib
def battery_calib(models: list[str]) -> int:
    """Thresholds OFF (gate=fits=0) so nothing is blocked: measures raw Call-1
    accuracy AND the natural gate/fit scores that the thresholds must separate."""
    pool = real_roster()
    res: dict = {"ts": datetime.now(timezone.utc).isoformat(),
                 "roster_pool": len(pool), "rows": []}
    for model in models:
        n = 25 if model == SOLAR else 148
        for i, (query, planted) in enumerate(PLANTS):
            roster = synthetic_roster(n, planted, pool, seed=i)
            r = run_plugin(query, model, roster, gate=0.0, fits=0.0)
            row = {"model": model, "n": n, "kind": "work", "plant": planted,
                   "hit": r["winner"] == planted, "winner": r["winner"],
                   "gate": r["info"].get("gate"), "fit": r["info"].get("fit"),
                   "probability": r["info"].get("probability"),
                   "confidence": r["info"].get("confidence"),
                   "calls": r["api_calls"], "wall_s": r["wall_s"], "cost": r["cost"]}
            res["rows"].append(row)
            print(f"{model:22s} work   N={n:3d} {'HIT ' if row['hit'] else 'miss'} "
                  f"gate={row['gate']} fit={row['fit']} {row['wall_s']}s", flush=True)
        for label, turn in [t for t in TURNS if t[0] != "work"]:
            r = run_plugin(turn, model, pool, gate=0.0, fits=0.0)
            row = {"model": model, "n": len(pool), "kind": label, "turn": turn,
                   "winner": r["winner"], "gate": r["info"].get("gate"),
                   "fit": r["info"].get("fit"), "reason": r["info"].get("reason"),
                   "calls": r["api_calls"], "wall_s": r["wall_s"], "cost": r["cost"]}
            res["rows"].append(row)
            print(f"{model:22s} {label:8s} gate={row['gate']} fit={row['fit']} "
                  f"winner={row['winner'] or '-'} {row['wall_s']}s", flush=True)

    print("\n-- gate threshold sweep (keep work, drop chit-chat) --")
    res["sweep"] = []
    for model in models:
        work = [r["gate"] for r in res["rows"]
                if r["model"] == model and r["kind"] == "work" and r["gate"] is not None]
        chat = [r["gate"] for r in res["rows"]
                if r["model"] == model and r["kind"] == "chitchat" and r["gate"] is not None]
        amb = [r["gate"] for r in res["rows"]
               if r["model"] == model and r["kind"] == "ambiguous" and r["gate"] is not None]
        for t in [round(0.05 * k, 2) for k in range(1, 19)]:
            keep_w = sum(1 for v in work if v >= t)
            pass_c = sum(1 for v in chat if v >= t)
            pass_a = sum(1 for v in amb if v >= t)
            res["sweep"].append({"model": model, "t": t, "work_kept": keep_w,
                                 "work_total": len(work), "chat_passed": pass_c,
                                 "chat_total": len(chat), "amb_passed": pass_a,
                                 "amb_total": len(amb)})
            print(f"{model:22s} t={t:.2f} work={keep_w}/{len(work)} "
                  f"chitchat_passed={pass_c}/{len(chat)} ambiguous_passed={pass_a}/{len(amb)}")
    p = write_raw("calib", res)
    print(f"\nraw -> {p}")
    return 0


# -------------------------------------------------------------- battery: verify
def battery_verify(models: list[str]) -> int:
    """Isolate the Call-2 (verify) stage: does Solar over-reject?

    For each plant we build Call-1 exactly like the plugin (chunk 25, no
    none_of_these) on a 25-skill roster, take the plugin's shortlist, then run
    three Call-2 wordings: A = production (choice + none_of_these + fits),
    B = forced pick (no none_of_these), C = score rubric.
    """
    pool = real_roster()
    res: dict = {"ts": datetime.now(timezone.utc).isoformat(), "rows": []}
    for model in models:
        for i, (query, planted) in enumerate(PLANTS):
            roster = synthetic_roster(25, planted, pool, seed=i)
            criteria = {s["name"]: s["short"] or s["name"] for s in roster}
            index_lines = "\n".join(f"- {s['name']}: {s['short']}" for s in roster)
            q1 = {"which_skill": {"type": "choice",
                  "instructions": ("Which skill best fits the user request? "
                                   "Choose from the skill index below. "
                                   "If none fits, choose the closest anyway; the need-checks decide."
                                   f"\nSkill index:\n{index_lines}"),
                  "criteria": criteria}}
            r1 = raw_call(model, query, q1)
            if not r1.get("ok"):
                print(f"{model} {planted}: call1 FAIL {r1.get('http') or r1.get('error')}", flush=True)
                continue
            probs = (r1["answers"]["which_skill"].get("probabilities") or {})
            ranked = sorted(((n, float(p)) for n, p in probs.items()
                             if n in {s["name"] for s in roster}),
                            key=lambda kv: kv[1], reverse=True)[:3]
            shortlist = [n for n, _ in ranked]
            by_name = {s["name"]: s for s in roster}
            top = [by_name[n] for n in shortlist if n in by_name]
            detail = "\n\n".join(f"## {s['name']}\nFull: {s['full']}\nExcerpt: {s['excerpt']}"
                                 for s in top)

            variants = {
                "A_production": {"best_of_shortlist": {
                    "type": "choice",
                    "instructions": ("Given the full descriptions, which skill truly fits "
                                     f"the request? \nCandidates:\n{detail}"),
                    "criteria": {**{s["name"]: s["full"] or s["name"] for s in top},
                                 "none_of_these": "None of these skills fit the request."}}},
                "B_forced": {"best_of_shortlist": {
                    "type": "choice",
                    "instructions": ("Which of these candidates is the closest match to the "
                                     f"request? Pick one. \nCandidates:\n{detail}"),
                    "criteria": {s["name"]: s["full"] or s["name"] for s in top}}},
                "C_score": {"best_of_shortlist": {
                    "type": "score",
                    "instructions": ("How well does the best candidate match the request? "
                                     f"\nCandidates:\n{detail}"),
                    "criteria": ["Not relevant", "Partly relevant", "Directly relevant"]}},
            }
            row = {"model": model, "plant": planted, "shortlist": shortlist,
                   "plant_in_shortlist": planted in shortlist, "variants": {}}
            for vname, q2 in variants.items():
                r2 = raw_call(model, query, q2)
                got = None
                if r2.get("ok"):
                    ans = r2["answers"]["best_of_shortlist"]
                    got = ans.get("choice") or ans.get("selected") or ans.get("score")
                row["variants"][vname] = {
                    "ok": bool(r2.get("ok")),
                    "selected": got,
                    "correct": got == planted,
                    "rejected": got == "none_of_these",
                    "latency_s": r2.get("latency_s"),
                }
            # D: score each candidate separately, take the argmax (one call per
            # candidate; score cannot pick among candidates in a single call).
            scores: dict = {}
            for s in top:
                qd = {"match": {"type": "score",
                      "instructions": (f"How well does the skill '{s['name']}' match what the "
                                       f"request asks for? \nSkill full: {s['full']}"),
                      "criteria": ["Not relevant", "Partly relevant", "Directly relevant"]}}
                rd = raw_call(model, query, qd)
                val = None
                if rd.get("ok"):
                    ans = rd["answers"]["match"]
                    val = ans.get("score")
                scores[s["name"]] = val
            pick = max(scores, key=lambda k: (scores[k] if scores[k] is not None else -1))
            row["variants"]["D_scored_each"] = {
                "ok": all(v is not None for v in scores.values()),
                "selected": pick, "scores": scores,
                "correct": pick == planted, "rejected": False,
            }
            res["rows"].append(row)
            v = row["variants"]
            print(f"{model:22s} {planted[:26]:26s} short={row['plant_in_shortlist']} "
                  f"A={v['A_production']['selected']} B={v['B_forced']['selected']} "
                  f"D={v['D_scored_each']['selected']}", flush=True)

    print("\n-- verify-stage summary --")
    res["summary"] = []
    for model in models:
        rows = [r for r in res["rows"] if r["model"] == model and r["plant_in_shortlist"]]
        for vname in ("A_production", "B_forced", "C_score", "D_scored_each"):
            ok = sum(1 for r in rows if r["variants"][vname]["correct"])
            rej = sum(1 for r in rows if r["variants"][vname]["rejected"])
            res["summary"].append({"model": model, "variant": vname,
                                   "correct": ok, "rejected": rej, "n": len(rows)})
            print(f"{model:22s} {vname:14s} correct={ok}/{len(rows)} rejected_none={rej}")
    p = write_raw("verify", res)
    print(f"\nraw -> {p}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("battery", choices=["limits", "accuracy", "thresholds", "calib", "verify", "e2e", "all"])
    a = ap.parse_args()
    if not (m._openrouter_key() or ""):
        print("OPENROUTER_API_KEY missing — run under `bws run`", file=sys.stderr)
        raise SystemExit(2)
    rc = 0
    if a.battery in ("limits", "all"):
        rc |= battery_limits()
    if a.battery in ("accuracy", "all"):
        rc |= battery_accuracy([(SOLAR, [8, 16, 25, 26]), (JEV, [26, 64, 148])])
    if a.battery in ("thresholds", "all"):
        rc |= battery_thresholds([JEV, SOLAR])
    if a.battery in ("calib", "all"):
        rc |= battery_calib([JEV, SOLAR])
    if a.battery in ("verify", "all"):
        rc |= battery_verify([JEV, SOLAR])
    if a.battery in ("e2e", "all"):
        rc |= battery_e2e()
    raise SystemExit(rc)