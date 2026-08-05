/**
 * Howlscan trip level toggle + soft starfield
 * Levels: off | mild | full  (localStorage howl_trip + cookie)
 */
(function () {
  const KEY = "howl_trip";
  const script = document.currentScript;
  const fromAttr = (script && script.getAttribute("data-trip")) || "";
  const params = new URLSearchParams(location.search);
  const fromQ = params.get("trip") || "";

  function readLevel() {
    const q = (fromQ || "").toLowerCase();
    if (q === "off" || q === "mild" || q === "full") return q;
    try {
      const ls = (localStorage.getItem(KEY) || "").toLowerCase();
      if (ls === "off" || ls === "mild" || ls === "full") return ls;
    } catch (_) {}
    const a = (fromAttr || "").toLowerCase();
    if (a === "off" || a === "mild" || a === "full") return a;
    return "mild";
  }

  function persist(level) {
    try { localStorage.setItem(KEY, level); } catch (_) {}
    try {
      document.cookie = KEY + "=" + level + "; Path=/; Max-Age=31536000; SameSite=Lax";
    } catch (_) {}
    try {
      fetch("/api/edge/trip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: level }),
      }).catch(function () {});
    } catch (_) {}
  }

  function apply(level) {
    const root = document.documentElement;
    root.classList.remove("howl-trip", "howl-trip-off", "howl-trip-mild", "howl-trip-full");
    if (level === "off") {
      root.classList.add("howl-trip-off");
      var fx = document.querySelector(".howl-trip-fx");
      if (fx) fx.style.display = "none";
      var link = document.querySelector('link[href*="howl-trippy.css"]');
      if (link) link.disabled = true;
    } else {
      root.classList.add("howl-trip", "howl-trip-" + level);
      var fx2 = document.querySelector(".howl-trip-fx");
      if (fx2) fx2.style.display = "";
      var link2 = document.querySelector('link[href*="howl-trippy.css"]');
      if (link2) link2.disabled = false;
      else if (!document.querySelector('link[data-howl-trip]')) {
        var l = document.createElement("link");
        l.rel = "stylesheet";
        l.href = "/assets/howl-trippy.css";
        l.setAttribute("data-howl-trip", level);
        document.head.appendChild(l);
      }
      ensureFx();
      paintStars(level === "full" ? 120 : 55);
    }
    var bar = document.getElementById("howlTripToggle");
    if (bar) {
      bar.querySelectorAll("button").forEach(function (b) {
        b.classList.toggle("on", b.getAttribute("data-level") === level);
      });
    }
  }

  function ensureFx() {
    if (document.querySelector(".howl-trip-fx")) return;
    if (!document.body) return;
    var wrap = document.createElement("div");
    wrap.className = "howl-trip-fx";
    wrap.setAttribute("aria-hidden", "true");
    wrap.innerHTML = '<div class="howl-nebula"></div><canvas class="howl-stars" id="howlStars"></canvas>';
    document.body.insertBefore(wrap, document.body.firstChild);
  }

  function paintStars(n) {
    var c = document.getElementById("howlStars");
    if (!c || !c.getContext) return;
    var reduce = false;
    try {
      reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_) {}
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = window.innerWidth;
    var h = window.innerHeight;
    c.width = w * dpr;
    c.height = h * dpr;
    c.style.width = w + "px";
    c.style.height = h + "px";
    var ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    for (var i = 0; i < n; i++) {
      var x = Math.random() * w;
      var y = Math.random() * h;
      var r = Math.random() * 1.6 + 0.3;
      var a = 0.35 + Math.random() * 0.65;
      ctx.beginPath();
      ctx.fillStyle = Math.random() > 0.7
        ? "rgba(0,240,255," + a + ")"
        : Math.random() > 0.5
          ? "rgba(255,45,149," + a + ")"
          : "rgba(255,255,255," + a + ")";
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    if (!reduce && !c._twinkle) {
      c._twinkle = true;
      var t = 0;
      function tick() {
        t++;
        if (t % 48 === 0) paintStars(n);
        requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    }
  }

  function mountToggle() {
    if (document.getElementById("howlTripToggle")) return;
    var bar = document.createElement("div");
    bar.id = "howlTripToggle";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Trip level");
    ["off", "mild", "full"].forEach(function (lv) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = lv;
      b.setAttribute("data-level", lv);
      b.addEventListener("click", function () {
        persist(lv);
        apply(lv);
      });
      bar.appendChild(b);
    });
    (document.body || document.documentElement).appendChild(bar);
  }

  function boot() {
    mountToggle();
    var level = readLevel();
    persist(level);
    apply(level);
    window.addEventListener("resize", function () {
      if (readLevel() !== "off") paintStars(readLevel() === "full" ? 120 : 55);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
