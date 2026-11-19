#!/usr/bin/env bash
set -euo pipefail
rm -rf dist
mkdir -p dist/assets
python3 -m pip install --quiet Pillow
cp compact/index.html dist/index.html
cp compact/icon-192.png dist/icon-192.png
cp compact/icon-512.png dist/icon-512.png
cp compact/icon.svg dist/icon.svg
cp offline.html dist/offline.html
cp assets/rain-levels-original.webp dist/assets/
python3 - <<'PY'
from pathlib import Path
import json
p=Path("dist/index.html")
s=p.read_text(encoding="utf-8")
s=s.replace('<base href="../"/>','')
s=s.replace('<base href="../">','')
s=s.replace('./compact/manifest.json','./manifest.json')
s=s.replace('./compact/icon-192.png','./icon-192.png')
s=s.replace('./compact/icon-512.png','./icon-512.png')
s=s.replace('./compact/pwa-install.js','./pwa-install.js')
p.write_text(s,encoding="utf-8")

m=json.loads(Path("compact/manifest.json").read_text(encoding="utf-8"))
m["id"]="/"
m["start_url"]="/"
m["scope"]="/"
from PIL import Image
source=Image.open(Path("compact/icon-512.png")).convert("RGBA")
source.resize((192,192),Image.Resampling.LANCZOS).save("dist/icon-192.png",optimize=True)
source.resize((512,512),Image.Resampling.LANCZOS).save("dist/icon-512.png",optimize=True)
m["icons"]=[
 {"src":"/icon-192.png","sizes":"192x192","type":"image/png","purpose":"any"},
 {"src":"/icon-512.png","sizes":"512x512","type":"image/png","purpose":"any"}
]
Path("dist/manifest.json").write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

Path("dist/pwa-install.js").write_text(
 '(()=>{if(!("serviceWorker"in navigator))return;window.addEventListener("load",async()=>{try{const r=await navigator.serviceWorker.register("/sw.js",{scope:"/",updateViaCache:"none"});await r.update();console.log("Compact PWA service worker:",r.scope);}catch(e){console.error("Compact PWA service worker registration failed:",e);}});})();\n',
 encoding="utf-8")
Path("dist/sw.js").write_text(
 'const CACHE_NAME="golf-weather-compact-netlify-v7";\n'
 'const APP_SHELL=["/","/index.html","/manifest.json","/pwa-install.js","/icon.svg","/icon-192.png","/icon-512.png","/offline.html","/assets/rain-levels-original.webp"];\n'
 'self.addEventListener("install",e=>e.waitUntil(caches.open(CACHE_NAME).then(c=>c.addAll(APP_SHELL)).then(()=>self.skipWaiting())));\n'
 'self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith("golf-weather-compact-netlify-")&&k!==CACHE_NAME).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));\n'
 'self.addEventListener("fetch",e=>{const q=e.request;if(q.method!=="GET")return;const u=new URL(q.url);if(u.origin!==self.location.origin)return;if(q.mode==="navigate"){e.respondWith(fetch(q).then(r=>{if(r.ok){const c=r.clone();caches.open(CACHE_NAME).then(x=>x.put(q,c));}return r;}).catch(async()=>await caches.match(q)||await caches.match("/index.html")||await caches.match("/offline.html")||new Response("目前無網路連線，請連線後重新開啟 Golf Weather 精簡版。",{status:503,headers:{"Content-Type":"text/plain; charset=utf-8"}})));return;}if(/\\.(png|jpg|jpeg|svg|webp|woff2?|js|css)$/i.test(u.pathname)){e.respondWith(caches.match(q).then(c=>c||fetch(q).then(r=>{if(r.ok){const z=r.clone();caches.open(CACHE_NAME).then(x=>x.put(q,z));}return r;})));}});\n',
 encoding="utf-8")
PY
