# Solar Decide probe report — can it inherit Jev's config?

**Date:** 2026-09-29 · **Verdict:** no. Solar is a competent decision model whose
**calibration and route limits are incompatible with Jev's settings**, and on a
180+ skill roster it is **~10x slower and ~11x costlier per turn** than Jev.

Everything below was measured through the real plugin path (Call-1 chunking,
gate, shortlist, Call-2 verify) against the live OpenRouter Decisions API, using
the plugin's actual 184-skill roster plus synthetic rosters with planted
ground truth. Raw rows: [`raw/`](raw/).

Reproduce:

```bash
P=29cb6471-a904-45f2-aebf-b46d00742866   # BASE Unit BWS project
bws run --project-id $P --no-inherit-env -- python3 scripts/solar_probe.py all
```

## 1. Route limits (hard facts)

| Probe | `upstage/solar-decide` | `typesafe/jev-1.13` |
|---|---|---|
| `choice` max labels | **26 total** (27 → 422 "26 single-token labels") | 255 (256 → 400 "at most 255 choices") |
| `choice` + `none_of_these` | 25 options + none = 26 ✅ / 26 + none = **422** | 25 + none ✅ |
| `score` levels | 2–10 (26 → 422 "must contain 2 to 10 levels") | 2–10 (50 → 400 "at most 10 levels") |
| `state` size | OK at 128K chars | OK at 64K, **fails 128K** (`max_tokens_exceeded`) |
| Latency, tiny payload | 0.25–2.3 s | 0.28–0.6 s |

Consequences: `none_of_these` **consumes one of Solar's 26 labels**, so the usable
chunk is **25** — the plugin now clamps Solar to 25 (shipped with this report). And
because `score` caps at 10 levels on *both* vendors, score cannot be used to skim a
roster in one call; there is no single-call route for Solar over a wide roster.

## 2. Selection quality (planted ground truth, 8 queries)

Roster = the real 184 skills, sampled down to N with exactly one correct answer.
Default Jev thresholds (gate 0.30 / fits 0.30):

| Model | N | top-1 | shortlist recall | p50 wall | mean calls |
|---|---|---|---|---|---|
| Solar | 8 | 38% | 88% | 1.34 s | 1.6 |
| Solar | 16 | 25% | 88% | 1.38 s | 1.5 |
| Solar | 25 | 38% | 88% | 1.55 s | 1.6 |
| Solar | 26 | 38% | 88% | 1.77 s | 1.6 |
| Jev | 26 | 88% | 88% | 0.71 s | 2.0 |
| Jev | 64 | 88% | 88% | 0.72 s | 2.0 |
| Jev | 148 | 88% | 88% | 0.82 s | 2.0 |

**Shortlist recall is identical (88%).** The top-1 gap is not a selection
weakness — it is the gate silencing turns *before* Call-2 ever runs (Solar's gate
came in at 0.082 and 0.133 on two work turns that Jev scores 0.79 and 0.83).

## 3. Calibration — the part that cannot be copied

Thresholds disabled (gate = fits = 0) to read the models' natural scales:

| | Jev | Solar |
|---|---|---|
| work gate values | 0.33, 0.66, 0.69, 0.70, 0.70, 0.71, 0.79, 0.83 | 0.08, 0.09, 0.11, 0.43, 0.46, 0.48, 0.48, 0.66 |
| work median | **0.70** | **0.44** |
| chit-chat gate values | 0.001, 0.027, 0.033, 0.035, 0.051 | 0.007, 0.008, 0.012, 0.024, 0.029 |
| chit-chat median | 0.033 | 0.012 |
| ambiguous | 0.076, 0.104, 0.144 | 0.014, 0.033, 0.049 |
| top-1 (thresholds off) | 7/8 | 6/8 |

Gate sweep (keep work turns, silence chit-chat):

