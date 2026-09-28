# Calibration notes — jev-suggest

Thresholds shipped (`gate 0.30`, `fits 0.30`) are inherited, not measured:

- `gate 0.30` comes from the jev-skill-router cookbook calibration
  (76 labelled requests, top-1 95%, sweep gate 0.20–0.40 × fits 0.30–0.50).
- `fits 0.30` is Ali's decision (router ships 0.40). Rationale: suggest is
  advisory — the model keeps the final call — so a slightly looser second
  door is acceptable. Revisit if bad suggestions persist 2 days straight.

## Pending: live probe (deferred by Ali, 28 Sep 2026)

Before switching `openrouter_model` to `upstage/solar-decide` (or trusting
`~typesafe/jev-latest` after a vendor release), run a head-to-head probe:

```
hermes jev-suggest suggest "<same request>" --json        # per model
```

with `openrouter_model` set to each of
`~typesafe/jev-latest` / `typesafe/jev-1.13` / `upstage/solar-decide`
in `plugins.entries.jev-suggest.settings` (+ `/reset` between switches,
or use the env fallback `JEV_SUGGEST_MODEL` for one-shot runs).

Record per model: winner, gate, fit, latency_s, cost (all in
`~/.hermes/logs/jev-suggest-events.jsonl` — runtime logs live in the logs
dir, plugin dir is code only). Thresholds are NOT portable between vendors
(observed: Jev 0.95 vs Span-01 0.45 on identical input) — re-tune
gate/fits per model against labelled requests from our own domain.

Prerequisite: the exact slug must be on the Hemes Model Policy guardrail
allowlist, else the Decisions API returns 404 `model-ignored-by-guardrail`.

## First datapoint (28 Sep 2026, live probe, ~$0.000055)

Identical state ("Please deploy the production site now and update the DNS
records.") + 1 choice + 1 noul, via `bws run` inference key:

| Model | choice | noul | Latency | Cost |
|---|---|---|---|---|
| `typesafe/jev-1.13` | technical p=1.0, conf 1.0 | 0.92 | 3.85s | $0.0000163 |
| `upstage/solar-decide` | technical p=0.9968, conf 0.978 | 0.9705 | 1.02s | $0.0000387 |

Findings: Solar agrees with Jev on this sample (both strongly technical +
urgent), returns `confidence` + full `probabilities` like Jev (not like
Span-01), accepts `choice` without 400, and the `upstage/solar-decide` alias
routes fine even though the allowlist holds the canonical
`upstage/solar-decide-20260928`. Guardrail independently verified via
Management API: Hemes Model Policy contains the Solar canonical slug.
Latency single-sample only — do not generalize (Jev 3.85s here vs its usual
~0.2–0.4s suggests provider routing variance).

## Blocker for Solar as router model (28 Sep 2026, verified live)

Solar Decide route caps **one Choice at 26 options**: 148 candidates →
HTTP 422 `validation_failed: 148 candidates exceed the 26 single-token
labels this route supports`. Boundary verified: 26 hyphenated names OK,
27th fails. Consequence: Solar as the skill-router model needs chunk ≤26
(148 skills = 6 chunk calls + rerank ≈ 7 round trips/turn — too slow for a
pre_llm_call hook). Mitigation shipped: auto-clamp chunk to 26 on Solar
models (logged warning) + `SOLAR_MAX_CHOICES` constant + offline test.
Decision: production model = `typesafe/jev-1.13` (pinned); Solar stays in
the allowlist for rerank-only or post-limit-lift use.

## Upstream latency variance (28 Sep 2026, observed live)

Identical 29KB Call-1 payloads on `typesafe/jev-1.13` returned in
0.4–0.7s most runs but hit 8–10s+ twice within minutes (provider routing
variance, not payload-dependent — direct replay of the same bytes was
fast). Lesson: the earlier 2-call probe (3 options) was necessary but
insufficient; production-shape (148-choice) E2E caught both the Solar 422
and this variance. Default `timeout_s` lowered 10.0 → 4.0 (router parity)
so slow turns fail open within hook budget instead of blocking 10s+.
