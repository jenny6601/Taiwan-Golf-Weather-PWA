#!/usr/bin/env python3
"""Read-only CWA station suitability probe. Outputs station metadata only."""
import json, math, os, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

KEY=(os.getenv("CWA_API_KEY") or os.getenv("CWA_AUTHORIZATION") or "").strip()
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
def as_list(v):
 if isinstance(v,list): return v
 if isinstance(v,dict): return [v]
 return []
def main():
 if not KEY: raise SystemExit("CWA_API_KEY secret missing")
 data=fetch()
 if str(data.get("success","")).lower()!="true": raise RuntimeError("CWA API did not return success: "+str(data.get("result",{}))[:300])
 records=data.get("records",{})
 locations=records.get("Station",[]) if isinstance(records,dict) else []
 stations=[]
 for s in as_list(locations):
  if not isinstance(s,dict):continue
  geo=s.get("GeoInfo") or {}; coords=geo.get("Coordinates") or []
  lat=lon=None
  for item in as_list(coords):
   if not isinstance(item,dict):continue
   try:
    la=float(item.get("StationLatitude"));lo=float(item.get("StationLongitude"))
    if -90<=la<=90 and -180<=lo<=180:lat,lon=la,lo;break
   except (TypeError,ValueError):pass
  if lat is None or lon is None:
   try:lat=float(geo.get("StationLatitude"));lon=float(geo.get("StationLongitude"))
   except (TypeError,ValueError):continue
  we=s.get("WeatherElement") or {}
  if isinstance(we,dict):
   elems=we.get("WeatherElement") or we.get("weatherElement") or we.get("element") or we
   elems=as_list(elems)
  else: elems=as_list(we)
  names=[]
  for e in elems:
   if isinstance(e,dict):
    n=e.get("ElementName") or e.get("elementName") or e.get("element")
    if n:names.append(str(n))
  ot=s.get("ObsTime") or {}
  if isinstance(ot,dict): obs=ot.get("DateTime") or ot.get("obsTime") or ot.get("datetime")
  else:obs=str(ot)
  stations.append({"id":s.get("StationId"),"name":s.get("StationName"),"lat":lat,"lon":lon,"time":obs,"elements":sorted(set(names))})
 results=[]
 for name,(lat,lon) in COURSES.items():
  near=sorted(stations,key=lambda x:dist(lat,lon,x["lat"],x["lon"]))[:5]
  results.append({"course":name,"nearest_stations":[{"station_id":s["id"],"station_name":s["name"],"distance_km":round(dist(lat,lon,s["lat"],s["lon"]),1),"observation_time":s["time"],"available_elements":s["elements"]} for s in near]})
 print(json.dumps({"test":"CWA station suitability probe","generated_at":datetime.now(TZ).isoformat(timespec="seconds"),"dataset":"O-A0001-001","station_count":len(stations),"courses":results,"raw_observations_saved":False},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
