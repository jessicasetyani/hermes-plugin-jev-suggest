#!/usr/bin/env python3
"""Accuracy + thresholds battery for new deciders vs Jev (production shape).
Run: bws run --project-id $P --no-inherit-env -- python3 scripts/new_decoders_accuracy.py
Writes raw to docs/calibration/raw/.
"""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("solar_probe", REPO / "scripts" / "solar_probe.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

JEV = "typesafe/jev-1.13"
PPLX = "perplexity/pplx-decider-v1-27b"
CLEF_FLASH = "cloudflare/clef-flash"
CLEF = "cloudflare/clef"

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    rc = 0
    if which in ("accuracy", "all"):
        # 8 planted queries x N=148 (single-chunk production shape) x 4 models
        rc |= sp.battery_accuracy([(JEV, [148]), (PPLX, [148]), (CLEF_FLASH, [148]), (CLEF, [148])])
    if which in ("thresholds", "all"):
        rc |= sp.battery_thresholds([JEV, PPLX, CLEF_FLASH, CLEF])
    raise SystemExit(rc)
