/* Register the service worker. Needs https (or localhost) — GitHub Pages
   is https, so this works there and in local testing. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    var root = window.SITE_ROOT || "";
    navigator.serviceWorker.register(root + "sw.js", { scope: root || "./" })
      .catch(function (e) { console.log("SW registration skipped:", e.message); });
  });
}
