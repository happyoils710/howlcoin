/* Capacitor shell boot — load live wallet or local fallback */
(function () {
  var status = document.getElementById("status");
  var err = document.getElementById("err");
  var frame = document.getElementById("frame");
  var boot = document.getElementById("boot");
  var btnLocal = document.getElementById("btnLocal");
  var live = (window.HOWL_NATIVE && window.HOWL_NATIVE.liveAppUrl) || "https://howlscan.org/app";
  var local = (window.HOWL_NATIVE && window.HOWL_NATIVE.localAppUrl) || "./assets/public-wallet.html";

  function showFrame(url) {
    status.textContent = "Loading…";
    frame.onload = function () {
      boot.style.display = "none";
      frame.classList.add("on");
      // inject bridge into child when same-origin local; live origin uses postMessage
      tryInject();
    };
    frame.onerror = function () {
      err.textContent = "Failed to load " + url;
      btnLocal.style.display = "inline-block";
    };
    frame.src = url;
  }

  function tryInject() {
    // Live howlscan.org is cross-origin — wallet detects Capacitor via parent flag + bridge script on CDN path.
    // Local bundled wallet is same-origin-ish via file/capacitor — try direct inject.
    try {
      var doc = frame.contentDocument;
      if (!doc) return;
      if (doc.getElementById("howl-native-bridge")) return;
      var s = doc.createElement("script");
      s.id = "howl-native-bridge";
      s.src = "../howl-native-bridge.js";
      (doc.head || doc.documentElement).appendChild(s);
    } catch (e) {
      // cross-origin — wallet must load bridge itself when ?native=1
    }
  }

  btnLocal.onclick = function () {
    err.textContent = "";
    showFrame(local);
  };

  // Prefer live app with native marker so public-wallet enables Capacitor bridge
  var liveNative = live + (live.indexOf("?") >= 0 ? "&" : "?") + "native=1";

  // Quick connectivity probe
  var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  var t = setTimeout(function () {
    if (ctrl) ctrl.abort();
  }, 6000);

  var opts = { method: "GET", cache: "no-store" };
  if (ctrl) opts.signal = ctrl.signal;

  fetch(live, opts)
    .then(function (r) {
      clearTimeout(t);
      if (!r.ok) throw new Error("HTTP " + r.status);
      showFrame(liveNative);
    })
    .catch(function () {
      clearTimeout(t);
      status.textContent = "Offline — using bundled wallet";
      btnLocal.style.display = "inline-block";
      showFrame(local + (local.indexOf("?") >= 0 ? "&" : "?") + "native=1");
    });
})();
