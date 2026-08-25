// Home page lightweight tech animations.
// Loaded only on the marketing homepage, never in admin shell.
(function () {
  "use strict";

  var REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (REDUCED_MOTION) return;

  // ── Entrance via IntersectionObserver ──────────────────────────────
  var entries = document.querySelectorAll(
    ".home-section-preview, .home-hero-content, .preview-mini-panel"
  );
  if (!entries.length) return;

  var staggerDelay = 60; // ms between each child item
  var animDuration = 400; // ms

  function animateIn(el, delay) {
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    el.style.transition =
      "opacity " + animDuration + "ms ease, transform " + animDuration + "ms ease";
    el.style.transitionDelay = delay + "ms";
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        el.style.opacity = "";
        el.style.transform = "";
      });
    });
  }

  function stagger(container, selector) {
    var children = container.querySelectorAll(selector);
    children.forEach(function (child, i) {
      animateIn(child, i * staggerDelay);
    });
  }

  entries.forEach(function (entry) {
    var observer = new IntersectionObserver(
      function (records) {
        records.forEach(function (record) {
          if (!record.isIntersecting) return;
          // Stagger direct children that are visible elements
          stagger(record.target, ":scope > *");
          // Scan line on preview panels
          addScanLine(record.target);
          // Stat dot pulse
          activateDot(record.target);
          observer.unobserve(record.target);
        });
      },
      { threshold: 0.2 }
    );
    observer.observe(entry);
  });

  // ── Scan line ────────────────────────────────────────────────────
  function addScanLine(container) {
    var panels = container.querySelectorAll(".preview-mini-panel");
    panels.forEach(function (panel) {
      if (panel.dataset.scanAdded) return;
      panel.dataset.scanAdded = "1";
      var line = document.createElement("div");
      line.className = "home-scan-line";
      line.setAttribute("aria-hidden", "true");
      panel.style.position = "relative";
      panel.style.overflow = "hidden";
      panel.appendChild(line);
      // Trigger once
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          line.classList.add("is-active");
        });
      });
    });
  }

  // ── Live dot pulse ───────────────────────────────────────────────
  function activateDot(container) {
    var dot = container.querySelector(".stat-dot");
    if (dot && !dot.dataset.pulseBound) {
      dot.dataset.pulseBound = "1";
      dot.classList.add("is-live");
    }
  }

  // ── Progress bars (scaleX, not width) ───────────────────────────
  var progressFills = document.querySelectorAll(
    ".traffic-bar-fill, .user-row-bar-fill, .preview-mini-bar-fill"
  );
  progressFills.forEach(function (fill) {
    var target = parseFloat(fill.dataset.target || fill.style.width || "0");
    if (!target) return;
    fill.style.transformOrigin = "left center";
    fill.style.transform = "scaleX(0)";
    fill.classList.add("animate-progress");
    // Scale from 0 to target on first observation
    var progObserver = new IntersectionObserver(
      function (records) {
        records.forEach(function (record) {
          if (!record.isIntersecting) return;
          requestAnimationFrame(function () {
            fill.style.transform = "scaleX(" + target / 100 + ")";
          });
          progObserver.unobserve(fill);
        });
      },
      { threshold: 0.1 }
    );
    progObserver.observe(fill);
  });

  // ── Number counter animation ─────────────────────────────────────
  var countEls = document.querySelectorAll("[data-count-target]");
  countEls.forEach(function (el) {
    var target = parseFloat(el.dataset.countTarget || el.textContent || "0");
    var decimals = parseInt(el.dataset.countDecimals || "0", 10);
    var suffix = el.dataset.countSuffix || "";
    var duration = 850;
    var start = null;

    var counterObserver = new IntersectionObserver(
      function (records) {
        records.forEach(function (record) {
          if (!record.isIntersecting) return;
          counterObserver.unobserve(el);
          requestAnimationFrame(function (ts) {
            start = ts;
            function step(now) {
              var elapsed = now - start;
              var progress = Math.min(elapsed / duration, 1);
              // Ease-out cubic
              var t = 1 - Math.pow(1 - progress, 3);
              var current = target * t;
              el.textContent = current.toFixed(decimals) + suffix;
              if (progress < 1) requestAnimationFrame(step);
            }
            requestAnimationFrame(step);
          });
        });
      },
      { threshold: 0.1 }
    );
    counterObserver.observe(el);
  });

  // ── Grid drift (very slow) ───────────────────────────────────────
  var grid = document.querySelector(".home-hero-grid");
  if (grid) {
    grid.classList.add("is-drifting");
  }
})();
