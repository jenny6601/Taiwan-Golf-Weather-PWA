#!/usr/bin/env python3
"""Collect forecast snapshots and CWA observations for offline accuracy validation.
Run with 'forecast' or 'observation'. Writes one JSON snapshot to /tmp only.
"""
import concurrent.futures, json, os, sys, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

TZ=ZoneInfo("Asia/Taipei")
FORECA_KEY=os.getenv("FORECA_API_KEY","").strip()
VC_KEY=os.getenv("VISUAL_CROSSING_API_KEY","").strip()
CWA_KEY=os.getenv("CWA_API_KEY","").strip()
COURSES={
 "礁溪":{"lat":24.8389,"lon":121.7389,"station":"C0UB00","station_name":"淡江大學蘭陽校園","distance_km":2.3},
 "林口":{"lat":25.0714,"lon":121.3719,"station":"C0AH50","station_name":"林口","distance_km":0.3},
 "桃園":{"lat":24.8789,"lon":121.2069,"station":"C0C670","station_name":"龍潭","distance_km":1.0},
 "台北":{"lat":25.0489,"lon":121.2825,"station":"CAC020","station_name":"國二E009K","distance_km":3.6},
 "揚昇":{"lat":24.9208,"lon":121.1878,"station":"82C160","station_name":"茶改場","distance_km":1.6},
}
def stamp(): return datetime.now(TZ).isoformat(timespec="seconds")
def get_json(url,headers=None):
 req=urllib.request.Request(url,headers={"User-Agent":"Taiwan-Golf-Weather-Validation/1.0","Accept":"application/json",**(headers or {})})
 with urllib.request.urlopen(req,timeout=90) as r:return json.loads(r.read().decode("utf-8"))
def foreca(lat,lon):
 if not FORECA_KEY: raise RuntimeError("FORECA_API_KEY missing")
 q=urllib.parse.urlencode({"periods":169,"tz":"Asia/Taipei","tempunit":"C","windunit":"KMH","dataset":"full","rounding":0})
 loc=f"{lon:.5f},{lat:.5f}"
 d=get_json("https://weatherapi.foreca.net/api/v1/forecast/hourly/"+loc+"?"+q,{"Authorization":"Bearer "+FORECA_KEY})
 out=[]
 for x in d.get("forecast",[]):
  out.append({"time":str(x.get("time",""))[:16],"wind_kmh":x.get("windSpeed"),"gust_kmh":x.get("windGust"),"wind_dir_deg":x.get("windDir"),"temp_c":x.get("temperature")})
 return out
def vc(lat,lon):
 if not VC_KEY: raise RuntimeError("VISUAL_CROSSING_API_KEY missing")
 loc=f"{lat:.5f},{lon:.5f}"
 p={"unitGroup":"metric","include":"hours","elements":"datetime,temp,windspeed,windgust,winddir","contentType":"json","key":VC_KEY}
 url="https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"+urllib.parse.quote(loc,safe=",")+"/next7days?"+urllib.parse.urlencode(p)
 d=get_json(url);out=[]
 for day in d.get("days",[]):
  for x in day.get("hours",[]):
   if day.get("datetime") and x.get("datetime"):
    out.append({"time":day["datetime"]+"T"+str(x["datetime"])[:5],"wind_kmh":x.get("windspeed"),"gust_kmh":x.get("windgust"),"wind_dir_deg":x.get("winddir"),"temp_c":x.get("temp")})
 return out
def obs_value(v,keys):
 if isinstance(v,dict):
  for k in keys:
   if k in v:return v[k]
  for x in v.values():
   z=obs_value(x,keys)
   if z is not None:return z
 return v if isinstance(v,(int,float,str)) else None
def observation():
 if not CWA_KEY: raise RuntimeError("CWA_API_KEY missing")
 q=urllib.parse.urlencode({"Authorization":CWA_KEY,"format":"JSON"})
 d=get_json("https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?"+q)
 if str(d.get("success","")).lower()!="true":raise RuntimeError("CWA API unsuccessful")
 allst=d.get("records",{}).get("Station",[])
 byid={s.get("StationId"):s for s in allst if isinstance(s,dict)}
 out={}
 for course,c in COURSES.items():
  s=byid.get(c["station"])
  if not s: out[course]={"station_id":c["station"],"error":"station_not_returned"};continue
  w=s.get("WeatherElement") or {}
  gust=w.get("GustInfo") if isinstance(w,dict) else {}
  ot=s.get("ObsTime") or {}
  out[course]={"station_id":c["station"],"station_name":c["station_name"],"distance_km":c["distance_km"],
   "observation_time":(ot.get("DateTime") if isinstance(ot,dict) else str(ot)),
   "wind_speed_ms":obs_value(w.get("WindSpeed"),["WindSpeed"]),
   "wind_direction_deg":obs_value(w.get("WindDirection"),["WindDirection"]),
   "temperature_c":obs_value(w.get("AirTemperature"),["AirTemperature"]),
   "peak_gust_ms":obs_value(gust.get("PeakGustSpeed") if isinstance(gust,dict) else None,["PeakGustSpeed"])}
 return out
def main():
 mode=sys.argv[1] if len(sys.argv)>1 else ""
 generated=stamp()
 if mode=="observation":
  payload={"kind":"cwa_observation","generated_at":generated,"dataset":"O-A0001-001","courses":observation()}
  path="/tmp/cwa-observation.json"
 elif mode=="forecast":
  if not FORECA_KEY or not VC_KEY:raise SystemExit("Forecast API secrets missing")
  result={}
  with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
   futures={}
   for name,c in COURSES.items():
    futures[pool.submit(foreca,c["lat"],c["lon"])]=(name,"Foreca")
    futures[pool.submit(vc,c["lat"],c["lon"])]=(name,"VisualCrossing")
   for f,(name,provider) in futures.items():
    result.setdefault(name,{})[provider]=f.result()
  payload={"kind":"forecast_snapshot","generated_at":generated,"timezone":"Asia/Taipei","units":{"wind":"km/h","gust":"km/h","temperature":"C","direction":"degrees"},"courses":result}
  path="/tmp/forecast-snapshot.json"
 else:raise SystemExit("Usage: collect_validation_data.py forecast|observation")
 with open(path,"w",encoding="utf-8") as f:json.dump(payload,f,ensure_ascii=False,separators=(",",":"))
 print(json.dumps({"kind":payload["kind"],"generated_at":generated,"courses":len(payload["courses"]),"output":path},ensure_ascii=False))
if __name__=="__main__":main()
