// Home page lightweight tech animations.
// Loaded only on the marketing homepage, never in admin shell.
(function () {
  "use strict";

  var REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // The entrance effect hides elements and reveals them again on
  // intersection. If we cannot observe, we must not hide: bail out and let
  // the server-rendered markup stand as-is. (The pre-paint bootstrap makes
  // the same check before adding .home-prepaint, so nothing stays hidden.)
  if (REDUCED_MOTION || typeof IntersectionObserver !== "function") return;

  // The pre-paint bootstrap's failsafe is no longer needed once this file
  // runs: from here on, the inline prime state below is the source of truth.
  if (window.__homePrepaintFailsafe) {
    window.clearTimeout(window.__homePrepaintFailsafe);
    window.__homePrepaintFailsafe = null;
  }

  var STAGGER_MS = 60; // between sibling items
  var ENTER_MS = 400;
  var EASE = "cubic-bezier(.16,1,.3,1)";

  function each(list, fn) {
    Array.prototype.forEach.call(list || [], fn);
  }

  // One observer per group instead of one per element.
  function observeOnce(elements, threshold, onEnter) {
    if (!elements || !elements.length) return;
    var observer = new IntersectionObserver(
      function (records) {
        records.forEach(function (record) {
          if (!record.isIntersecting) return;
          observer.unobserve(record.target);
          onEnter(record.target);
        });
      },
      { threshold: threshold }
    );
    each(elements, function (el) {
      observer.observe(el);
    });
  }

  // ── Entrance ──────────────────────────────────────────────────────
  // The "from" state is written here, at script-execution time. This file is
  // loaded with `defer`, so that still lands before the first paint. Writing
  // it inside the observer callback (the previous behaviour) ran after paint,
  // so every card flashed visible, disappeared, then faded in.
  //
  // `.preview-mini-panel` is intentionally not observed on its own: it is a
  // descendant of `.home-section-preview` and used to be animated twice,
  // with two competing sets of inline transitions.
  var sections = document.querySelectorAll(
    ".home-section-preview, .home-hero-content"
  );

  function childrenOf(section) {
    return section.querySelectorAll(":scope > *");
  }

  function prime(el, delay) {
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    el.style.transition =
      "opacity " + ENTER_MS + "ms " + EASE + ", transform " + ENTER_MS + "ms " + EASE;
    el.style.transitionDelay = delay + "ms";
    el.style.willChange = "opacity, transform";
  }

  function reveal(el, delay) {
    el.style.opacity = "";
    el.style.transform = "";
    // Drop the compositor hints once the transition can no longer be running.
    window.setTimeout(function () {
      el.style.transition = "";
      el.style.transitionDelay = "";
      el.style.willChange = "";
    }, ENTER_MS + delay + 80);
  }

  each(sections, function (section) {
    var kids = childrenOf(section);
    if (kids.length) {
      each(kids, function (kid, i) {
        prime(kid, i * STAGGER_MS);
      });
    } else {
      prime(section, 0);
    }
  });

  observeOnce(sections, 0.2, function (section) {
    var kids = childrenOf(section);
    if (kids.length) {
      each(kids, function (kid, i) {
        reveal(kid, i * STAGGER_MS);
      });
    } else {
      reveal(section, 0);
    }
    addScanLine(section);
    activateDot(section);
  });

  // ── Scan line ────────────────────────────────────────────────────
  // A single sweep across the panel. The previous version was a 2px line
  // animated to translateX(calc(100vw + 100%)), so inside a ~500px panel it
  // left the visible area almost immediately and then travelled off-screen
  // for the rest of the duration. It also forced overflow:hidden onto the
  // panel itself, which clipped focus rings; the clipping now lives on a
  // throwaway layer that is removed when the sweep ends.
  function addScanLine(container) {
    each(container.querySelectorAll(".preview-mini-panel"), function (panel) {
      if (panel.dataset.scanAdded) return;
      panel.dataset.scanAdded = "1";
      if (window.getComputedStyle(panel).position === "static") {
        panel.style.position = "relative";
      }
      var clip = document.createElement("div");
      clip.setAttribute("aria-hidden", "true");
      clip.style.cssText =
        "position:absolute;inset:0;overflow:hidden;pointer-events:none;" +
        "z-index:2;border-radius:inherit";
      var line = document.createElement("div");
      line.className = "home-scan-line";
      // Inline values override the stylesheet's width:2px / opacity:0.
      line.style.cssText =
        "position:absolute;top:0;left:0;width:100%;height:100%;opacity:1;" +
        "transform:translateX(-100%);will-change:transform;" +
        "background:linear-gradient(90deg,transparent 0%," +
        "rgba(44,110,127,.08) 40%,rgba(44,110,127,.38) 50%," +
        "rgba(44,110,127,.08) 60%,transparent 100%)";
      clip.appendChild(line);
      panel.appendChild(clip);
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          line.style.transition = "transform 1100ms cubic-bezier(.4,0,.2,1)";
          line.style.transform = "translateX(100%)";
        });
      });
      window.setTimeout(function () {
        if (clip.parentNode) clip.parentNode.removeChild(clip);
      }, 1600);
    });
  }

  // ── Live dot pulse ───────────────────────────────────────────────
  // `.stat-dot.is-live::before` is absolutely positioned with inset:-2px.
  // `.stat-dot` itself declared no position, so the halo resolved against
  // the nearest positioned ancestor — the panel that addScanLine() marks
  // position:relative — and painted a pulsing green wash across the whole
  // card instead of a 12px ring. Give every dot an explicit containing block.
  each(document.querySelectorAll(".stat-dot"), function (dot) {
    if (window.getComputedStyle(dot).position === "static") {
      dot.style.position = "relative";
    }
  });

  function activateDot(container) {
    var dot = container.querySelector(".stat-dot");
    if (!dot || dot.dataset.pulseBound) return;
    dot.dataset.pulseBound = "1";
    if (window.getComputedStyle(dot).position === "static") {
      dot.style.position = "relative";
    }
    dot.classList.add("is-live");
  }

  // ── Progress bars ────────────────────────────────────────────────
  // The markup already carries the real value as style="width:X%", which is
  // what a no-JS or reduced-motion visitor sees. The animation therefore has
  // to end at scaleX(1); scaling to target/100 squared it (62% -> 38.4%).
  var fills = document.querySelectorAll(
    ".traffic-bar-fill, .user-row-bar-fill, .preview-mini-bar-fill"
  );
  each(fills, function (fill) {
    fill.style.transformOrigin = "left center";
    fill.style.transform = "scaleX(0)";
    fill.style.willChange = "transform";
  });

  // All entrance elements now carry their inline prime state, which matches
  // the CSS pre-paint state exactly — removing the class changes nothing
  // visually. The observer reveals below take it from here.
  document.documentElement.classList.remove("home-prepaint");
  observeOnce(fills, 0.1, function (fill) {
    fill.classList.add("animate-progress");
    requestAnimationFrame(function () {
      fill.style.transform = "scaleX(1)";
      window.setTimeout(function () {
        // scaleX(1) and no transform are visually identical; drop both the
        // transform and the hint so the bar stops holding a layer.
        fill.style.transform = "";
        fill.style.willChange = "";
      }, 1100);
    });
  });

  // ── Number counters ──────────────────────────────────────────────
  observeOnce(document.querySelectorAll("[data-count-target]"), 0.1, function (el) {
    var target = parseFloat(el.dataset.countTarget);
    if (!isFinite(target)) return;
    var decimals = parseInt(el.dataset.countDecimals || "0", 10);
    if (!isFinite(decimals) || decimals < 0) decimals = 0;
    var suffix = el.dataset.countSuffix || "";
    var duration = 850;
    var start = null;
    function step(now) {
      if (start === null) start = now;
      var progress = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      if (progress < 1) {
        el.textContent = (target * eased).toFixed(decimals) + suffix;
        requestAnimationFrame(step);
      } else {
        // Land exactly on the server-rendered value.
        el.textContent = target.toFixed(decimals) + suffix;
      }
    }
    requestAnimationFrame(step);
  });

  // ── Hero grid drift ──────────────────────────────────────────────
  // The lattice period lives in one place: the --home-grid-size custom
  // property in the stylesheet, consumed by both background-size and the
  // grid-drift keyframes. No runtime @keyframes injection anymore.
  var grid = document.querySelector(".home-hero-grid");
  if (grid) {
    grid.classList.add("is-drifting");
  }
})();
