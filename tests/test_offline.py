"""Offline tests for hermes-plugin-jev-suggest (no network).

Run:  python3 tests/test_offline.py
Covers: chunking, skip logic, multi-chunk flow, none_of_these handling,
rate-limit hardening (retry/breaker/pacing/cache), payload cap.
"""
import importlib.util
import sys
import urllib.error
from email.message import Message
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("jevs", REPO / "__init__.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
_real_decide = m._decide  # restore before transport-level sections

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def base_settings(**over):
    st = {"mode": "on", "gate": 0.30, "fits": 0.3, "shortlist": 3,
          "excerpt": 700, "timeout_s": 10.0, "suggest_chars": 4000,
          "max_skills": 300, "chunk": 240,
          "retry_max_wait_s": 2.0, "breaker_threshold": 3,
          "breaker_cooldown_s": 120, "min_interval_s": 0.0,
          "cache_seconds": 0,
          "openrouter_model": "~typesafe/jev-latest",
          "openrouter_base_url": "https://openrouter.ai/api/alpha"}
    st.update(over)
    return st


# 1. chunking basics
check("148 items chunk 240 -> 1 group",
      len(m._chunk_items(list(range(148)), 240)) == 1)
check("300 items chunk 240 -> 2 groups",
      len(m._chunk_items(list(range(300)), 240)) == 2)
try:
    m._chunk_items([1], 256)
    check("chunk 256 rejected", False)
except ValueError:
    check("chunk 256 rejected", True)

# 2. skip reasons
check("slash skipped", m._skip_reason("/reset", 4000) == "slash")
check("already_routed skipped",
      m._skip_reason("x <skill_relevance> y", 4000) == "already_routed")
check("too_long skipped", m._skip_reason("a" * 4001, 4000) == "too_long")
check("trivial skipped", m._skip_reason("hi", 4000) == "trivial")
check("normal eligible", m._skip_reason("deploy the site now please", 4000) is None)

# 3. multi-chunk flow with stubbed _decide (300 fake skills, chunk=240)
roster = [{"name": f"s{i:03d}", "short": f"skill {i}",
           "full": f"skill {i} full", "excerpt": f"excerpt {i}"}
          for i in range(300)]
calls = []


def fake_decide(state, questions, settings):
    calls.append(set(questions))
    if len(calls) <= 2:
        n_opts = len(questions["which_skill"]["criteria"])  # the two chunk calls
        # chunk 0 (has gate): s000 wins weakly; chunk 1: s250 wins strongly
        probs = {}
        if len(calls) == 1:
            probs = {"s000": 0.30, m.NONE_OPTION: 0.05}
            return {"answers": {
                "which_skill": {"choice": "s000", "probabilities": probs},
                "need_act": {"noul": 0.9},
                "need_steps": {"noul": 0.9},
                "just_talk": {"noul": 0.0}}, "usage": {}}
        probs = {"s250": 0.80, m.NONE_OPTION: 0.02}
        return {"answers": {
            "which_skill": {"choice": "s250", "probabilities": probs}},
            "usage": {}}
    # call 2 (rerank): must contain none_of_these
    assert m.NONE_OPTION in questions["best_of_shortlist"]["criteria"], \
        "call2 missing none_of_these"
    return {"answers": {
        "best_of_shortlist": {"choice": "s250",
                              "probabilities": {"s250": 0.9}},
        "fits_s250": {"noul": 0.95}}, "usage": {}}


m._decide = fake_decide
m._ROSTER_CACHE = roster
st = base_settings()
winner = m.suggest_skill("please deploy the production site now", st)
check("multi-chunk winner is s250 (best-chunk-first)", winner == "s250")
check("3 decisions calls (2 chunks + rerank)", len(calls) == 3)
check("gate qs only on first chunk",
      any("need_act" in c for c in calls[:1]) and
      not any("need_act" in c for c in calls[1:2]))
check("chunks logged", m._LAST_INFO.get("chunks") == 2)

# 4. none-wins-chunk excluded: chunk 1 says none fits
calls.clear()


def fake_decide2(state, questions, settings):
    calls.append(set(questions))
    if len(calls) == 1:
        return {"answers": {
            "which_skill": {"choice": "s000",
                            "probabilities": {"s000": 0.4, m.NONE_OPTION: 0.05}},
            "need_act": {"noul": 0.9}, "need_steps": {"noul": 0.9},
            "just_talk": {"noul": 0.0}}, "usage": {}}
    if len(calls) == 2:
        return {"answers": {
            "which_skill": {"choice": m.NONE_OPTION,
                            "probabilities": {m.NONE_OPTION: 0.9,
                                              "s250": 0.05}}}, "usage": {}}
    return {"answers": {
        "best_of_shortlist": {"choice": "s000",
                              "probabilities": {"s000": 0.9}},
        "fits_s000": {"noul": 0.9}}, "usage": {}}


m._decide = fake_decide2
m._LAST_INFO.clear()
winner2 = m.suggest_skill("please deploy the production site now", st)
check("noisy chunk excluded, winner s000", winner2 == "s000")

# 5. Retry-After parsing
import time as _t
check("retry-after seconds", m._retry_after_s("2", _t.time()) == 2.0)
check("retry-after garbage -> None", m._retry_after_s("nan", _t.time()) is None)
check("retry-after inf -> None", m._retry_after_s("inf", _t.time()) is None)
check("retry-after empty -> None", m._retry_after_s("", _t.time()) is None)
check("retry-after None -> None", m._retry_after_s(None, _t.time()) is None)

# 6. breaker: 3x 429 (Retry-After too long) -> silent without transport
m._decide = _real_decide
m._BREAKER.clear()
m._RESP_CACHE.clear()
m._PACE_LAST.clear()
transport_calls = []


def _http_429(url, timeout=None):
    transport_calls.append(url)
    msg = Message()
    msg["retry-after"] = "99"  # longer than retry_max_wait_s=2.0
    raise urllib.error.HTTPError(url, 429, "Too Many Requests", msg, None)


m._urlopen = _http_429
m._openrouter_key = lambda **kw: "test-key"
bst = base_settings(min_interval_s=0.0, cache_seconds=0)
ep = m._endpoint(bst["openrouter_base_url"])
for _ in range(3):
    m._decide("some state", {"q": {"type": "noul", "instructions": "x?",
                                   "criteria": {"true": "y", "false": "n"}}}, bst)
check("breaker opens after 3x429", m._breaker_open(ep, bst) is True)
before = len(transport_calls)
m._decide("some state", {"q": {"type": "noul", "instructions": "x?",
                               "criteria": {"true": "y", "false": "n"}}}, bst)
check("open breaker skips transport", len(transport_calls) == before)

# 7. retry once when Retry-After fits
m._BREAKER.clear()
m._RESP_CACHE.clear()
m._PACE_LAST.clear()
t2_calls = []


def _http_429_then_ok(url, timeout=None):
    import io
    import json as _j
    t2_calls.append(url)
    if len(t2_calls) == 1:
        msg = Message()
        msg["retry-after"] = "0"
        raise urllib.error.HTTPError(url, 429, "Slow Down", msg, None)

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _j.dumps({"answers": {}, "usage": {}}).encode()
    return _R()


m._urlopen = _http_429_then_ok
out = m._decide("s", {"q": {"type": "noul", "instructions": "x?",
                            "criteria": {"true": "y", "false": "n"}}}, bst)
check("retry-after 0 retried once and returned", out == {"answers": {}, "usage": {}} and len(t2_calls) == 2)

# 8. cache: identical calls share one transport hit
m._BREAKER.clear()
m._RESP_CACHE.clear()
m._PACE_LAST.clear()
t3_calls = []


def _http_ok(url, timeout=None):
    import json as _j
    t3_calls.append(url)

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _j.dumps({"answers": {"q": {"type": "noul", "noul": 0.5}}, "usage": {}}).encode()
    return _R()


m._urlopen = _http_ok
cst = base_settings(min_interval_s=0.0, cache_seconds=300)
qq = {"q": {"type": "noul", "instructions": "x?", "criteria": {"true": "y", "false": "n"}}}
m._decide("same", qq, cst)
m._decide("same", qq, cst)
check("cache answers 2nd identical call", len(t3_calls) == 1)

# 9. payload cap: oversized state fails open without sending
m._BREAKER.clear()
m._RESP_CACHE.clear()
t4_calls = []
m._urlopen = lambda url, timeout=None: t4_calls.append(url) or (_ for _ in ()).throw(AssertionError("must not send"))
big = "x" * (m.MAX_PAYLOAD_BYTES + 1)
out = m._decide(big, {"q": {"type": "noul", "instructions": "x?",
                            "criteria": {"true": "y", "false": "n"}}}, base_settings(cache_seconds=0))
check("oversize payload fails open unsent", out is None and not t4_calls)

# 10. outcome taxonomy (unified schema)
m._decide = _real_decide
m._BREAKER.clear()
m._RESP_CACHE.clear()
m._PACE_LAST.clear()
m._ROSTER_CACHE = [{"name": "only-skill", "short": "the only skill",
                    "full": "the only skill full", "excerpt": "excerpt"}]


def _gate_fail(state, questions, settings):
    if "need_act" in questions:
        return {"answers": {
            "which_skill": {"choice": "only-skill", "probabilities": {"only-skill": 0.9}},
            "need_act": {"noul": 0.0}, "need_steps": {"noul": 0.0},
            "just_talk": {"noul": 0.9}}, "usage": {}}
    raise AssertionError("call2 must not run when gate fails")


m._decide = _gate_fail
w = m.suggest_skill("please do something with files now", base_settings())
check("gate fail -> silent/gate",
      w == "" and m._LAST_INFO.get("outcome") == "silent"
      and m._LAST_INFO.get("reason") == "gate")


def _fits_fail(state, questions, settings):
    if "need_act" in questions:
        return {"answers": {
            "which_skill": {"choice": "only-skill", "probabilities": {"only-skill": 0.9}},
            "need_act": {"noul": 0.9}, "need_steps": {"noul": 0.9},
            "just_talk": {"noul": 0.0}}, "usage": {}}
    return {"answers": {
        "best_of_shortlist": {"choice": "only-skill",
                              "probabilities": {"only-skill": 0.85},
                              "confidence": 0.77},
        "fits_only-skill": {"noul": 0.05}}, "usage": {}}


m._decide = _fits_fail
w = m.suggest_skill("please do something with files now", base_settings())
check("fits fail -> silent/fits",
      w == "" and m._LAST_INFO.get("reason") == "fits")
check("probability captured", m._LAST_INFO.get("probability") == 0.85)
check("confidence captured", m._LAST_INFO.get("confidence") == 0.77)


def _always_none(state, questions, settings):
    return None


m._decide = _always_none
w = m.suggest_skill("please do something with files now", base_settings())
check("transport fail -> silent/api_error",
      w == "" and m._LAST_INFO.get("reason") == "api_error")

# 11. hook logs skip paths with unified fields (no production log touched)
m._decide = _real_decide
captured = []
m._log_event = captured.append


class _Ctx:
    def __init__(self, settings):
        self._s = settings

    def get_config(self, key, default):
        return self._s.get(key, default)


hook_settings = base_settings(mode="on")
hook = m.make_hook_handler(_Ctx(hook_settings))
r = hook(user_message="/reset", session_id="s1", turn_id="t1")
check("hook slash returns empty", r == {})
check("hook slash logged silent/slash",
      len(captured) == 1 and captured[0].get("outcome") == "silent"
      and captured[0].get("reason") == "slash"
      and captured[0].get("origin") == "hook"
      and captured[0].get("mode") == "on"
      and captured[0].get("session_id") == "s1")

# 12. solar clamp: chunk 240 -> 25 on upstage/solar-decide (26 labels incl. none_of_these)
m._ROSTER_CACHE = [{"name": f"c{i:03d}", "short": f"cand {i}",
                    "full": f"cand {i} full", "excerpt": f"ex {i}"}
                   for i in range(60)]
solar_calls = []


def _solar_stub(state, questions, settings):
    solar_calls.append(set(questions))
    if "best_of_shortlist" not in questions:
        names = [k for k in questions["which_skill"]["criteria"] if k != m.NONE_OPTION]
        assert len(names) <= m.SOLAR_MAX_CHOICES, f"chunk too big for solar: {len(names)}"
        ans = {"which_skill": {"choice": names[0], "probabilities": {names[0]: 0.8}}}
        if "need_act" in questions:
            ans.update({"need_act": {"noul": 0.9}, "need_steps": {"noul": 0.9},
                        "just_talk": {"noul": 0.0}})
        return {"answers": ans, "usage": {}}
    best = "c000"
    return {"answers": {
        "best_of_shortlist": {"choice": best, "probabilities": {best: 0.9}},
        "fits_c000": {"noul": 0.9}}, "usage": {}}


m._decide = _solar_stub
m._LAST_INFO.clear()
solar_st = base_settings(openrouter_model="upstage/solar-decide")
w = m.suggest_skill("please deploy the production site now sir", solar_st)
check("solar clamps to 25-item chunks", m._LAST_INFO.get("chunks") == 3)
check("solar flow completes", w == "c000")

# 14. every solar chunk must fit 26 labels including none_of_these
big_roster = [{"name": f"d{i:03d}", "short": f"d{i}", "full": f"d{i} full",
               "excerpt": f"ex {i}"} for i in range(60)]
seen_sizes = []
m._ROSTER_CACHE = big_roster


def _label_stub(state, questions, settings):
    q = questions["which_skill"]
    seen_sizes.append(len(q["criteria"]))
    assert len(q["criteria"]) <= 26, f"solar label overflow: {len(q['criteria'])}"
    names_ = [k for k in q["criteria"] if k != m.NONE_OPTION]
    ans = {"which_skill": {"choice": names_[0], "probabilities": {names_[0]: 0.8}}}
    if "need_act" in questions:
        ans.update({"need_act": {"noul": 0.9}, "need_steps": {"noul": 0.9},
                    "just_talk": {"noul": 0.0}})
    if "best_of_shortlist" in questions:
        return {"answers": {"best_of_shortlist": {"choice": "d000", "probabilities": {"d000": 0.9}},
                            "fits_d000": {"noul": 0.9}}, "usage": {}}
    return {"answers": ans, "usage": {}}


m._decide = _label_stub
m.suggest_skill("please deploy the production site now sir",
                base_settings(openrouter_model="upstage/solar-decide"))
check("solar chunks carry none_of_these within 26", max(seen_sizes) == 26 and len(seen_sizes) >= 3)

# 15. solar calibration guardrail (probe 2026-09-29: gate scores ~2.4x lower than Jev)
check("solar warns on Jev-tuned gate",
      bool(m._solar_config_warning("upstage/solar-decide", {"gate": 0.30})))
check("solar gate 0.10 is accepted",
      m._solar_config_warning("upstage/solar-decide", {"gate": 0.10}) is None)
check("jev never warns",
      m._solar_config_warning("typesafe/jev-1.13", {"gate": 0.30}) is None)

# 13. mode-off kill switch (env var removed): explicit settings win
m._decide = _real_decide
off_st = dict(base_settings(), mode="off")
calls_before = len(solar_calls)
w = m.suggest_skill("please deploy the production site now sir", off_st)
check("mode off blocks suggest_skill", w == "" and len(solar_calls) == calls_before)
check("mode off outcome logged", m._LAST_INFO.get("reason") == "disabled")
hook2 = m.make_hook_handler(_Ctx(dict(hook_settings, mode="off")))
captured.clear()
r = hook2(user_message="please deploy the site now", session_id="s2", turn_id="t2")
check("mode off hook returns empty", r == {})
check("mode off hook does not call decide", len(captured) == 0)

print("---")
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
