#!/usr/bin/env python3
"""Read-only CWA station suitability probe; reports element availability, not values."""
import json, math, os, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
KEY=(os.getenv("CWA_API_KEY") or "").strip()
TZ=ZoneInfo("Asia/Taipei")
COURSES={"礁溪":(24.8389,121.7389),"林口":(25.0714,121.3719),"桃園":(24.8789,121.2069),"台北":(25.0489,121.2825),"揚昇":(24.9208,121.1878)}
def dist(a,b,c,d):
 r=6371.0;p1,p2=math.radians(a),math.radians(c);dp=math.radians(c-a);dl=math.radians(d-b)
 h=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
 return 2*r*math.asin(math.sqrt(h))
def fetch():
 q=urllib.parse.urlencode({"Authorization":KEY,"format":"JSON"})
 req=urllib.request.Request("https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?"+q,headers={"User-Agent":"Taiwan-Golf-Weather-CWA-Validation/1.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=60) as r:return json.loads(r.read().decode())
def main():
 if not KEY: raise SystemExit("CWA_API_KEY secret missing")
 data=fetch()
 if str(data.get("success","")).lower()!="true": raise RuntimeError("CWA API did not return success")
 locations=data.get("records",{}).get("Station",[])
 stations=[]
 for s in locations if isinstance(locations,list) else [locations]:
  if not isinstance(s,dict):continue
  geo=s.get("GeoInfo") or {}; coords=geo.get("Coordinates") or []
  lat=lon=None
  if isinstance(coords,dict):coords=[coords]
  for c in coords if isinstance(coords,list) else []:
   if not isinstance(c,dict):continue
   try:
    lat=float(c.get("StationLatitude"));lon=float(c.get("StationLongitude"))
    if -90<=lat<=90 and -180<=lon<=180:break
   except (TypeError,ValueError):lat=lon=None
  if lat is None or lon is None:
   try:lat=float(geo.get("StationLatitude"));lon=float(geo.get("StationLongitude"))
   except (TypeError,ValueError):continue
  we=s.get("WeatherElement") or {}
  # O-A0001-001 schema uses direct named dictionaries, not ElementName arrays.
  def present(v):
   if not isinstance(v,dict):return False
   val=v.get("WindSpeed") if "WindSpeed" in v else v.get("AirTemperature")
   return val is not None
  ws=we.get("WindSpeed");wd=we.get("WindDirection");temp=we.get("AirTemperature")
  gust=(we.get("GustInfo") or {}).get("PeakGustSpeed") if isinstance(we.get("GustInfo"),dict) else None
  obs=s.get("ObsTime") or {}; ot=obs.get("DateTime") if isinstance(obs,dict) else str(obs)
  stations.append({"id":s.get("StationId"),"name":s.get("StationName"),"lat":lat,"lon":lon,"time":ot,
   "availability":{"wind_speed":isinstance(ws,dict) and ws.get("WindSpeed") is not None,
   "wind_direction":isinstance(wd,dict) and wd.get("WindDirection") is not None,
   "air_temperature":isinstance(temp,dict) and temp.get("AirTemperature") is not None,
   "peak_gust":isinstance(gust,dict) and gust.get("PeakGustSpeed") is not None}})
 results=[]
 for name,(lat,lon) in COURSES.items():
  near=sorted(stations,key=lambda x:dist(lat,lon,x["lat"],x["lon"]))[:10]
  results.append({"course":name,"nearest_stations":[{"station_id":s["id"],"station_name":s["name"],"distance_km":round(dist(lat,lon,s["lat"],s["lon"]),1),"observation_time":s["time"],"availability":s["availability"]} for s in near]})
 print(json.dumps({"test":"CWA station element availability","generated_at":datetime.now(TZ).isoformat(timespec="seconds"),"dataset":"O-A0001-001","station_count":len(stations),"courses":results},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
