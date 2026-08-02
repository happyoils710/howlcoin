/**
 * Howlscan site-wide theme — persists across explorer, whitepaper, token, wallet guide, /app.
 * Keys: howlscan_theme_v1 (site) + howl_theme_v1 (wallet) kept in sync.
 */
(function (global) {
  "use strict";

  var KEY_SITE = "howlscan_theme_v1";
  var KEY_WALLET = "howl_theme_v1";
  var THEMES = ["light", "dark", "neo", "bones"];
  var COLORS = {
    light: "#f4f6fa",
    dark: "#0c0f14",
    neo: "#03010a",
    bones: "#000000",
  };

  function normalize(name) {
    return THEMES.indexOf(name) >= 0 ? name : "dark";
  }

  function readStored() {
    try {
      var a = localStorage.getItem(KEY_SITE);
      var b = localStorage.getItem(KEY_WALLET);
      if (a && THEMES.indexOf(a) >= 0) return a;
      if (b && THEMES.indexOf(b) >= 0) return b;
    } catch (e) {}
    return "dark";
  }

  function writeStored(t) {
    try {
      localStorage.setItem(KEY_SITE, t);
      localStorage.setItem(KEY_WALLET, t);
    } catch (e) {}
  }

  function setTheme(name) {
    var t = normalize(name);
    try {
      document.documentElement.setAttribute("data-theme", t);
    } catch (e) {}
    writeStored(t);

    var meta = document.getElementById("themeColorMeta");
    if (meta) meta.content = COLORS[t] || COLORS.dark;

    document.querySelectorAll("#themeSelect, .howl-theme-select, select[data-howl-theme]").forEach(function (sel) {
      if (sel && sel.value !== t) sel.value = t;
    });
    document.querySelectorAll(".theme-pill, .howl-theme-pill").forEach(function (p) {
      var pt = p.getAttribute("data-theme");
      if (pt) p.classList.toggle("on", pt === t);
    });
    return t;
  }

  function applyStoredTheme() {
    return setTheme(readStored());
  }

  // Re-apply after SPA hash navigations, bfcache restore, and tab focus
  function wirePersistence() {
    if (wirePersistence._done) return;
    wirePersistence._done = true;
    global.addEventListener("hashchange", applyStoredTheme);
    global.addEventListener("pageshow", applyStoredTheme);
    global.addEventListener("storage", function (ev) {
      if (ev && (ev.key === KEY_SITE || ev.key === KEY_WALLET)) applyStoredTheme();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") applyStoredTheme();
    });
  }

  // Apply immediately if DOM already has documentElement
  try {
    applyStoredTheme();
  } catch (e) {}
  wirePersistence();

  global.HowlTheme = {
    THEMES: THEMES,
    KEY_SITE: KEY_SITE,
    KEY_WALLET: KEY_WALLET,
    get: readStored,
    set: setTheme,
    apply: applyStoredTheme,
  };
  // Aliases used by explorer inline handlers
  global.setTheme = setTheme;
  global.applyStoredTheme = applyStoredTheme;
})(typeof window !== "undefined" ? window : this);
