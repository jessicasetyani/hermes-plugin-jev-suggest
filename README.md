# hermes-plugin-jev-suggest

Automatic skill suggestion for Hermes Agent via a decision model on OpenRouter
(backend locked to OpenRouter; the model slug is selectable).

**Version 0.4.0** — see [CHANGELOG.md](CHANGELOG.md). Rule for this repo:
behaviour change → minor bump + tag; docs/comments only → no bump.

## What it does

Runs via the `pre_llm_call` hook, before the LLM tool loop:

1. **Call 1 — skim**: one `choice` over the whole skill roster (60-char
   descriptions) + 3 `noul` need-checks. If the gate score is below threshold
   (chit-chat), stop — no second call.
2. **Call 2 — verify**: re-read the top-N with full descriptions (700 chars).
   May reject all of them.
3. **Inject**: at most one advisory line appended to the current user message:

```
<skill_relevance>
Relevant to the current request: <name>. Ignore this if it does not fit...
</skill_relevance>
```

The roster is never modified, the system prompt stays byte-stable (prefix
caching safe), and the agent keeps the final decision.

## Backend: OpenRouter-only (locked)

Unlike `jev-skill-router` (3 backends: TypeSafe-direct / OpenRouter / gateway),
this plugin is **locked to the OpenRouter Decisions API** — only
`openrouter_model` is selectable, from an allowlist:

| `openrouter_model` | Notes |
|---|---|
| `typesafe/jev-1.13` | **Recommended for production.** Pinned: reproducible, thresholds stay valid until you bump it. |
| `~typesafe/jev-latest` | Plugin default. Alias = always the newest Jev; calibration can drift on every vendor release. |
| `upstage/solar-decide` | Newcomer (Solar Mini 4). Full `noul`/`choice`/`score` like Jev, returns `confidence`. ~$0.05/M input. **Not interchangeable with Jev** — see [probe report](docs/calibration/SOLAR-PROBE-REPORT.md): 26-label cap incl. `none_of_these` (→ chunk 25), gate scores ~2.4x lower (use `gate: 0.10`, not 0.30), and ~9 calls / 7–10 s / $0.0047 per turn on a 184-skill roster. Sensible only for rosters ≤25 or as a hybrid verify stage. |
| `perplexity/pplx-decider-v1-27b` | **Calibrated 2026-10-04** (raw `accuracy-20261004T020417Z.json`, `thresholds-20261004T020549Z.json`). Same Decisions API + `confidence`. 262K ctx. **No 26-label cap** — choice 240 OK, `chunk: 240` stands. Accuracy N=148: **top-1 7/8 (88%) vs Jev 6/8 (75%)**, recall 7/8 both. Gate work 0.52–0.84 (mean 0.75) vs chit-chat ≤0.07: Jev `gate 0.30` separates perfectly with better margin than Jev. Latency p50 1.55s / max 2.39s (Jev 0.68/0.90) — inside 4s hook budget at 2x cost in time. **Probe spend $0.00 on all 23 calls** (list $0.04/M — treat as launch promo, not guaranteed). Viable opt-in alternative; production stays Jev until longer soak. |
| `cloudflare/clef-flash` | **Calibrated 2026-10-04 — not recommended.** No cap (240 OK) + `confidence`, but top-1 **4/8 (50%)** vs Jev 75%; 2 of 4 misses are gate artifacts (work scores 0.14/0.29 fall under Jev `gate 0.30` — needs `gate ~0.15`, Solar-style). Latency p50 1.72s, spend ~2x Jev ($0.0062/8 turns vs $0.0029). |
| `cloudflare/clef` | **Calibrated 2026-10-04 — not recommended.** No cap (240 OK) + `confidence`, top-1 6/8 = Jev, gate separates (work 0.69–0.95), but latency p50 2.35s / max 3.09s (nears 4s budget) and spend **~6x Jev** ($0.0172/8 turns). No accuracy win to justify it. |
| `liquid/d1` | **Added 2026-10-05, uncalibrated — opt-in only.** Same Decisions API; toy probe 200 on `noul`/`choice`/`score` (`choice`/`score` return `confidence`, `noul` does not — Span-01-style). $0.04/M in, $0 out, 66K ctx, single provider Liquid, ~0.44–0.57s on toy calls. **No chunk-cap probe yet** — runs at `chunk: 240` until the Solar-style 26/27/100/240 series passes; gate 0.30 is untuned, do not inherit blindly. Guardrail: Hemes Policy 39→40 (`liquid/d1-20260930`). |

