#!/usr/bin/env python3
"""Daily 16-day cross-provider forecast consistency check for five pilot golf courses."""
import json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ=ZoneInfo("Asia/Taipei")
COURSES={"礁溪":"9f35112bceaa34e7","東方":"07d249bc77494442","美麗華":"54a4a424eb9351c2","林口":"9194898a8728899b","大溪":"08fb9cffedd9df7d"}
CACHE_BASE="https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/"
OUT=sys.argv[1] if len(sys.argv)>1 else "forecast-crosscheck.json"
ACCU=os.getenv("ACCUWEATHER_API_KEY","").strip()
Q_TOKEN=os.getenv("QWEATHER_API_TOKEN","").strip()
Q_HOST=os.getenv("QWEATHER_API_HOST","").strip().replace("https://","").rstrip("/")

def get_json(url, headers=None):
    req=urllib.request.Request(url,headers={"User-Agent":"Taiwan-Golf-Weather-PWA-Crosscheck/1.0",**(headers or {})})
    with urllib.request.urlopen(req,timeout=35) as r: return json.load(r)

def fetch_accuweather(lat,lon):
    if not ACCU: raise RuntimeError("Missing ACCUWEATHER_API_KEY secret")
    q=urllib.parse.urlencode({"apikey":ACCU,"q":f"{lat},{lon}","language":"zh-tw","details":"true"})
    loc=get_json("https://dataservice.accuweather.com/locations/v1/cities/geoposition/search?"+q)
    key=loc["Key"]
    q=urllib.parse.urlencode({"apikey":ACCU,"language":"zh-tw","details":"true","metric":"true"})
    data=get_json(f"https://dataservice.accuweather.com/forecasts/v1/daily/15day/{key}?"+q)
    out={}
    for d in data.get("DailyForecasts",[]):
        date=d.get("Date","")[:10]; day=d.get("Day",{}); night=d.get("Night",{})
        probs=[x.get("PrecipitationProbability") for x in (day,night) if isinstance(x.get("PrecipitationProbability"),(int,float))]
        amounts=[x.get("Rain",{}).get("Value") for x in (day,night) if isinstance(x.get("Rain"),dict) and isinstance(x["Rain"].get("Value"),(int,float))]
        out[date]={"rain_probability_max":max(probs) if probs else None,"rain_mm_day_night_sum":sum(amounts) if amounts else None,
                   "temp_min":d.get("Temperature",{}).get("Minimum",{}).get("Value"),"temp_max":d.get("Temperature",{}).get("Maximum",{}).get("Value"),
                   "condition_day":day.get("IconPhrase"),"condition_night":night.get("IconPhrase")}
    return out

def fetch_qweather(lat,lon):
    if not Q_TOKEN or not Q_HOST: raise RuntimeError("Missing QWEATHER_API_TOKEN or QWEATHER_API_HOST secret")
    # City-based 30-day endpoint is required for a 16-day comparison. QWeather documents v7 as deprecated but still available during this pilot.
    q=urllib.parse.urlencode({"location":f"{lon:.2f},{lat:.2f}","lang":"zh"})
    data=get_json(f"https://{Q_HOST}/v7/weather/30d?{q}",{"Authorization":f"Bearer {Q_TOKEN}"})
    if data.get("code")!="200": raise RuntimeError(f"QWeather API code {data.get('code')}")
    out={}
    for d in data.get("daily",[]):
        out[d["fxDate"]]={"rain_mm_day":float(d["precip"]) if d.get("precip") not in (None,"") else None,
            "temp_min":float(d["tempMin"]) if d.get("tempMin") not in (None,"") else None,
            "temp_max":float(d["tempMax"]) if d.get("tempMax") not in (None,"") else None,
            "condition_day":d.get("textDay"),"condition_night":d.get("textNight"),
            "wind_speed_day_kmh":float(d["windSpeedDay"]) if d.get("windSpeedDay") not in (None,"") else None}
    return out

