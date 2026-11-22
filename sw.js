const CACHE_NAME = "golf-weather-v32-4-pwa-v69";
const APP_SHELL = ["./index.html","./offline.html","./manifest.json","./pwa/pwa-install.js","./icons/icon-192.png","./icons/icon-512.png","./icons/apple-touch-icon.png","./icons/icon-maskable-512.png"];
self.addEventListener("install", event => event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(APP_SHELL)).then(()=>self.skipWaiting())));
self.addEventListener("activate", event => event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith("golf-weather-v32-4-pwa-")&&key!==CACHE_NAME).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener("fetch", event => {
 const request=event.request;
 if(request.method!=="GET") return;
 const url=new URL(request.url);
 if(url.origin!==self.location.origin) return;
 if(request.mode==="navigate"){
  event.respondWith(fetch(request,{cache:"no-cache"}).then(response=>{
   if(response.ok){const copy=response.clone();caches.open(CACHE_NAME).then(cache=>cache.put("./index.html",copy));}
   return response;
  }).catch(async()=>{
   return (await caches.match(request))||(await caches.match("./index.html"))||(await caches.match("./offline.html"))||new Response("目前無網路連線，請連線後重新開啟 Golf Weather V32.2。",{status:503,headers:{"Content-Type":"text/plain; charset=utf-8"}});
  }));
  return;
 }
 if(/\.(png|jpg|jpeg|svg|webp|woff2?|js|css)$/i.test(url.pathname)){
  event.respondWith(caches.match(request).then(cached=>cached||fetch(request).then(response=>{
   if(response.ok){const copy=response.clone();caches.open(CACHE_NAME).then(cache=>cache.put(request,copy));}
   return response;
  })));
 }
});
