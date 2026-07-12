// /admin/usage and /admin/user/<uid> client glue.
// Refreshes the live cards and the inline SVG charts without a full page reload.
(function () {
  "use strict";

  var pollUrl = (function () {
    var p = window.location.pathname;
    if (p.indexOf("/admin/user/") === 0) return p + ".json";
    return "/admin/analytics.json";
  })();

  var tip = document.getElementById("usage-hover-tip");
  var refreshBtn = document.getElementById("usage-refresh-now");
  var pollStatus = document.querySelector('[data-role="poll-status"]');
  var CHART_REFRESH_MS = 30000;
  var lastChartsAt = Date.now();
  var lastChartSignature = null;

  function fmtBytes(n) {
    var v = Math.max(0, Number(n) || 0);
    if (!v) return "0 B";
    var u = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(2) + " " + u[i];
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch];
    });
  }

  function attachHover(svg) {
    if (!svg || !tip || svg.__hyHoverBound) return;
    svg.__hyHoverBound = true;
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
    if (el && txt !== undefined && el.textContent !== String(txt)) el.textContent = String(txt);
  }

  function setPollStatus(text, cls) {
    if (!pollStatus) return;
    pollStatus.textContent = text;
    pollStatus.classList.remove("is-live", "is-paused", "is-error");
    if (cls) pollStatus.classList.add(cls);
  }

  function stamp() {
    return new Date().toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function updateHourlyChart(svg, series) {
    if (!svg || !Array.isArray(series)) return;
    var n = series.length;
    if (!n) {
      svg.setAttribute("viewBox", "0 0 0 0");
      svg.innerHTML = "";
      return;
    }

    var oldViewBox = (svg.getAttribute("viewBox") || "0 0 0 136").split(/\s+/);
    var totalHeight = Number(oldViewBox[3]) || 136;
    var labelStrip = 16;
    var height = Math.max(32, totalHeight - labelStrip);
    var barW = 3;
    var gap = 1;
    var width = n * (barW + gap) - gap;
    var maxValue = 0;
    var peakHour = "";
    series.forEach(function (item) {
      var value = Math.max(0, Number(item.bytes) || 0);
      if (value > maxValue) {
        maxValue = value;
        peakHour = String(item.hour || "");
      }
    });
    if (maxValue <= 0) maxValue = 1;

    var separators = [];
    var bars = [];
    var previousDay = "";
    series.forEach(function (item, i) {
      var hour = String(item.hour || "");
      var day = hour.slice(0, 10);
      if (i > 0 && day !== previousDay) {
        var sepX = i * (barW + gap);
        separators.push('<line x1="' + sepX + '" y1="0" x2="' + sepX + '" y2="' + height + '"></line>');
      }
      previousDay = day;
      var value = Math.max(0, Number(item.bytes) || 0);
      if (value <= 0) return;
      var barH = Math.max(1, Math.round(height * value / maxValue));
      var x = i * (barW + gap);
      var cls = hour === peakHour ? "hourly-bar peak" : "hourly-bar";
      bars.push('<rect class="' + cls + '" x="' + x + '" y="' + (height - barH) +
        '" width="' + barW + '" height="' + barH + '" data-hour="' + escapeHtml(hour) +
        '" data-bytes="' + value + '"></rect>');
    });

    var labels = [];
    var cursor = 0;
    while (cursor < n) {
      var labelDay = String(series[cursor].hour || "").slice(0, 10);
      var start = cursor;
      while (cursor < n && String(series[cursor].hour || "").slice(0, 10) === labelDay) cursor++;
      var middle = (start + (cursor - start) / 2) * (barW + gap);
      labels.push('<text class="day-label" x="' + middle.toFixed(1) + '" y="' + (height + labelStrip - 3) +
        '" text-anchor="middle">' + escapeHtml(labelDay.slice(5)) + '</text>');
    }

    svg.setAttribute("viewBox", "0 0 " + width + " " + (height + labelStrip));
    svg.setAttribute("aria-label", "过去 " + n + " 小时流量");
    svg.innerHTML = '<g class="day-separators">' + separators.join("") + '</g>' +
      '<g class="bars">' + bars.join("") + '</g>' +
      '<g class="day-labels">' + labels.join("") + '</g>';
    attachHover(svg);
  }

  function updateHeatmap(svg, grid, timestamp) {
    if (!svg || !Array.isArray(grid)) return;
    var rows = grid.length;
    if (!rows) {
      svg.setAttribute("viewBox", "0 0 0 0");
      svg.innerHTML = "";
      return;
    }
    var cellW = 20;
    var cellH = 22;
    var labelW = 46;
    var width = labelW + 24 * cellW;
    var height = rows * cellH + 28;
    var maxValue = 0;
    grid.forEach(function (row) {
      (row.hours || []).forEach(function (value) {
        maxValue = Math.max(maxValue, Number(value) || 0);
      });
    });
    if (maxValue <= 0) maxValue = 1;

    var currentKey = String(timestamp || "").slice(0, 13);
    var todayIndex = rows - 1;
    var parts = [];
    grid.forEach(function (row, r) {
      var date = String(row.date || "");
      var y = r * cellH + cellH - 6;
      parts.push('<text class="heat-date" x="' + (labelW - 6) + '" y="' + y +
        '" text-anchor="end">' + escapeHtml(date.slice(5)) + '</text>');
    });
    grid.forEach(function (row, r) {
      var date = String(row.date || "");
      var hours = row.hours || [];
      for (var c = 0; c < 24; c++) {
        var value = Math.max(0, Number(hours[c]) || 0);
        var future = r === todayIndex && currentKey.slice(0, 10) === date &&
          Number(currentKey.slice(11, 13)) < c;
        var x = labelW + c * cellW;
        var yCell = r * cellH + 1;
        var cls = future ? "heat-cell future" : "heat-cell";
        var opacity = future ? "" : ' opacity="' + (0.05 + 0.95 * value / maxValue).toFixed(2) + '"';
        var title = date + " " + ("0" + c).slice(-2) + " · " + fmtBytes(value);
        parts.push('<rect class="' + cls + '" x="' + x + '" y="' + yCell +
          '" width="' + (cellW - 1) + '" height="' + (cellH - 2) + '"' + opacity +
          '><title>' + escapeHtml(title) + '</title></rect>');
      }
    });
    [0, 4, 8, 12, 16, 20, 23].forEach(function (h) {
      var x = labelW + h * cellW + cellW / 2;
      parts.push('<text class="heat-hour" x="' + x + '" y="' + (rows * cellH + 12) +
        '" text-anchor="middle">' + h + '</text>');
    });
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("aria-label", "7 天小时热图");
    svg.innerHTML = parts.join("");
  }

  function sparklineSvg(values) {
    var arr = Array.isArray(values) ? values : [];
    var n = arr.length;
    if (!n) return '<svg class="spark" viewBox="0 0 0 14" aria-hidden="true"></svg>';
    var height = 14;
    var barW = 3;
    var gap = 1;
    var width = n * (barW + gap) - gap;
    var maxValue = 0;
    arr.forEach(function (value) { maxValue = Math.max(maxValue, Number(value) || 0); });
    if (maxValue <= 0) maxValue = 1;
    var floorY = height - 1;
    var drawH = height - 2;
    var points = [];
    arr.forEach(function (value, i) {
      value = Math.max(0, Number(value) || 0);
      points.push({
        x: n === 1 ? width / 2 : i * width / (n - 1),
        y: Math.max(1, Math.min(floorY, floorY - drawH * value / maxValue))
      });
    });
    var line = points.map(function (p, i) {
      return (i ? 'L' : 'M') + p.x.toFixed(2) + ',' + p.y.toFixed(2);
    }).join(' ');
    var area = 'M0,' + floorY + ' ' + points.map(function (p) {
      return 'L' + p.x.toFixed(2) + ',' + p.y.toFixed(2);
    }).join(' ') + ' L' + width + ',' + floorY + ' Z';
    var last = points[points.length - 1];
    return '<svg class="spark" viewBox="0 0 ' + width + ' ' + height + '" aria-hidden="true">' +
      '<path class="spark-area" d="' + area + '"></path>' +
      '<path class="spark-line" d="' + line + '" vector-effect="non-scaling-stroke"></path>' +
      '<circle class="spark-dot today" cx="' + last.x.toFixed(2) + '" cy="' + last.y.toFixed(2) + '" r="1.8"></circle>' +
      '</svg>';
  }

  function updateTopN(host, top) {
    if (!host || !Array.isArray(top)) return;
    if (!top.length) {
      host.innerHTML = '<div class="empty">暂无数据</div>';
      return;
    }
    host.innerHTML = top.map(function (item) {
      var uid = String(item.uid || "");
      return '<a class="top-row" href="/admin/user/' + encodeURIComponent(uid) + '">' +
        '<span class="top-uid">' + escapeHtml(uid) + ' ↗</span>' +
        '<span class="top-spark">' + sparklineSvg(item.spark) + '</span>' +
        '<span class="top-bytes">' + fmtBytes(item.last_24h_bytes) + '</span></a>';
    }).join("");
  }

  function chartSignature(data) {
    // FNV-style rolling hash avoids allocating a large JSON string merely to
    // decide whether hundreds of SVG nodes need rebuilding.
    var hash = 2166136261;
    function mixNumber(value) {
      var n = Math.max(0, Number(value) || 0);
      var lo = n >>> 0;
      var hi = Math.floor(n / 4294967296) >>> 0;
      hash = Math.imul((hash ^ lo) >>> 0, 16777619) >>> 0;
      hash = Math.imul((hash ^ hi) >>> 0, 16777619) >>> 0;
    }
    function mixText(value) {
      var s = String(value == null ? "" : value);
      for (var i = 0; i < s.length; i++) {
        hash = Math.imul((hash ^ s.charCodeAt(i)) >>> 0, 16777619) >>> 0;
      }
    }
    var hourly = data.hourly_totals || data.hourly_bars || [];
    hourly.forEach(function (item) { mixText(item.hour); mixNumber(item.bytes); });
    (data.heatmap || []).forEach(function (row) {
      mixText(row.date);
      (row.hours || []).forEach(mixNumber);
    });
    (data.top_n || []).forEach(function (item) {
      mixText(item.uid);
      mixNumber(item.last_24h_bytes);
      (item.spark || []).forEach(mixNumber);
    });
    return hash;
  }

  function updatePayload(data) {
    if (!data) return;
    if (data.stats) {
      setText("[data-stat=current_hour] .v", fmtBytes(data.stats.current_hour_bytes));
      setText("[data-stat=today] .v", fmtBytes(data.stats.today_bytes));
      setText("[data-stat=last_7d] .v", fmtBytes(data.stats.last_7d_bytes));
      setText("[data-stat=cycle] .v", fmtBytes(data.stats.cycle_bytes));
      setText("[data-role=usage-online]", data.stats.online);
      setText("[data-role=usage-yesterday]", fmtBytes(data.stats.yesterday_bytes));
      setText("[data-role=usage-7d-average]", fmtBytes(Math.floor((Number(data.stats.last_7d_bytes) || 0) / 7)));
      setText("[data-role=cycle-progress]", "第 " + data.stats.cycle_day + " / " + data.stats.cycle_total_days + " 天");
    } else {
      setText("[data-stat=current_hour] .v", fmtBytes(data.current_hour_bytes));
      setText("[data-stat=today] .v", fmtBytes(data.today_bytes));
      var quota = Number(data.cycle_quota_bytes) || 0;
      var cycleText = fmtBytes(data.cycle_used_bytes) + (quota > 0 ? " / " + fmtBytes(quota) : " (无限)");
      setText("[data-stat=user_cycle] .v", cycleText);
      setText("[data-role=detail-online]", data.online);
    }

    var hourly = data.hourly_totals || data.hourly_bars;
    if (hourly || data.heatmap || data.top_n) {
      var signature = chartSignature(data);
      if (signature !== lastChartSignature) {
        if (hourly) updateHourlyChart(document.querySelector("svg.hourly-bars"), hourly);
        if (data.heatmap) updateHeatmap(document.querySelector("svg.heatmap"), data.heatmap, data.ts);
        if (data.top_n) updateTopN(document.getElementById("top-n-host"), data.top_n);
        lastChartSignature = signature;
      }
    }
  }

  // Initial hover wiring
  var bars = document.querySelectorAll("svg.hourly-bars");
  for (var i = 0; i < bars.length; i++) attachHover(bars[i]);

  // The 30-day table is large and normally collapsed. Fetch it only after the
  // user opens the section, then keep the loaded fragment for that page visit.
  var historyDetails = document.getElementById("usage-history");
  var historyHost = document.getElementById("usage-history-host");
  var historyState = "idle";
  function loadHistory() {
    if (!historyHost || historyState === "loading" || historyState === "loaded") return;
    historyState = "loading";
    historyHost.setAttribute("aria-busy", "true");
    historyHost.innerHTML = '<div class="empty history-placeholder">正在加载每日明细…</div>';
    fetch(historyHost.dataset.url || "/admin/usage-history", {
      credentials: "same-origin", cache: "no-store"
    })
      .then(function (r) {
        if (!r.ok || r.redirected) throw new Error("history " + r.status);
        return r.text();
      })
      .then(function (markup) {
        historyHost.innerHTML = markup;
        historyState = "loaded";
      })
      .catch(function () {
        historyState = "idle";
        historyHost.innerHTML = '<div class="empty history-placeholder">加载失败，收起后重新展开即可重试</div>';
      })
      .finally(function () { historyHost.setAttribute("aria-busy", "false"); });
  }
  if (historyDetails) historyDetails.addEventListener("toggle", function () {
    if (historyDetails.open) loadHistory();
  });

  // Polling
  var timer = null;
  var inflight = false;
  function tick(forceCharts) {
    if (inflight) return;
    var includeCharts = forceCharts === true || Date.now() - lastChartsAt >= CHART_REFRESH_MS;
    var requestUrl = pollUrl + (includeCharts ? "" : "?summary=1");
    inflight = true;
    if (refreshBtn) refreshBtn.disabled = true;
    setPollStatus("刷新中", "is-live");
    fetch(requestUrl, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (data) {
        if (!data) {
          setPollStatus("刷新失败", "is-error");
          return;
        }
        updatePayload(data);
        if (includeCharts) lastChartsAt = Date.now();
        setPollStatus("更新 " + stamp(), "is-live");
      })
      .finally(function () {
        inflight = false;
        if (refreshBtn) refreshBtn.disabled = false;
      });
  }
  function start() {
    if (!timer) {
      tick();
      timer = setInterval(tick, 5000);
    }
  }
  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    setPollStatus("已暂停", "is-paused");
  }
  if (refreshBtn) refreshBtn.addEventListener("click", function () { tick(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else start();
  });
  window.addEventListener("pagehide", stop);
  start();
})();
