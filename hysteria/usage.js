// /admin/usage and /admin/user/<uid> client glue.
// Two responsibilities:
//   (1) hover tooltips on .hourly-bar rects
//   (2) 5-second poll that swaps stat-card text from JSON
// No framework. Polling target derived from window.location.pathname.
(function () {
  "use strict";

  var pollUrl = (function () {
    var p = window.location.pathname;
    if (p.indexOf("/admin/user/") === 0) return p + ".json";
    return "/admin/usage.json";
  })();

  var tip = document.getElementById("usage-hover-tip");

  function fmtBytes(n) {
    if (!n) return "0 B";
    var u = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    var v = Number(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(2) + " " + u[i];
  }

  function attachHover(svg) {
    if (!svg || !tip) return;
    svg.addEventListener("mousemove", function (e) {
      var t = e.target;
      if (!t || t.tagName !== "rect" || !t.classList.contains("hourly-bar")) {
        tip.style.display = "none";
        return;
      }
      var hour = t.getAttribute("data-hour") || "";
      var bytes = t.getAttribute("data-bytes") || "0";
      tip.textContent = hour.replace("T", " ") + " · " + fmtBytes(bytes);
      tip.style.display = "block";
      tip.style.left = (e.pageX + 10) + "px";
      tip.style.top = (e.pageY - 28) + "px";
    });
    svg.addEventListener("mouseleave", function () {
      tip.style.display = "none";
    });
  }

  function setText(sel, txt) {
    var el = document.querySelector(sel);
    if (el && txt !== undefined) el.textContent = txt;
  }

  // Initial hover wiring
  var bars = document.querySelectorAll("svg.hourly-bars");
  for (var i = 0; i < bars.length; i++) attachHover(bars[i]);

  // Polling
  setInterval(function () {
    fetch(pollUrl, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (data) {
        if (!data) return;
        if (data.stats) {
          setText("[data-stat=current_hour] .v", fmtBytes(data.stats.current_hour_bytes));
          setText("[data-stat=today] .v", fmtBytes(data.stats.today_bytes));
          setText("[data-stat=last_7d] .v", fmtBytes(data.stats.last_7d_bytes));
          setText("[data-stat=cycle] .v", fmtBytes(data.stats.cycle_bytes));
        }
      });
  }, 5000);
})();