Any other slug falls back to the default (logged, fail-open).

> Guardrail: a model not on the allowlist returns 404
> `model-ignored-by-guardrail` until its exact slug is added to the Hemes Model
> Policy. Verify with a re-`GET` of `/api/v1/guardrails` (the PATCH echo
> normalizes aliases to dated canonical slugs).

## Install (rollout to another Hermes agent)

```bash
hermes plugins install jessicasetyani/hermes-plugin-jev-suggest --enable
# takes effect on next session (/reset); restart gateway if running via gateway
```

Updates: `hermes plugins update jev-suggest` (or `check-updates` to poll).

## Configuration

`plugins.entries.jev-suggest.settings` in `config.yaml` — production values
in use on Ali's instance:

```yaml
plugins:
  entries:
    jev-suggest:
      settings:
        mode: auto            # off | auto (only when a key is present) | on
        gate: 0.30
        fits: 0.30
        shortlist: 3
        excerpt: 700
        timeout_s: 4.0
        suggest_chars: 4000
        max_skills: 300
        chunk: 240
        openrouter_model: "typesafe/jev-1.13"   # or ~typesafe/jev-latest / upstage/solar-decide / perplexity/pplx-decider-v1-27b / cloudflare/clef-flash / cloudflare/clef / liquid/d1
        openrouter_base_url: "https://openrouter.ai/api/alpha"
        retry_max_wait_s: 2.0
        breaker_threshold: 3
        breaker_cooldown_s: 120
        min_interval_s: 0.25
        cache_seconds: 300
```

Full key list with defaults (`plugin.yaml` `config_schema` is authoritative):

| Key | Default | Meaning |
|---|---|---|
| `mode` | `off` | `off` = never · `auto` = only with key · `on` = always |
| `gate` | `0.30` | Call-1 score; below it nothing is suggested |
| `fits` | `0.30` | Winner's own "does it fit" judgment; below it nothing is suggested |
| `shortlist` | `3` | Candidates carried from Call 1 to Call 2 |
| `excerpt` | `700` | SKILL.md characters each candidate brings |
| `timeout_s` | `4.0` | Seconds per Decisions API call (hook budget; router parity) |
| `suggest_chars` | `4000` | Longer user messages are left alone |
| `max_skills` | `300` | Max skills indexed |
| `chunk` | `240` | Skills per `choice` (API cap 255; larger rosters split into chunks + `none_of_these`, router-style) |
| `retry_max_wait_s` | `2.0` | On 429/529, retry once only if `Retry-After` waits at most this long |
| `breaker_threshold` | `3` | Consecutive 429/529s before going silent |
| `breaker_cooldown_s` | `120` | Silence window after the breaker opens |
| `min_interval_s` | `0.25` | Minimum gap between outgoing calls, per process |
| `cache_seconds` | `300` | Identical calls answered from cache per window |
| `openrouter_model` | `~typesafe/jev-latest` | Allowlist: `upstage/solar-decide` / `~typesafe/jev-latest` / `typesafe/jev-1.13` / `perplexity/pplx-decider-v1-27b` / `cloudflare/clef-flash` / `cloudflare/clef` / `liquid/d1` |
| `openrouter_base_url` | `https://openrouter.ai/api/alpha` | Base URL (endpoint = base + `/decisions`) |
| `log_enabled` | `true` | Write the event log at all (`false` = plugin runs, nothing recorded) |
| `log_max_bytes` | `5242880` | Rotate the event log at this size; `0` = never rotate |
| `log_keep_files` | `10` | Rotated logs kept, oldest deleted first; `0` = keep everything |
| `log_retention_days` | `30` | Delete rotations older than this; `0` = keep forever |

Env fallback (backward compat; config.yaml wins when a ctx is bound):
`JEV_SUGGEST_MODEL`, `JEV_SUGGEST_GATE`, `JEV_SUGGEST_FITS`,
`JEV_SUGGEST_TIMEOUT`, `JEV_SUGGEST_SHORTLIST`, `JEV_SUGGEST_EXCERPT`,
`JEV_SUGGEST_MAX_STATE`, `JEV_SUGGEST_MAX_SKILLS`, `JEV_SUGGEST_CHUNK`,
`JEV_SUGGEST_RETRY_MAX_WAIT`, `JEV_SUGGEST_BREAKER_THRESHOLD`,
`JEV_SUGGEST_BREAKER_COOLDOWN`, `JEV_SUGGEST_MIN_INTERVAL`,
`JEV_SUGGEST_CACHE_SECONDS`, `JEV_SUGGEST_MODE`, `JEV_SUGGEST_ENDPOINT`.

