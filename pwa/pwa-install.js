(() => {
 if (!("serviceWorker" in navigator)) return;
 window.addEventListener("load", async () => {
  try {
   // Remove the retired compact-edition service worker and its caches.
   const registrations=await navigator.serviceWorker.getRegistrations();
   await Promise.all(registrations
    .filter(item=>item.scope.includes("/compact/"))
    .map(item=>item.unregister()));
   if ("caches" in window) {
    const keys=await caches.keys();
    await Promise.all(keys
     .filter(key=>key.startsWith("golf-weather-compact-"))
     .map(key=>caches.delete(key)));
   }
   const registration=await navigator.serviceWorker.register("./sw.js",{scope:"./",updateViaCache:"none"});
   await registration.update();
   if(registration.waiting) registration.waiting.postMessage({type:"SKIP_WAITING"});
   console.log("Golf Weather PWA registered and update checked:",registration.scope);
  } catch(error) { console.error("PWA registration failed:",error); }
 });
})();
