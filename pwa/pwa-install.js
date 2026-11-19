(() => {
 if (!("serviceWorker" in navigator)) return;
 window.addEventListener("load", async () => {
  try {
   const registration=await navigator.serviceWorker.register("./sw.js",{scope:"./",updateViaCache:"none"});
   await registration.update();
   if(registration.waiting) registration.waiting.postMessage({type:"SKIP_WAITING"});
   console.log("Golf Weather PWA registered and update checked:",registration.scope);
  } catch(error) { console.error("PWA registration failed:",error); }
 });
})();
