#!/usr/bin/env python3
"""Read-only CWA station suitability probe for golf-course forecast verification.
Fetches current hourly station observations and reports nearest stations and available fields.
No raw observations or API credentials are persisted.
"""
import json, math, os, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

KEY=(os.getenv("CWA_API_KEY") or os.getenv("CWA_AUTHORIZATION") or "").strip()
TZ=ZoneInfo("Asia/Taipei")
COURSES={
 "礁溪":(24.8389,121.7389),
 "林口":(25.0714,121.3719),
 "桃園":(24.8789,121.2069),
 "台北":(25.0489,121.2825),
 "揚昇":(24.9208,121.1878),
}
def distance_km(a,b,c,d):
 r=6371.0
 p1,p2=math.radians(a),math.radians(c)
 dp=math.radians(c-a); dl=math.radians(d-b)
 h=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
 return 2*r*math.asin(math.sqrt(h))
def fetch(dataid):
 q=urllib.parse.urlencode({"Authorization":KEY,"format":"JSON"})
 url=f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{dataid}?{q}"
 req=urllib.request.Request(url,headers={"User-Agent":"Taiwan-Golf-Weather-CWA-Validation/1.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=60) as r: return json.loads(r.read().decode("utf-8"))
def main():
 if not KEY: raise SystemExit("CWA API secret missing: expected CWA_API_KEY or CWA_AUTHORIZATION")
 started=datetime.now(TZ).isoformat(timespec="seconds")
 data=fetch("O-A0001-001")
 if str(data.get("success","")).lower()!="true": raise RuntimeError("CWA hourly observation API did not return success")
 locations=data.get("records",{}).get("Station",[])
 stations=[]
 for s in locations:
  geo=s.get("GeoInfo",{})
  c=geo.get("Coordinates",[])
  lat=lon=None
  for item in c:
   if item.get("CoordinateName") in ("WGS84","WGS 84") or item.get("CoordinateFormat")=="decimal degrees":
    try: lat=float(item.get("StationLatitude")); lon=float(item.get("StationLongitude"))
    except (TypeError,ValueError): pass
  if lat is None or lon is None:
   try: lat=float(geo.get("StationLatitude")); lon=float(geo.get("StationLongitude"))
   except (TypeError,ValueError): continue
  we={x.get("ElementName"):x.get("ElementValue",[]) for x in s.get("WeatherElement",[])}
  obs=s.get("ObsTime",{}).get("DateTime") or s.get("ObsTime",{}).get("obsTime")
  stations.append({"id":s.get("StationId"),"name":s.get("StationName"),"lat":lat,"lon":lon,"time":obs,"elements":list(we.keys())})
 results=[]
 for name,(lat,lon) in COURSES.items():
  near=sorted(stations,key=lambda x:distance_km(lat,lon,x["lat"],x["lon"]))[:5]
  results.append({"course":name,"nearest_stations":[{"station_id":s["id"],"station_name":s["name"],"distance_km":round(distance_km(lat,lon,s["lat"],s["lon"]),1),"observation_time":s["time"],"available_elements":s["elements"]} for s in near]})
 report={"test":"CWA station suitability probe","generated_at":datetime.now(TZ).isoformat(timespec="seconds"),"started_at":started,"dataset":"O-A0001-001","station_count":len(stations),"courses":results,"raw_observations_saved":False}
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