| threshold | Jev work kept | Jev chit-chat passed | Solar work kept | Solar chit-chat passed |
|---|---|---|---|---|
| 0.05 | 8/8 | 1/5 | **8/8** | 0/5 |
| 0.10 | 8/8 | 0/5 | 6/8 | 0/5 |
| 0.15 | 8/8 | 0/5 | 5/8 | 0/5 |
| 0.30 (Jev's setting) | **8/8** | 0/5 | **5/8** | 0/5 |
| 0.35–0.65 | 7/8 | 0/5 | 4–5/8 | 0/5 |
| 0.70 | 4/8 | 0/5 | 1/8 | 0/5 |

* Jev's 0.30 is well placed (separates 0.33–0.83 from ≤0.14).
* Solar's useful band is **0.05–0.10**: 0.05 keeps every work turn, but the margin
  above Solar's loudest chit-chat (0.029) is thin — **0.10 is the safer pick**
  (2 work turns lost, still 0/5 chit-chat).
* `fits` does **not** separate successes from failures on either model
  (Jev hits 0.39–0.84 vs miss 0.57; Solar hits 0.32–0.79 vs misses 0.23, 0.42) —
  keep it as a floor, not a filter.

## 4. Verify stage (Call-2) — Solar is fine here

Shortlists built the plugin's way on a 25-skill roster (plant present in 7/8):

| Call-2 shape | Jev correct | Solar correct | rejected `none_of_these` |
|---|---|---|---|
| A — production wording (choice + none) | **7/7** | **6/7** | 0 (both) |
| B — forced pick (no `none_of_these`) | 7/7 | 6/7 | 0 |
| C — single `score` call | n/a (invalid design) | n/a | — |
| D — `score` each candidate, argmax | 6/7 | 6/7 | — |

Solar does **not** over-reject; the "silent" outcomes in §5 come from its gate, not
from a trigger-happy verifier. Variant C was mis-specified (a `score` question
returns a position, it cannot name a candidate) — reported for completeness, not
as evidence. Splitting verify into per-candidate `score` calls (D) buys nothing
for 3x the calls.

## 5. End-to-end on the real 184-skill roster

One turn: “The wildcard certificate on the API gateway expires next week”.

| Configuration | Winner | Calls | Wall | Cost |
|---|---|---|---|---|
| Jev-1.13, production config | ✅ ssl-cert-inspection | 2 | **0.74 s** | $0.000440 |
| Solar + Jev's config copied | ❌ silent (`none_selected`) | 9 | 7.76 s | $0.004775 |
| Solar tuned (gate 0.10) | ❌ silent (`none_selected`) | 9 | 8.20 s | $0.004707 |
| Hybrid: Jev skim + Solar verify | ✅ ssl-cert-inspection | 2 | 1.28 s | $0.000499 |

The correct skill was in the shortlist in every Solar run — the 8-chunk skim
produced a 3-item shortlist that Call-2 then rejected, and the Call-2 rejection
did not reproduce in §4, so treat Solar's wide-roster behaviour as **noisy** as well
as slow. Cost and latency are structural: 184 skills ÷ 25 labels = 8 chunk calls,
each ~1 s.

## 6. Recommended configurations

**Use Solar only where the roster is ≤25 (single call), or as the verify stage.**
This is what "best Solar setup" means concretely:

```yaml
plugins:
  entries:
    jev-suggest:
      settings:
        openrouter_model: "upstage/solar-decide"
        gate: 0.10          # NOT 0.30 — Solar's scale is ~2.4x lower
        fits: 0.30          # weak separator on both models; a floor only
        shortlist: 3
        chunk: 25           # auto-clamped; 26 labels incl. none_of_these
        timeout_s: 4.0      # 8 sequential calls must still fit the hook budget
```

What it costs on a 180+ skill roster: ~9 calls, **7–10 s per eligible turn**,
~$0.0047/turn (11x Jev). Verdict: **not viable as the primary model here.**

**Keep Jev-1.13 as the production model.** Solar's only defensible integration is
hybrid — Jev skim + Solar verify — which produced the same answer for +0.54 s and
+13% cost: technically clean, but no measured quality gain, so not adopted.

## 7. Caveats

* n = 8 planted queries + 15 turns, one run per cell: indicative, not statistical.
  Thresholds derived from 8 work turns should be re-checked before production use.
* Ground truth is my own semantic labelling of which skill matches which request;
  two plants are ambiguous by design (they are the two misses).
* Latency was measured from this laptop over consumer internet; the *ratio*
  between models is the durable finding, not the absolute seconds.
* Solar availability was 99.97% (3d) at probe time; no transport errors occurred
  in ~150 calls.

## 8. Shipped as a result

* `SOLAR_MAX_CHOICES = 25` — `none_of_these` consumes a Solar label; the old 26
  clamp would have 422'd every chunked Call-1 on this roster (bug found by this probe).
* `_solar_config_warning()` — `suggest_skill` logs once, and `hermes jev-suggest
  status` prints a warning when Solar is paired with a Jev-tuned gate.
* Offline tests: 40 passing checks (39 `check()` calls), including the 26-label
  budget and the calibration guardrail.
* `scripts/solar_probe.py` — re-runnable batteries (`limits|accuracy|calib|verify|e2e`).