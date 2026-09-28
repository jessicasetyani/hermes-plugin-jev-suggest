# hermes-plugin-jev-suggest

Automatic skill suggestion for Hermes Agent via a decision model on OpenRouter (locked to OpenRouter; model selectable).

## What it does

Runs via the `pre_llm_call` hook, before the LLM tool loop:

1. **Call 1 — skim**: one `choice` over the whole skill roster (60-char descriptions) + 3 `noul` need-checks. If the gate score is below threshold (chit-chat), stop — no second call.
2. **Call 2 — verify**: re-read the top-N with full descriptions (700 chars). May reject all of them.
3. **Inject**: at most one advisory line into the current user message:

```
<skill_relevance>
Relevant to the current request: <name>. Ignore this if it does not fit...
</skill_relevance>
```

The roster is never modified, the system prompt stays byte-stable (prefix caching safe), and the agent keeps the final decision.

## Backend: OpenRouter-only (locked)

Unlike `jev-skill-router` (3 backends: TypeSafe-direct / OpenRouter / gateway), plugin ini **dikunci ke OpenRouter Decisions API** — hanya `openrouter_model` yang bisa diganti, dari allowlist:

| `openrouter_model` | Keterangan |
|---|---|
| `~typesafe/jev-latest` | Default. Alias = selalu Jev terbaru (kalibrasi bisa geser tiap release). |
| `typesafe/jev-1.13` | Pinned. Reproduksibel, disarankan untuk production. |
| `upstage/solar-decide` | Pendatang baru (Solar Mini 4). Full `noul`/`choice`/`score` seperti Jev. ~$0.05/M input, P50 ~0.57s. Threshold wajib re-tune. **Batasan: max 26 options per `choice`** (422 di atas itu, verified 28 Sep 2026) — untuk roster 148 perlu clamp chunk 26 = 6 calls/turn, jadi TIDAK disarankan sebagai model utama skill-router; pakai pinned Jev. |

Slug lain → fallback ke default (logged, fail-open).

> Guardrail: model baru default 404 `model-ignored-by-guardrail` sampai masuk allowlist Hemes Model Policy. Tambahkan slug persisnya, re-GET untuk konfirmasi (PATCH echo menormalisasi alias ke dated slug).

## Install (rollout to another Hermes agent)

```bash
hermes plugins install jessicasetyani/hermes-plugin-jev-suggest --enable
# takes effect on next session (/reset); restart gateway if running via gateway
```

Updates: `hermes plugins update jev-suggest` (or `check-updates` to poll).

## Configuration (config.yaml — contek jev-skill-router)

`plugins.entries.jev-suggest.settings` in `config.yaml`:

```yaml
plugins:
  entries:
    jev-suggest:
      settings:
        mode: auto            # off | auto (hanya bila key ada) | on
        gate: 0.30
        fits: 0.30
        shortlist: 3
        excerpt: 700
        timeout_s: 10.0
        suggest_chars: 4000
        max_skills: 300
        openrouter_model: "~typesafe/jev-latest"  # atau typesafe/jev-1.13 / upstage/solar-decide
        openrouter_base_url: "https://openrouter.ai/api/alpha"
```

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
| `chunk` | `240` | Skills per Choice (API cap 255; lebih dari itu dipecah + `none_of_these`, cara router) |
| `retry_max_wait_s` | `2.0` | 429/529 → retry 1x hanya bila Retry-After ≤ ini |
| `breaker_threshold` | `3` | 429/529 beruntun sebelum diam |
| `breaker_cooldown_s` | `120` | Jeda diam setelah breaker terbuka |
| `min_interval_s` | `0.25` | Jarak minimum antar calls |
| `cache_seconds` | `300` | Calls identik dijawab dari cache |
| `openrouter_model` | `~typesafe/jev-latest` | Allowlist: `upstage/solar-decide` / `~typesafe/jev-latest` / `typesafe/jev-1.13` |
| `openrouter_base_url` | `https://openrouter.ai/api/alpha` | Base URL (endpoint = base + `/decisions`) |

Env fallback (backward compat, config.yaml wins bila ctx terikat): `JEV_SUGGEST_MODEL`, `JEV_SUGGEST_GATE`, `JEV_SUGGEST_FITS`, `JEV_SUGGEST_TIMEOUT`, `JEV_SUGGEST_SHORTLIST`, `JEV_SUGGEST_EXCERPT`, `JEV_SUGGEST_MAX_STATE`, `JEV_SUGGEST_MAX_SKILLS`, `JEV_SUGGEST_CHUNK`, `JEV_SUGGEST_RETRY_MAX_WAIT`, `JEV_SUGGEST_BREAKER_THRESHOLD`, `JEV_SUGGEST_BREAKER_COOLDOWN`, `JEV_SUGGEST_MIN_INTERVAL`, `JEV_SUGGEST_CACHE_SECONDS`, `JEV_SUGGEST_MODE`, `JEV_SUGGEST_ENDPOINT`.

Requires `OPENROUTER_API_KEY` in env (injected by Bitwarden Secrets Manager at Hermes runtime) or Hermes credential pool. Missing key / timeout / error → fail-open.

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
trivial|disabled` (skips, now logged instead of silent) plus
`gate|empty_shortlist|fits|none_selected|api_error` (router collapses all of
these to `no_fit` — map back on merge). `skill_tool` events (every skill load,
for acceptance-rate analysis) remain suggest-only.

Old rows simply have `null` for fields added later — no migration needed.

## Verify

```
python3 tests/test_offline.py   # offline logic + fail-open, no network
hermes plugins validate .       # catalog admission gate
```

## Calibration

Thresholds (`gate 0.30`, `fits 0.30`) are inherited, not measured — see
[`docs/calibration/NOTES.md`](docs/calibration/NOTES.md) for provenance
and the deferred live-probe procedure (required before trusting
`upstage/solar-decide` or a new `~typesafe/jev-latest` release).

## Exit plan

1. `hermes jev-suggest off` atau `hermes plugins disable jev-suggest` — stop calls / disable permanently
2. Delete `~/.hermes/plugins/jev-suggest/` + `/reset` — zero residue
3. Fail criteria: added p50 latency > 0.5s, bad suggestions 2 days straight, or error rate > 5%

## Source of truth

This repo. Deployed copy at `~/.hermes/plugins/jev-suggest/` is a plain copy — after editing here, copy over and re-validate with `hermes plugins validate ~/.hermes/plugins/jev-suggest`.
