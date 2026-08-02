/**
 * Howl native bridge — runs inside Capacitor shell and/or wallet page.
 * Opens sites in a real system in-app browser (Custom Tabs / SFSafariViewController
 * or Capgo WebView) so news & dapps are not blocked by iframe X-Frame-Options.
 */
(function (global) {
  "use strict";

  var BRIDGE = {
    version: "1.0.0",
    isNative: false,
    platform: "web",
  };

  function hasCapacitor() {
    return !!(global.Capacitor && (global.Capacitor.isNativePlatform
      ? global.Capacitor.isNativePlatform()
      : global.Capacitor.Plugins));
  }

  function plugin(name) {
    try {
      if (global.Capacitor && global.Capacitor.Plugins && global.Capacitor.Plugins[name]) {
        return global.Capacitor.Plugins[name];
      }
    } catch (_) {}
    return null;
  }

  function detect() {
    BRIDGE.isNative = hasCapacitor();
    try {
      BRIDGE.platform = (global.Capacitor && global.Capacitor.getPlatform)
        ? global.Capacitor.getPlatform()
        : (BRIDGE.isNative ? "native" : "web");
    } catch (_) {
      BRIDGE.platform = BRIDGE.isNative ? "native" : "web";
    }
    // Shell iframe parent
    if (!BRIDGE.isNative && global.parent && global.parent !== global) {
      try {
        if (global.parent.HOWL_NATIVE || global.parent.Capacitor) {
          BRIDGE.isNative = true;
          BRIDGE.platform = "capacitor-shell";
        }
      } catch (_) {}
    }
    // Query flag from shell boot
    try {
      if (/[?&]native=1(?:&|$)/.test(global.location.search || "")) {
        BRIDGE.isNative = true;
      }
    } catch (_) {}
    global.HOWL_NATIVE_BRIDGE = BRIDGE;
    return BRIDGE;
  }

  /**
   * Open URL in native in-app browser (full engine — not an iframe).
   * Prefer Capgo InAppBrowser WebView (toolbar + stays in app),
   * fall back to @capacitor/browser (Custom Tabs / Safari View).
   */
  async function openUrl(url, opts) {
    opts = opts || {};
    if (!url || !/^https?:\/\//i.test(url)) {
      return { ok: false, reason: "bad-url" };
    }
    detect();

    // Capgo InAppBrowser — real WebView with nav bar
    var IAB = plugin("InAppBrowser");
    if (IAB && typeof IAB.openWebView === "function") {
      try {
        await IAB.openWebView({
          url: url,
          title: opts.title || "Howl",
          isPresentAfterPageLoad: false,
          isAnimated: true,
          showReloadButton: true,
          visibleTitle: true,
          toolbarColor: "#03010a",
          backgroundColor: "black",
        });
        return { ok: true, via: "inappbrowser" };
      } catch (e) {
        console.warn("InAppBrowser failed", e);
      }
    }
    if (IAB && typeof IAB.open === "function") {
      try {
        await IAB.open({ url: url, isPresentAfterPageLoad: true });
        return { ok: true, via: "inappbrowser-open" };
      } catch (e) {
        console.warn("InAppBrowser.open failed", e);
      }
    }

    // Official Capacitor Browser (Chrome Custom Tabs / SFSafariViewController)
    var Browser = plugin("Browser");
    if (Browser && typeof Browser.open === "function") {
      try {
        await Browser.open({
          url: url,
          presentationStyle: "fullscreen",
          toolbarColor: "#03010a",
        });
        return { ok: true, via: "capacitor-browser" };
      } catch (e) {
        console.warn("Browser.open failed", e);
      }
    }

    // Ask parent shell (wallet in iframe of index.html)
    if (global.parent && global.parent !== global) {
      try {
        global.parent.postMessage({ type: "howl-open-url", url: url, title: opts.title || "" }, "*");
        return { ok: true, via: "postMessage" };
      } catch (_) {}
    }

    return { ok: false, reason: "no-plugin" };
  }

  async function closeBrowser() {
    var IAB = plugin("InAppBrowser");
    if (IAB && IAB.close) {
      try { await IAB.close(); } catch (_) {}
    }
    var Browser = plugin("Browser");
    if (Browser && Browser.close) {
      try { await Browser.close(); } catch (_) {}
    }
  }

  // Parent shell listener
  if (typeof global.addEventListener === "function") {
    global.addEventListener("message", function (ev) {
      var d = ev && ev.data;
      if (!d || d.type !== "howl-open-url") return;
      openUrl(d.url, { title: d.title });
    });
  }

  BRIDGE.openUrl = openUrl;
  BRIDGE.closeBrowser = closeBrowser;
  BRIDGE.detect = detect;
  detect();

  // Expose helpers used by public-wallet.html
  global.howlNativeOpen = openUrl;
  global.howlNativeClose = closeBrowser;
  global.howlIsNative = function () {
    return detect().isNative;
  };
})(typeof window !== "undefined" ? window : globalThis);