Requires `OPENROUTER_API_KEY` in env (injected by Bitwarden Secrets Manager at
Hermes runtime) or the Hermes credential pool. Missing key / timeout / error →
fail-open.

## Commands

```
hermes jev-suggest on|off|auto
hermes jev-suggest status
hermes jev-suggest suggest "deploy the site" [--json]
hermes jev-suggest check
```

## Log schema (unified with jev-skill-router)

Written to `~/.hermes/logs/jev-suggest-events.jsonl` — runtime logs belong in
the logs dir (same convention as the router's `jev-skill-router.log`); the
plugin directory holds code only.

**Lifecycle policy** (all settings, no env vars needed):

| Setting | Default | Behaviour |
|---|---|---|
| `log_enabled` | `true` | `false` → plugin keeps working, nothing is written |
| `log_max_bytes` | 5 MB | Active file is renamed to `jev-suggest-events-<stamp>.jsonl` when it reaches this size |
| `log_keep_files` | 10 | Oldest rotations are deleted beyond this count |
| `log_retention_days` | 30 | Rotations older than this are deleted (0 = keep forever) |

Rotation and pruning happen on the next append, so there is no background job
and no dependency on the agent loop being alive. `hermes jev-suggest status`
prints the active policy and the rotation count.

**Acceptance is exact, not inferred.** When a suggestion is injected, the hook
remembers it per session; every `skill_tool` event then carries the suggestion
it followed:

```
skill_tool: skill, tool, session_id, turn_id,
            suggested,            # winner of this session's last suggestion
            suggested_turn_id,    # the turn that produced it
            matched               # true/false: did the load match the suggestion
```

Acceptance rate = `matched=True` / (suggest events with a winner). No
time-ordered guessing, no session-level joins.

Every `suggest` event carries the union of both plugins' fields, so one day
the router log can be merged mechanically:

```
event, origin(hook|cli-suggest), mode, model,
session_id, turn_id,
outcome(suggested|silent|error), reason, winner,
gate, probability, confidence, fit, top,
calls, chunks, latency_s, cost, state_chars
```

`reason` is a superset of the router's: `slash|empty|too_long|already_routed|
trivial|disabled` (skips, now logged instead of silently dropped) plus
`gate|empty_shortlist|fits|none_selected|api_error` (the router collapses all of
these to `no_fit` — map back on merge). `skill_tool` events (every skill load,
for acceptance-rate analysis) remain suggest-only.

Old rows simply have `null` for fields added later — no migration needed.

Merge historic logs: `python3 scripts/merge_router_log.py` → writes
`docs/history/merged-decisions.jsonl` (read-only inputs).

## Verify

```
python3 tests/test_offline.py          # offline logic + fail-open, no network
hermes plugins validate .              # catalog admission gate
python3 scripts/log_stats.py           # funnel/gate/latency/cost/acceptance from the live log
python3 scripts/log_stats.py --json    # machine-readable, for dashboards
python3 scripts/merge_router_log.py    # merge historic router + suggest rows
```

## Calibration

Thresholds (`gate 0.30`, `fits 0.30`) are inherited from the router, not
measured — see [`docs/calibration/NOTES.md`](docs/calibration/NOTES.md) for
provenance, the first live datapoint, and the probe procedure required before
trusting `upstage/solar-decide` or a new `~typesafe/jev-latest` release.

## Exit plan

1. `hermes jev-suggest off` or `hermes plugins disable jev-suggest` — stop calls / disable permanently
2. Delete `~/.hermes/plugins/jev-suggest/` + `/reset` — zero residue
3. Fail criteria: added p50 latency > 0.5s, bad suggestions 2 days straight, or error rate > 5%

## Source of truth

This repo. The deployed copy at `~/.hermes/plugins/jev-suggest/` is a plain
copy — after editing here, copy over and re-validate with
`hermes plugins validate ~/.hermes/plugins/jev-suggest`.