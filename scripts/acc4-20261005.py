import importlib.util, sys
from pathlib import Path
REPO=Path("/Users/alinagasaputra/Projects/hermes-plugins/hermes-plugin-jev-suggest")
spec=importlib.util.spec_from_file_location("solar_probe", REPO/"scripts"/"solar_probe.py")
sp=importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)
JEV="typesafe/jev-1.13"
SOLAR="upstage/solar-decide"
PPLX="perplexity/pplx-decider-v1-27b"
LIQ="liquid/d1"
which=sys.argv[1] if len(sys.argv)>1 else "all"
rc=0
if which in ("accuracy","all"):
    rc|=sp.battery_accuracy([(JEV,[148]),(PPLX,[148]),(LIQ,[148]),(SOLAR,[148])])
if which in ("thresholds","all"):
    rc|=sp.battery_thresholds([JEV,PPLX,LIQ,SOLAR])
sys.exit(rc)
