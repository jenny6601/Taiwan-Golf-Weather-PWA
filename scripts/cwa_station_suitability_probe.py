#!/usr/bin/env python3
"""Read-only CWA station suitability probe; prints schema metadata, never observation values."""
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
 records=data.get("records",{}); locations=records.get("Station",[]) if isinstance(records,dict) else []
 stations=[]; schema=None
 for s in locations if isinstance(locations,list) else [locations]:
  if not isinstance(s,dict): continue
  we=s.get("WeatherElement")
  if schema is None:
   schema={"station_top_level_keys":list(s.keys()),"weather_element_type":type(we).__name__,"weather_element_keys":list(we.keys())[:40] if isinstance(we,dict) else None,"weather_element_item_types":[type(x).__name__ for x in we[:5]] if isinstance(we,list) else None,"weather_element_nested_keys":{k:list(v.keys())[:30] for k,v in we.items() if isinstance(v,dict)} if isinstance(we,dict) else None}
  geo=s.get("GeoInfo") or {}; coords=geo.get("Coordinates") or []
  lat=lon=None
  if isinstance(coords,dict): coords=[coords]
  for item in coords if isinstance(coords,list) else []:
   if not isinstance(item,dict):continue
   try:
    la=float(item.get("StationLatitude"));lo=float(item.get("StationLongitude"))
    if -90<=la<=90 and -180<=lo<=180:lat,lon=la,lo;break
   except (TypeError,ValueError):pass
  if lat is None or lon is None:
   try:lat=float(geo.get("StationLatitude"));lon=float(geo.get("StationLongitude"))
   except (TypeError,ValueError):continue
  ot=s.get("ObsTime") or {}; obs=(ot.get("DateTime") or ot.get("obsTime")) if isinstance(ot,dict) else str(ot)
  stations.append({"id":s.get("StationId"),"name":s.get("StationName"),"lat":lat,"lon":lon,"time":obs})
 results=[]
 for name,(lat,lon) in COURSES.items():
  near=sorted(stations,key=lambda x:dist(lat,lon,x["lat"],x["lon"]))[:5]
  results.append({"course":name,"nearest_stations":[{"station_id":s["id"],"station_name":s["name"],"distance_km":round(dist(lat,lon,s["lat"],s["lon"]),1),"observation_time":s["time"]} for s in near]})
 print(json.dumps({"test":"CWA station schema probe","generated_at":datetime.now(TZ).isoformat(timespec="seconds"),"station_count":len(stations),"schema_probe":schema,"courses":results},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
