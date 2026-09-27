# hermes-plugin-jev-suggest

Automatic skill suggestion for Hermes Agent via Jev decision model (`~typesafe/jev-latest` on OpenRouter).

## What it does

Runs on every turn via the `pre_llm_call` hook, before the LLM tool loop:

1. **Call 1 — skim**: one `choice` over the whole skill roster (60-char descriptions) + 3 `noul` need-checks. If the gate score is below `0.30` (chit-chat), stop — no second call.
2. **Call 2 — verify**: re-read the top-3 with full descriptions (700 chars). May reject all of them.
3. **Inject**: at most one advisory line into the current user message:

```
<skill_relevance>
Relevant to the current request: <name>. Ignore this if it does not fit...
</skill_relevance>
```

The roster is never modified, the system prompt stays byte-stable (prefix caching safe), and the agent keeps the final decision.

## Why this shape

Hermes truncates skill descriptions to 60 chars in the system prompt, so similarly-named skills are easily confused (TypeSafe cookbook: 16.8% wrong-load without suggestion, 7.3% with). Two cheap Jev calls (~$0.00002/turn) fix the routing without touching the harness.

## Install (rollout to another Hermes agent)

```bash
hermes plugins install jessicasetyani/hermes-plugin-jev-suggest --enable
# takes effect on next session (/reset); restart gateway if running via gateway
```

Updates: `hermes plugins update jev-suggest` (or `check-updates` to poll).

## Configuration (env, all optional)

| Var | Default | Meaning |
|---|---|---|
| `JEV_SUGGEST_MODEL` | `~typesafe/jev-latest` | Decision model (alias = always latest Jev) |
| `JEV_SUGGEST_DISABLE` | unset | `1/true/yes/on` = kill switch, no calls made |
| `JEV_SUGGEST_GATE` | `0.30` | Below this Call-1 score → suggest nothing |
| `JEV_SUGGEST_FITS` | `0.30` | Below this Call-2 fit → suggest nothing |
| `JEV_SUGGEST_TIMEOUT` | `10` | Seconds per Decisions API call |
| `JEV_SUGGEST_SHORTLIST` | `3` | Candidates carried from Call 1 to Call 2 |

Requires `OPENROUTER_API_KEY` in env (injected by Bitwarden Secrets Manager at Hermes runtime). Missing key / timeout / error → fail-open: the turn proceeds with no suggestion.

## Exit plan

1. `JEV_SUGGEST_DISABLE=1` — stop calls without uninstalling
2. `hermes plugins disable jev-suggest` — disable permanently
3. Delete `~/.hermes/plugins/jev-suggest/` + `/reset` — zero residue
4. Fail criteria: added p50 latency > 0.5s, bad suggestions 2 days straight, or error rate > 5%

## Source of truth

This repo. Deployed copy at `~/.hermes/plugins/jev-suggest/` is a plain copy — after editing here, copy over and re-validate with `hermes plugins validate ~/.hermes/plugins/jev-suggest`.
