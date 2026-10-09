"""v1.1 decision-model calibration (2026-10-09).

Same harness as acc4-20261005.py / thr4-20261005.py, retargeted at the checkpoint
that replaced the retired pplx-decider-v1-27b:

    perplexity/pplx-decider-v1.1-27b   (canonical -20261006)

Jev is re-measured in the same run so the comparison is same-session, not a
cross-session number carried forward from 2026-10-05.

Run from a checkout whose __init__.py allowlist already carries the v1.1 slug —
otherwise the plugin silently falls back to ~typesafe/jev-latest and the whole
run measures Jev while labelling it pplx:

    P=29cb6471-a904-45f2-aebf-b46d00742866
    bws run --project-id $P --no-inherit-env -- python3 scripts/acc-v11-20261009.py [accuracy|thresholds|all]
"""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("solar_probe", HERE / "solar_probe.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

JEV = "typesafe/jev-1.13"
PPLX11 = "perplexity/pplx-decider-v1.1-27b"

# Guard: the allowlist must accept the slug, or every "pplx" number below is Jev.
assert PPLX11 in sp.m.ALLOWED_MODELS, (
    f"{PPLX11!r} not in the plugin allowlist — this run would silently measure "
    f"the fallback model ({sp.m.DEFAULT_MODEL!r})."
)

which = sys.argv[1] if len(sys.argv) > 1 else "all"
rc = 0
if which in ("accuracy", "all"):
    rc |= sp.battery_accuracy([(JEV, [148]), (PPLX11, [148])])
if which in ("thresholds", "all"):
    rc |= sp.battery_thresholds([PPLX11, JEV])
sys.exit(rc)