def summarize_models(entry,date):
    day=entry.get("dates",{}).get(date,{})
    models=day.get("modelRows",{})
    summaries=[]
    for name,rows in models.items():
        rows=[r for r in rows if r.get("available") and r.get("time") in {f"{h:02d}:00" for h in range(6,19)}]
        if not rows: continue
        vals=[r.get("rain") for r in rows if isinstance(r.get("rain"),(int,float))]
        mm=[r.get("mm") for r in rows if isinstance(r.get("mm"),(int,float))]
        temps=[r.get("temp") for r in rows if isinstance(r.get("temp"),(int,float))]
        gust=[r.get("gust") for r in rows if isinstance(r.get("gust"),(int,float))]
        summaries.append({"model":name,"rain_probability_max":max(vals) if vals else None,
          "rain_mm_06_18_sum":round(sum(mm),1) if mm else None,
          "temp_min_06_18":min(temps) if temps else None,"temp_max_06_18":max(temps) if temps else None,
          "gust_max_kmh":max(gust) if gust else None})
    def stats(key):
        v=[x[key] for x in summaries if isinstance(x.get(key),(int,float))]
        return {"min":min(v),"max":max(v),"mean":round(sum(v)/len(v),1)} if v else None
    rain_probs=[x["rain_probability_max"] for x in summaries if x["rain_probability_max"] is not None]
    rain_amounts=[x["rain_mm_06_18_sum"] for x in summaries if x["rain_mm_06_18_sum"] is not None]
    spread=(max(rain_probs)-min(rain_probs)) if len(rain_probs)>=2 else None
    return {"model_count":len(summaries),"models":summaries,"rain_probability":stats("rain_probability_max"),
      "rain_mm_06_18":stats("rain_mm_06_18_sum"),"temperature_min":stats("temp_min_06_18"),
      "temperature_max":stats("temp_max_06_18"),"gust_max":stats("gust_max_kmh"),
      "rain_probability_spread_pp":spread}

def band(spread, model_count):
    if model_count<3 or spread is None: return "資料不足"
    if spread<=20: return "高度一致"
    if spread<=40: return "大致一致"
    if spread<=60: return "分歧"
    return "高度分歧"

def main():
    now=datetime.now(TZ); dates=[(now.date()+timedelta(days=i)).isoformat() for i in range(16)]
    report={"schema_version":1,"generated_at":now.isoformat(),"timezone":"Asia/Taipei",
      "scope":"pilot-5-courses","dates":dates,"sources":["ECMWF IFS","NOAA GFS","DWD ICON","JMA GSM","Canada GEM","AccuWeather","QWeather"],"courses":{}}
    errors=[]
    for name,key in COURSES.items():
        try:
            payload=get_json(CACHE_BASE+f"courses/{key}.json")
            entry=payload["courses"][name]; lat,lon=entry["lat"],entry["lon"]
            providers={}
            for provider,fn in (("AccuWeather",fetch_accuweather),("QWeather",fetch_qweather)):
                try: providers[provider]={"status":"ok","daily":fn(lat,lon)}
                except Exception as e: providers[provider]={"status":"error","error":str(e)}
            course={"lat":lat,"lon":lon,"providers":providers,"daily":{}}
            for date in dates:
                models=summarize_models(entry,date)
                external={}
                for provider,p in providers.items():
                    external[provider]=p["daily"].get(date) if p["status"]=="ok" else None
                course["daily"][date]={"pwa_models":models,"external":external,
                    "pwa_consensus":band(models["rain_probability_spread_pp"],models["model_count"])}
            report["courses"][name]=course
        except Exception as e: errors.append(f"{name}: {e}")
    report["errors"]=errors
    with open(OUT,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({"courses":len(report["courses"]),"dates":len(dates),"errors":errors,"output":OUT},ensure_ascii=False))
    if len(report["courses"])!=5: raise SystemExit("Pilot report incomplete; refusing success.")
if __name__=="__main__": main()
