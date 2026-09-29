# Changelog

All notable changes to this plugin. Version bumps follow: **behaviour change → minor**,
**doc/comment only → no bump**, **breaking config/schema change → major**.

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