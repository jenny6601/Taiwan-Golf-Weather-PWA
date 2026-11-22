#!/usr/bin/env python3
"""Read-only validation-data quality and forecast/observation alignment report."""
import argparse, json, math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TZ=ZoneInfo("Asia/Taipei")
COURSES=["礁溪","林口","桃園","台北","揚昇"]
def dt(s):
 if not s:return None
 try:
  x=datetime.fromisoformat(str(s).replace("Z","+00:00"))
  return x if x.tzinfo else x.replace(tzinfo=TZ)
 except Exception:return None
def num(x):
 try:
  v=float(x)
  return v if math.isfinite(v) else None
 except (TypeError,ValueError):return None
def mae(vals):
 return round(sum(abs(a-b) for a,b in vals)/len(vals),3) if vals else None
def rmse(vals):
 return round(math.sqrt(sum((a-b)**2 for a,b in vals)/len(vals)),3) if vals else None
def circ_err(a,b):
 return abs((a-b+180)%360-180)
def files(p):
 return sorted(p.glob("*.json")) if p.exists() else []
def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--data-dir",default="validation-data")
 ap.add_argument("--output",default="/tmp/validation-quality-report.json")
 a=ap.parse_args();root=Path(a.data_dir)
 obs_files=files(root/"observations");fc_files=files(root/"forecasts")
 observations=[];forecasts=[];issues=[]
 for f in obs_files:
  try:
   d=json.loads(f.read_text(encoding="utf-8")); gen=dt(d.get("generated_at"))
   if d.get("kind")!="cwa_observation" or not gen:issues.append(f"{f.name}: invalid observation envelope");continue
   for course,v in d.get("courses",{}).items():
    ot=dt(v.get("observation_time"))
    if not ot:issues.append(f"{f.name}/{course}: missing observation timestamp");continue
    wind=num(v.get("wind_speed_ms"));gust=num(v.get("peak_gust_ms"));temp=num(v.get("temperature_c"));direction=num(v.get("wind_direction_deg"))
    if wind is not None and wind<0:issues.append(f"{f.name}/{course}: negative wind speed");wind=None
    if gust is not None and gust<0:issues.append(f"{f.name}/{course}: negative gust");gust=None
    observations.append({"file":f.name,"course":course,"generated":gen,"time":ot,"wind":wind*3.6 if wind is not None else None,"gust":gust*3.6 if gust is not None else None,"temp":temp,"direction":direction})
  except Exception as e:issues.append(f"{f.name}: {type(e).__name__}")
 for f in fc_files:
  try:
   d=json.loads(f.read_text(encoding="utf-8"));issued=dt(d.get("generated_at"))
   if d.get("kind")!="forecast_snapshot" or not issued:issues.append(f"{f.name}: invalid forecast envelope");continue
   for course,providers in d.get("courses",{}).items():
    for provider,rows in providers.items():
     if not isinstance(rows,list):issues.append(f"{f.name}/{course}/{provider}: forecast not list");continue
     for row in rows:
      valid=dt(row.get("time"))
      if not valid:continue
      if valid<=issued:continue
      forecasts.append({"file":f.name,"course":course,"provider":provider,"issued":issued,"time":valid,"wind":num(row.get("wind_kmh")),"gust":num(row.get("gust_kmh")),"temp":num(row.get("temp_c")),"direction":num(row.get("wind_dir_deg"))})
  except Exception as e:issues.append(f"{f.name}: {type(e).__name__}")
 # Deduplicate observation records by course + observation time; keep latest snapshot capture.
 od={}
 for o in observations:
  k=(o["course"],o["time"])
  if k not in od or o["generated"]>od[k]["generated"]:od[k]=o
 obs=list(od.values())
 # For each valid time, use the latest forecast issued strictly before the observation's valid time.
 metrics={}
 for course in COURSES:
  metrics[course]={}
  for provider in ["Foreca","VisualCrossing"]:
   pairs=[]
   for o in obs:
    candidates=[f for f in forecasts if f["course"]==course and f["provider"]==provider and f["time"]==o["time"] and f["issued"]<o["time"]]
    if not candidates:continue
    f=max(candidates,key=lambda x:x["issued"])
    if f["wind"] is not None and o["wind"] is not None:pairs.append((f["wind"],o["wind"]))
   metrics[course][provider]={"wind_speed_pairs":len(pairs),"wind_speed_mae_kmh":mae(pairs),"wind_speed_rmse_kmh":rmse(pairs)}
 report={"generated_at":datetime.now(TZ).isoformat(timespec="seconds"),"observation_files":len(obs_files),"forecast_files":len(fc_files),"unique_observation_course_hours":len(obs),"forecast_rows_after_issue_time":len(forecasts),"course_provider_metrics":metrics,"data_quality_issues":issues,"interpretation":"Only exact valid-time matches with forecast issue time strictly before valid time are scored. Gusts and wind direction are retained for later semantic validation; no skill claim should be made from sparse samples."}
 Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"report":a.output,"observation_files":len(obs_files),"forecast_files":len(fc_files),"unique_observation_course_hours":len(obs),"forecast_rows_after_issue_time":len(forecasts),"issues":len(issues),"metrics":metrics},ensure_ascii=False))
if __name__=="__main__":main()
