# Changelog

All notable changes to this plugin. Version bumps follow: **behaviour change → minor**,
**doc/comment only → no bump**, **breaking config/schema change → major**.

## 0.4.0 — 2026-10-04

Allowlist support for three new Decisions-API models (same endpoint, all three
primitives per OpenRouter docs — not yet calibrated):

- **Feat:** `perplexity/pplx-decider-v1-27b` ($0.04/M, 262K, ~0.33s),
  `cloudflare/clef-flash` ($0.09/M, ~0.28s), `cloudflare/clef` ($0.24/M, ~0.47s)
  added to `ALLOWED_MODELS` + `plugin.yaml` + README. No chunk clamp yet.
- **Evidence:** live probe 2026-10-04 via `bws run` (`scripts/new_decoders_probe.py`,
  raw `docs/calibration/raw/new-decoders-20261004.txt`). Pre-guardrail: all three
  returned `404 model-ignored-by-guardrail` on every call — Hemes Model Policy
  `22d5f807-…` carried Jev/Span/Solar but none of the three. Post-PATCH (re-GET
  verified, 36→39 entries; OpenRouter normalized
  `perplexity/pplx-decider-v1-27b` → `…-20261001`): all three accept
  noul/choice/score, return `confidence`, and pass choice 26/27/25+none/26+none/
  100/240 — no Solar-style 26-label cap, so `chunk: 240` stands. Gate separation
  is Jev-like (work 0.64–0.83 vs chit-chat ~0.00), so Jev `gate 0.30` is a sane
  starting point, not a blind inherit. Latency 0.4–1.7s (clef 240-choice slowest).
  pplx usage reported `cost: 0` on probe calls vs clef-flash ~$2.9e-05 and clef
  ~$7.7e-05 per gate call.
- **Guardrail:** `_solar_config_warning` now warns for `NEW_DECISION_MODELS`
  (early-calibration — see accuracy findings below).
- Production stays `typesafe/jev-1.13` until post-allowlist probe sets per-model
  chunk clamp + gate/fits.
- **Accuracy battery 2026-10-04** (`scripts/new_decoders_accuracy.py`: 8 planted
  queries × N=148 × 4 models + 15-turn thresholds; raw `accuracy-20261004T020417Z.json`,
  `thresholds-20261004T020549Z.json`): pplx top-1 7/8 (88%) vs Jev 6/8 (75%),
  clef 6/8, clef-flash 4/8; recall 7/8 all four. Gate means (work/chitchat):
  Jev 0.71/0.03, pplx 0.75/0.03 (gate 0.30 separates, better margin than Jev),
  clef-flash 0.42/0.01 (needs gate ~0.15), clef 0.86/0.02 (separates, runs hot).
  Latency p50: Jev 0.68s, pplx 1.55s, clef-flash 1.72s, clef 2.35s (max 3.09s —
  nears 4s hook budget). Spend per 8 turns: Jev $0.0029, pplx $0.0000 (all probe
  calls cost 0 — launch promo, not guaranteed), clef-flash $0.0062, clef $0.0172.
  Verdict: pplx viable opt-in alternative, both Clef variants not recommended.

## 0.3.0 — 2026-09-29

Behaviour changes after the router-parity port, none of which changed the config
surface except the added log settings.

- **Fix:** Solar `choice` clamp is 25, not 26 — the route caps a Choice at 26 labels
  *in total* and `none_of_these` consumes one, so the previous clamp would 422 every
  chunked Call-1 once a roster exceeds one chunk (found by the Solar probe).
- **Feat:** exact acceptance linkage — `skill_tool` events carry `suggested`,
  `suggested_turn_id`, `matched` instead of relying on time-ordered joins.
- **Feat:** log lifecycle as settings — `log_enabled`, `log_max_bytes` (5 MB),
  `log_keep_files` (10), `log_retention_days` (30); rotation and pruning run on the
  next append, replaced the silent 5 MB stop.
- **Change:** event log moved to `~/.hermes/logs/jev-suggest-events.jsonl` (logs dir
  is for runtime, the plugin dir is code only).
- **Change:** `timeout_s` default 10.0 → 4.0 (hook budget; upstream latency variance).
- **Feat:** Solar calibration guardrail — warns when Solar runs with a Jev-tuned gate;
  `SOLAR_MAX_CHOICES` documented with the probe evidence.
- **Removed:** `JEV_SUGGEST_DISABLE` env kill switch — `mode: off` (per turn, no
  `/reset`) and `hermes plugins disable` are the two kill layers.
- **Tooling:** `scripts/solar_probe.py` (5 batteries), `scripts/log_stats.py`,
  `scripts/merge_router_log.py`; 50 offline checks; Solar probe report + raw rows.

## 0.2.0 — 2026-09-28

Port of `jev-skill-router` behaviour onto the OpenRouter-locked plugin.

- Chunked Call-1 (240/chunk + `none_of_these`, best-chunk-first, gate questions on the
  first chunk only) and a 2-call skim → verify flow.
- Unified event schema: `origin`, `mode`, `outcome`, `reason` on every path including
  skips, plus `probability`, `confidence`, `calls`, `gate`, `top`, `state_chars`.
- Transport hardening: rate-limit retry with `Retry-After`, breaker, pacing, response
  cache, 64 KB payload cap, no-redirect/no-proxy opener.
- `config.yaml` settings (env fallback), model allowlist, `on|off|auto` modes, CLI
  (`on|off|auto|status|suggest|check`).

## 0.1.0 — 2026-09-27

Initial: `pre_llm_call` skill suggestion via `~typesafe/jev-latest`, fail-open,
roster untouched, one advisory `<skill_relevance>` line.