#!/usr/bin/env python3
"""Quick probe for 3 new decision models: primitives, choice caps, calibration signals."""
import json, os, sys, time, urllib.request, urllib.error
ENDPOINT="https://openrouter.ai/api/alpha/decisions"
KEY=os.environ.get("OPENROUTER_API_KEY","")
if not KEY:
    sys.exit("NO_KEY")
MODELS=["perplexity/pplx-decider-v1-27b","cloudflare/clef-flash","cloudflare/clef"]
def call(model, state, questions, timeout=60):
    body=json.dumps({"model":model,"state":state,"questions":questions}).encode()
    req=urllib.request.Request(ENDPOINT,data=body,headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json"})
    t0=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            d=json.loads(r.read())
            return {"ok":True,"lat":round(time.monotonic()-t0,2),"answers":d.get("answers"),"usage":d.get("usage"),"bytes":len(body)}
    except urllib.error.HTTPError as e:
        try: det=e.read().decode()[:500]
        except: det=""
        return {"ok":False,"http":e.code,"lat":round(time.monotonic()-t0,2),"detail":det,"bytes":len(body)}
    except Exception as e:
        return {"ok":False,"error":f"{type(e).__name__}:{e}"[:300],"lat":round(time.monotonic()-t0,2)}
def names(n): return [f"opt-{i:03d}" for i in range(n)]
out={"models":{}}
SHORT="Help! My payouts have been failing for 3 days."
for m in MODELS:
    print(f"\n=== {m} ===",flush=True)
    r={}
    # primitives
    r["noul"]=call(m,SHORT,{"urgent":{"type":"noul","instructions":"Does this message convey urgency?","criteria":{"true":"Explicitly time-sensitive","false":"No urgency expressed"}}})
    print(f" noul: {'OK' if r['noul'].get('ok') else 'FAIL'} {r['noul'].get('http') or r['noul'].get('error') or ''} {r['noul'].get('lat')}s {json.dumps(r['noul'].get('answers'))[:250] if r['noul'].get('ok') else r['noul'].get('detail','')[:250]}",flush=True)
    r["choice3"]=call(m,SHORT,{"department":{"type":"choice","instructions":"Which team should handle this?","criteria":{"billing":"Payments, invoicing, refunds","technical":"Bugs, outages, integrations","sales":"Pricing, upgrades, new accounts"}}})
    print(f" choice3: {'OK' if r['choice3'].get('ok') else 'FAIL'} {r['choice3'].get('http') or ''} {r['choice3'].get('lat')}s {json.dumps(r['choice3'].get('answers'))[:300] if r['choice3'].get('ok') else r['choice3'].get('detail','')[:250]}",flush=True)
    r["score3"]=call(m,SHORT,{"frustration":{"type":"score","instructions":"How frustrated is the customer?","criteria":["Calm","Frustrated","Very angry"]}})
    print(f" score3: {'OK' if r['score3'].get('ok') else 'FAIL'} {r['score3'].get('http') or ''} {r['score3'].get('lat')}s {json.dumps(r['score3'].get('answers'))[:300] if r['score3'].get('ok') else r['score3'].get('detail','')[:250]}",flush=True)
    # choice caps
    for n,extra in [(26,False),(27,False),(25,True),(26,True),(100,False),(240,False)]:
        crit={x:x for x in names(n)}
        if extra: crit["none_of_these"]="None fit."
        label=f"choice {n}"+(" +none" if extra else "")
        rr=call(m,"deploy the site now",{"q":{"type":"choice","instructions":"pick","criteria":crit}})
        r[label]=rr
        print(f" {label:16s}: {'OK' if rr.get('ok') else 'FAIL'} {rr.get('http') or rr.get('error') or ''} {rr.get('lat')}s {(rr.get('detail','')[:200] if not rr.get('ok') else '')}",flush=True)
    # gate calibration: work vs chit-chat
    for label,state in [("work","Our OneDrive backup job has been failing every night since the SharePoint migration, fix it now"),("chitchat","hi")]:
        rr=call(m,state,{
            "need_act":{"type":"noul","instructions":"Does this turn need the agent to act on the user's stuff (files, tools, accounts)?","criteria":{"true":"Needs tools/files/actions","false":"Just talk, no action"}},
            "need_steps":{"type":"noul","instructions":"Does this turn need following written multi-step procedures?","criteria":{"true":"Needs a documented workflow","false":"No procedure needed"}},
            "just_talk":{"type":"noul","instructions":"Is this just chit-chat with no task?","criteria":{"true":"Greeting/small talk only","false":"Has a real task"}}})
        r[f"gate-{label}"]=rr
        if rr.get("ok"):
            a=rr["answers"]
            try:
                na=(float(a["need_act"]["noul"])+float(a["need_steps"]["noul"]))/2
                jt=float(a["just_talk"]["noul"])
                gate=na*(1-jt)
                print(f" gate-{label}: need_act={a['need_act']['noul']} need_steps={a['need_steps']['noul']} just_talk={jt} => gate={gate:.3f} lat={rr['lat']}s usage={rr.get('usage')}",flush=True)
            except Exception as e:
                print(f" gate-{label}: OK but parse fail {e} {json.dumps(a)[:400]}",flush=True)
        else:
            print(f" gate-{label}: FAIL {rr}",flush=True)
    out["models"][m]=r
print("\nFULL_JSON_BEGIN")
print(json.dumps(out,default=str)[:20000])
print("FULL_JSON_END")
