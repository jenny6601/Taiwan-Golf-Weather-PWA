#!/usr/bin/env python3
"""Read-only same-run Foreca vs Visual Crossing forecast comparison.
Only aggregate metrics and fetch timestamps are written; no raw hourly values or credentials.
"""
import concurrent.futures, json, os, statistics, time, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

TZ=ZoneInfo("Asia/Taipei")
FORECA_KEY=os.getenv("FORECA_API_KEY","").strip()
VC_KEY=os.getenv("VISUAL_CROSSING_API_KEY","").strip()
COURSES={
 "礁溪":(24.8389,121.7389),
 "林口":(25.0714,121.3719),
 "桃園":(24.8789,121.2069),
 "台北":(25.0489,121.2825),
 "揚昇":(24.9208,121.1878),
}
def now(): return datetime.now(TZ).isoformat(timespec="seconds")
def get_json(url, headers=None):
 req=urllib.request.Request(url,headers={"User-Agent":"Taiwan-Golf-Weather-Control-Test/1.0","Accept":"application/json",**(headers or {})})
 with urllib.request.urlopen(req,timeout=60) as r: return json.loads(r.read().decode("utf-8"))
def fetch_foreca(lat,lon):
 if not FORECA_KEY: raise RuntimeError("FORECA_API_KEY secret missing")
 loc=f"{lon:.5f},{lat:.5f}"
 q=urllib.parse.urlencode({"periods":169,"tz":"Asia/Taipei","tempunit":"C","windunit":"KMH","dataset":"full","rounding":0})
 started=now()
 d=get_json("https://weatherapi.foreca.net/api/v1/forecast/hourly/"+loc+"?"+q,{"Authorization":"Bearer "+FORECA_KEY})
 completed=now()
 rows={}
 for x in d.get("forecast",[]):
  t=str(x.get("time",""))[:16]
  if len(t)==16:
   rows[t]={"wind":x.get("windSpeed"),"gust":x.get("windGust"),"dir":x.get("windDir"),"temp":x.get("temperature")}
 return {"started":started,"completed":completed,"rows":rows}
def fetch_vc(lat,lon):
 if not VC_KEY: raise RuntimeError("VISUAL_CROSSING_API_KEY secret missing")
 loc=f"{lat:.5f},{lon:.5f}"
 params={"unitGroup":"metric","include":"hours","elements":"datetime,temp,feelslike,windspeed,windgust,winddir,precip,precipprob,conditions","contentType":"json","key":VC_KEY}
 url="https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"+urllib.parse.quote(loc,safe=",")+"/next7days?"+urllib.parse.urlencode(params)
 started=now(); d=get_json(url); completed=now()
 rows={}
 for day in d.get("days",[]):
  date=day.get("datetime")
  for x in day.get("hours",[]):
   clock=x.get("datetime")
   if date and clock:
    t=f"{date}T{str(clock)[:5]}"
    rows[t]={"wind":x.get("windspeed"),"gust":x.get("windgust"),"dir":x.get("winddir"),"temp":x.get("temp")}
 return {"started":started,"completed":completed,"rows":rows}
def avg(vals): return round(statistics.mean(vals),2) if vals else None
def med(vals): return round(statistics.median(vals),2) if vals else None
def circular_diff(a,b):
 d=abs((a-b)%360)
 return min(d,360-d)
def compare(name,lat,lon):
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
  ff=ex.submit(fetch_foreca,lat,lon); vf=ex.submit(fetch_vc,lat,lon)
  f=ff.result(); v=vf.result()
 common=sorted(set(f["rows"]) & set(v["rows"]))
 # Exclude past/current partial hours; compare the next five forecast days, daytime only.
 cutoff=datetime.now(TZ).strftime("%Y-%m-%dT%H")
 common=[t for t in common if t>=cutoff and 6<=int(t[11:13])<=18][:65]
 metrics={}
 for key in ("wind","gust","temp"):
  pairs=[(f["rows"][t].get(key),v["rows"][t].get(key)) for t in common]
  pairs=[(a,b) for a,b in pairs if isinstance(a,(int,float)) and isinstance(b,(int,float))]
  delta=[b-a for a,b in pairs]
  metrics[key]={"n":len(pairs),"foreca_mean":avg([a for a,b in pairs]),"vc_mean":avg([b for a,b in pairs]),"vc_minus_foreca_mean":avg(delta),"absolute_error_mean":avg([abs(x) for x in delta]),"delta_median":med(delta)}
 dp=[circular_diff(f["rows"][t]["dir"],v["rows"][t]["dir"]) for t in common if isinstance(f["rows"][t].get("dir"),(int,float)) and isinstance(v["rows"][t].get("dir"),(int,float))]
 return {"course":name,"matched_daytime_hours":len(common),"foreca_fetch_started":f["started"],"foreca_fetch_completed":f["completed"],"vc_fetch_started":v["started"],"vc_fetch_completed":v["completed"],"wind_direction_absolute_difference_mean_deg":avg(dp),"metrics":metrics}
def main():
 if not FORECA_KEY or not VC_KEY: raise SystemExit("Required GitHub Secrets missing; no data fetched.")
 started=now()
 results=[]; errors=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  fs={pool.submit(compare,n,*coords):n for n,coords in COURSES.items()}
  for future,n in fs.items():
   try: results.append(future.result())
   except Exception as e: errors.append({"course":n,"error":str(e)[:300]})
 results.sort(key=lambda x:x["course"])
 report={"test":"same-run provider control comparison","generated_at":now(),"test_started_at":started,"timezone":"Asia/Taipei","course_count":len(results),"expected_courses":5,"scope":"next five days daytime 06:00-18:00, max 65 matched hours per course","course_results":results,"errors":errors,"raw_hourly_values_saved":False}
 out="/tmp/same-run-provider-control.json"
 with open(out,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
 print(json.dumps({"generated_at":report["generated_at"],"courses_ok":len(results),"expected":5,"errors":errors,"output":out},ensure_ascii=False))
 if len(results)!=5: raise SystemExit("Incomplete control test.")
if __name__=="__main__": main()
