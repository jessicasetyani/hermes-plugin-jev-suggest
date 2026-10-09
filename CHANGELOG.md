# Changelog

All notable changes to this plugin. Version bumps follow: **behaviour change → minor**,
**doc/comment only → no bump**, **breaking config/schema change → major**.

## 0.7.0 — 2026-10-09

Swap the Perplexity decider to the new checkpoint: **v1 retired, v1.1 replaces it.**

- **Evidence — v1 is gone (live probe 2026-10-09, Decisions API via `bws run`):**
  `perplexity/pplx-decider-v1-27b` and its dated canonical `perplexity/pplx-decider-v1-27b-20261001` both return
  `404 No endpoints found` — not a guardrail message, so no provider endpoint
  exists at all. No deprecation window was published.
- **Evidence — v1.1 is the successor:** OpenRouter model page `Decider V1.1 27B`
  (released 2026-10-07, canonical `perplexity/pplx-decider-v1.1-27b-20261006`, 262K ctx, $0.02/M input · $0 output,
  `deprecationDate: null`). The Decisions API resolves 1 endpoint for both the
  alias and the dated slug.
- **Change:** `ALLOWED_MODELS` / `NEW_DECISION_MODELS` carry `perplexity/pplx-decider-v1.1-27b` +
  `perplexity/pplx-decider-v1.1-27b-20261006` instead of the retired v1 pair; `plugin.yaml` description, README
  allowlist table and version rows updated; version → 0.7.0.
- **⚠️ Calibration debt:** every pplx number in this repo (accuracy N=148 top-1
  7/8, threshold sweep, gate 0.30) was measured on **v1**. Calibration does not
  transfer across checkpoints, so v1.1 shipped as an **uncalibrated opt-in** until the
  2026-10-09 run recorded below.
- **Guardrail (resolved 2026-10-09):** the slug was added to Hemes Model Policy and
  verified — the policy stores the canonical `perplexity/pplx-decider-v1.1-27b-20261006`
  and live calls return 200.
- **Calibration 2026-10-09** (raw `accuracy-20261009T154748Z.json`,
  `thresholds-20261009T154831Z.json`, harness `scripts/acc-v11-20261009.py`):
  N=148 same-session vs Jev — **top-1 6/8 = Jev 6/8, recall 7/8 = Jev 7/8**. v1 had
  scored 7/8; the lost pick is `teams-meeting-summarizer` → `meeting-notes-authoring`
  (shortlist recall intact, so a verify-stage confusion, not a routing miss). Gate
  separation is **wider than Jev's**: work min 0.553 / mean 0.832 vs ambiguous
  max 0.044 and chit-chat max 0.015 — `gate 0.35` validated with ~0.2 margin.
  Work-turn `fit` 0.92–1.00, so `fits 0.5` silences nothing. p50 wall 2.14s vs
  Jev 1.23s. Cost **$0.00018/turn at list** (8.9K in / 8 out, billed $0 on promo)
  vs Jev's measured $0.00044/turn. Caveat: 1 of 7 work turns returned
  `reason=none_selected` — `none_of_these` ≥0.50 vetoed the single 240-skill chunk.
- **Verdict:** v1.1 is viable and ~2.5x cheaper per turn at list, but it does **not**
  beat Jev on accuracy — the v1-era "pplx is more accurate than Jev" case does not
  survive the checkpoint. The trade is now cost against ~0.9s of extra latency.
  `gate 0.35` / `fits 0.5` remain correct as configured; no config change needed.
- **Change (no bump, doc/comment only):** `_solar_config_warning` no longer prints
  the pre-v1.1 recommendation text (`pplx top-1 7/8`, `$0.04/M`).

## 0.6.0 — 2026-10-05

Four-model head-to-head (Jev vs Solar vs pplx vs liquid/d1) + liquid/d1 full
calibration. Production stays `typesafe/jev-1.13` (pinned); pplx/liquid are
viable opt-ins, Solar is router-unusable.

- **Evidence — primitives + caps 2026-10-05** (`scripts/probe4-20261005.py` via `bws run`):
  all four accept noul/choice/score; choice/score return `confidence`, noul
  does not (all vendors). Choice caps: Jev 240 OK, pplx 240 OK, liquid/d1
  240 OK (26/27/25+none/26+none/100/240 all OK — no Solar cap, chunk 240
  stands); Solar 26 OK (13.9s) but 27/26+none/100/240 all 422
  `26 single-token labels`. Gate work vs chitchat: Jev 0.818/0.003, pplx
  0.830/0.000, liquid 0.902/0.000 (gate 0.30 separates); Solar 0.091/0.005
  on long instructions (just_talk 0.906 misfires) but 0.95/0.00 on short —
  instruction-sensitive, do not trust.
- **Evidence — accuracy N=148 2026-10-05** (raw
  `accuracy-20261005T143947Z.json`, 8 planted queries, production shape):
  pplx top-1 7/8 (88%), Jev 6/8 (75%), liquid 5/8 (62%), Solar 1/8 (12%);
  recall 7/8 Jev/pplx/liquid, 5/8 Solar. Liquid's 3 misses are verify-stage
  fits=None rejections (skim found them — top=y), not routing misses.
  p50 wall: Jev 0.88s, pplx 1.21s, liquid 1.32s, Solar 39.52s (6.6 calls —
  exceeds 4s hook budget every turn). Cost/8 turns: Jev $0.00294, liquid
  $0.00201, pplx $0.00000 (promo), Solar $0.00418.
- **Evidence — thresholds 2026-10-05** (`scripts/thr4-20261005.py`, 3 work + 2 chitchat +
  1 ambiguous): Jev work 0.73-0.89 vs chit 0.00-0.05, pplx 0.77-0.87 vs
  0.00-0.01, liquid 0.76-0.96 vs 0.00-0.05, ambiguous 0.29-0.34/0.11/0.29 —
  gate 0.30 separates work from chitchat on Jev/pplx/liquid.
- **Change:** liquid/d1 warning upgraded from toy-probe untuned to calibrated
  viable-opt-in (comment block + `_solar_config_warning`).
- **Verdict:** pplx best accuracy (7/8) at $0 cost; liquid cheapest billed
  ($0.0020 vs Jev $0.0029) at 5/8 top-1 / 7/8 recall; Solar router-unusable
  (12% top-1, 39s p50, 422 over 26 labels).

## 0.5.0 — 2026-10-05

Allowlist support for `liquid/d1` (Liquid AI System One decision model) as
opt-in only — the configured model (`perplexity/pplx-decider-v1-27b`) is untouched:

- **Feat:** `liquid/d1` + canonical `liquid/d1-20260930` added to
  `ALLOWED_MODELS` + `NEW_DECISION_MODELS` + `plugin.yaml` + README, with a
  dedicated early-calibration `status` warning. Unknown slugs still fall back
  to the default (fail-open).
- **Evidence:** Hemes Model Policy re-GET verified 39→40 with
  `liquid/d1-20260930`; toy probe 2026-10-05 via `bws run` 200 on
  noul/choice/score (choice billing 0.87/conf 0.81, score 1.12/conf 0.67,
  noul urgency 0.94 with NO confidence — Span-01-style; $0.000002–0.000012/call,
  0.44–0.57s). Chunk cap UNPROBED — runs at `chunk: 240` until the
  26/27/100/240 series passes; gate 0.30 untuned.
- Production stays on the current Perplexity setting until a full
  chunk-cap + accuracy/threshold battery passes.

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