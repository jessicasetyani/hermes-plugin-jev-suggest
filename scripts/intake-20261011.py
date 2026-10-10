#!/usr/bin/env python3
"""Intake battery for the 5 new deciders that passed viability (2026-10-11).

    P=29cb6471-a904-45f2-aebf-b46d00742866
    bws run --project-id $P --no-inherit-env -- python3 scripts/intake-20261011.py [accuracy|thresholds|all]

Needs each candidate in the plugin ALLOWED_MODELS or the run silently measures
the fallback (~typesafe/jev-latest) while labelling it a candidate.
"""
from __future__ import annotations
import importlib.util, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Measure THIS checkout, not the installed copy (solar_probe defaults to the
# install). Env is lost under `bws run --no-inherit-env`, so set it here.
os.environ.setdefault("JEV_SUGGEST_PLUGIN", str(REPO / "__init__.py"))
spec = importlib.util.spec_from_file_location("solar_probe", REPO / "scripts" / "solar_probe.py")
sp = importlib.util.module_from_spec(spec); spec.loader.exec_module(sp)

JEV = "typesafe/jev-1.13"
CAND = ["openai/gpt-6-luna-decisions", "nace-ai/drex-v1.5", "microsoft/microsoft-decision-1",
        "cloudflare/clef-omni", "inception/mercury-decide"]
bad = [c for c in CAND + [JEV] if c not in sp.m.ALLOWED_MODELS]
assert not bad, f"not in plugin allowlist (would measure the fallback): {bad}"

which = sys.argv[1] if len(sys.argv) > 1 else "all"
rc = 0
if which in ("accuracy", "all"):
    rc |= sp.battery_accuracy([(JEV, [148])] + [(c, [148]) for c in CAND])
if which in ("thresholds", "all"):
    rc |= sp.battery_thresholds([JEV] + CAND)
sys.exit(rc)
