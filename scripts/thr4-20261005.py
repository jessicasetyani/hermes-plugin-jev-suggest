import json, os, sys, time, urllib.request, urllib.error
ENDPOINT="https://openrouter.ai/api/alpha/decisions"
KEY=os.environ.get("OPENROUTER_API_KEY","")
if not KEY: sys.exit("NO_KEY")
TURNS=[("work","Tolong cek uptime dan tanggal expired SSL certificate untuk api-gateway kita"),("work","Reconcile the monthly ledger xlsx against the bank statement export"),("work","SonarQube found 12 new blocker issues; triage them for the release gate"),("chitchat","Halo, apa kabar hari ini?"),("chitchat","Menurutmu film terbaik dekade ini apa dan kenapa?"),("ambiguous","Kira-kira apa yang harus kita lakukan soal ini ya?")]
MODELS=["typesafe/jev-1.13","perplexity/pplx-decider-v1-27b","liquid/d1","upstage/solar-decide"]
def call(model, state, timeout=40):
    qs={"need_act":{"type":"noul","instructions":"Does this turn need the agent to act?","criteria":{"true":"Needs tools/files/actions","false":"Just talk"}},"need_steps":{"type":"noul","instructions":"Does this need procedures?","criteria":{"true":"Needs workflow","false":"No procedure"}},"just_talk":{"type":"noul","instructions":"Is this just chit-chat?","criteria":{"true":"Greeting/small talk only","false":"Has real task"}}}
    body=json.dumps({"model":model,"state":state,"questions":qs}).encode()
    req=urllib.request.Request(ENDPOINT,data=body,headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json"})
    t0=time.monotonic()
    try:
        from urllib.request import urlopen
        with urlopen(req,timeout=timeout) as r:
            d=json.loads(r.read())
            return {"ok":True,"lat":round(time.monotonic()-t0,2),"a":d.get("answers"),"u":d.get("usage")}
    except urllib.error.HTTPError as e:
        return {"ok":False,"http":e.code,"lat":round(time.monotonic()-t0,2)}
    except Exception as e:
        return {"ok":False,"err":str(e)[:150],"lat":round(time.monotonic()-t0,2)}
for m in MODELS:
    print("\n=="+m,flush=True)
    for kind,txt in TURNS:
        rr=call(m,txt)
        if rr.get("ok"):
            a=rr["a"]
            na=(float(a["need_act"]["noul"])+float(a["need_steps"]["noul"]))/2
            jt=float(a["just_talk"]["noul"])
            print(f" {kind:9s} gate={(na*(1-jt)):.3f} na={na:.2f} jt={jt:.2f} lat={rr['lat']}s",flush=True)
        else:
            print(f" {kind} FAIL {rr}",flush=True)
