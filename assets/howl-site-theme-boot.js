/* Tiny FOUC boot — apply data-theme before first paint (inline-friendly). */
(function () {
  try {
    var t = localStorage.getItem("howlscan_theme_v1") || localStorage.getItem("howl_theme_v1") || "dark";
    if (["light", "dark", "neo", "bones"].indexOf(t) < 0) t = "dark";
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) {}
})();
