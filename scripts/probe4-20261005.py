#!/usr/bin/env python3
import json, os, sys, time, urllib.request, urllib.error
ENDPOINT="https://openrouter.ai/api/alpha/decisions"
KEY=os.environ.get("OPENROUTER_API_KEY","")
if not KEY:
    sys.exit("NO_KEY")
MODELS=["typesafe/jev-1.13","upstage/solar-decide","perplexity/pplx-decider-v1-27b","liquid/d1"]
def call(model, state, questions, timeout=60):
    body=json.dumps({"model":model,"state":state,"questions":questions}).encode()
    req=urllib.request.Request(ENDPOINT,data=body,headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json"})
    t0=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            d=json.loads(r.read())
            return {"ok":True,"lat":round(time.monotonic()-t0,2),"answers":d.get("answers"),"usage":d.get("usage"),"bytes":len(body)}
    except urllib.error.HTTPError as e:
        try: det=e.read().decode()[:800]
        except: det=""
        return {"ok":False,"http":e.code,"lat":round(time.monotonic()-t0,2),"detail":det,"bytes":len(body)}
    except Exception as e:
        return {"ok":False,"error":str(type(e).__name__)+":"+str(e)[:300],"lat":round(time.monotonic()-t0,2)}
def names(n):
    return ["opt-%03d" % i for i in range(n)]
SHORT="Help! My payouts have been failing for 3 days."
for m in MODELS:
    print("\n=== "+m+" ===",flush=True)
    tests=[
        ("noul",{"urgent":{"type":"noul","instructions":"Does this message convey urgency?","criteria":{"true":"Explicitly time-sensitive","false":"No urgency expressed"}}}),
        ("choice3",{"department":{"type":"choice","instructions":"Which team should handle this?","criteria":{"billing":"Payments, invoicing, refunds","technical":"Bugs, outages, integrations","sales":"Pricing, upgrades, new accounts"}}}),
        ("score3",{"frustration":{"type":"score","instructions":"How frustrated is the customer?","criteria":["Calm","Frustrated","Very angry"]}}),
    ]
    for label,qs in tests:
        rr=call(m,SHORT,qs)
        if rr.get("ok"):
            print(" "+label+": OK "+str(rr["lat"])+"s usage="+str(rr.get("usage"))+" ans="+json.dumps(rr.get("answers"))[:500],flush=True)
        else:
            print(" "+label+": FAIL "+str(rr.get("http") or rr.get("error"))+" "+str(rr["lat"])+"s "+rr.get("detail","")[:500],flush=True)
    caps=[(26,False),(27,False),(25,True),(26,True),(100,False),(240,False)]
    for n,extra in caps:
        crit={}
        for x in names(n):
            crit[x]=x
        if extra:
            crit["none_of_these"]="None fit."
        label="choice "+str(n)+(" +none" if extra else "")
        rr=call(m,"deploy the site now",{"q":{"type":"choice","instructions":"pick","criteria":crit}})
        if rr.get("ok"):
            print(" "+label.ljust(16)+": OK "+str(rr["lat"])+"s",flush=True)
        else:
            print(" "+label.ljust(16)+": FAIL "+str(rr.get("http") or rr.get("error") or "")+" "+str(rr["lat"])+"s "+rr.get("detail","")[:300],flush=True)
    pairs=[("work","Our OneDrive backup job has been failing every night since the SharePoint migration, fix it now"),("chitchat","hi")]
    for glabel,state in pairs:
        rr=call(m,state,{
            "need_act":{"type":"noul","instructions":"Does this turn need the agent to act on the user's stuff (files, tools, accounts)?","criteria":{"true":"Needs tools/files/actions","false":"Just talk, no action"}},
            "need_steps":{"type":"noul","instructions":"Does this turn need following written multi-step procedures?","criteria":{"true":"Needs a documented workflow","false":"No procedure needed"}},
            "just_talk":{"type":"noul","instructions":"Is this just chit-chat with no task?","criteria":{"true":"Greeting/small talk only","false":"Has a real task"}}})
        if rr.get("ok"):
            a=rr["answers"]
            try:
                na=(float(a["need_act"]["noul"])+float(a["need_steps"]["noul"]))/2
                jt=float(a["just_talk"]["noul"])
                gate=na*(1-jt)
                print(" gate-"+glabel+": need_act="+str(a["need_act"]["noul"])+" need_steps="+str(a["need_steps"]["noul"])+" just_talk="+str(jt)+" => gate="+format(gate,".3f")+" lat="+str(rr["lat"])+"s usage="+str(rr.get("usage")),flush=True)
            except Exception as e:
                print(" gate-"+glabel+": OK but parse fail "+str(e)+" "+json.dumps(a)[:500],flush=True)
        else:
            print(" gate-"+glabel+": FAIL "+str(rr),flush=True)
