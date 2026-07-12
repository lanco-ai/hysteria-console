// Lightweight interactive chart for /admin/codex.
(function () {
  "use strict";

  var root = document.getElementById("codex-dashboard");
  var svg = document.getElementById("codex-quota-chart");
  var frame = document.getElementById("codex-chart-frame");
  var empty = document.getElementById("codex-chart-empty");
  var tooltip = document.getElementById("codex-chart-tooltip");
  var refreshBtn = document.getElementById("codex-refresh-now");
  if (!root || !svg || !frame || !tooltip) return;

  var endpoint = root.getAttribute("data-endpoint") || "/admin/codex.json";
  var range = "day";
  var payload = null;
  var points = [];
  var requestController = null;
  var pollTimer = null;
  var countdownTimer = null;
  var requestSerial = 0;
  var activeTipIndex = -1;
  var RANGE_SECONDS = {day: 86400, week: 604800, month: 2678400, year: 31622400};
  var RANGE_LABELS = {day: "24 小时", week: "7 天", month: "31 天", year: "1 年"};
  var WIDTH = 1000;
  var HEIGHT = 360;
  var MARGIN = {top: 20, right: 22, bottom: 45, left: 54};
  var PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
  var PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch];
    });
  }

  function numberOrNull(value) {
    var n = Number(value);
    return value !== null && value !== "" && isFinite(n) ? n : null;
  }

  function percent(value) {
    var n = numberOrNull(value);
    if (n === null) return "未提供";
    return (Math.round(n * 100) / 100).toLocaleString("zh-CN", {maximumFractionDigits: 2}) + "%";
  }

  function dateTime(epoch, withSeconds) {
    var n = numberOrNull(epoch);
    if (n === null || n <= 0) return "未提供";
    return new Date(n * 1000).toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      second: withSeconds ? "2-digit" : undefined, hour12: false
    });
  }

  function duration(target, dueText) {
    var n = numberOrNull(target);
    if (n === null) return "—";
    var seconds = Math.floor(n - Date.now() / 1000);
    if (seconds <= 0) return dueText || "即将刷新";
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var secs = seconds % 60;
    if (days) return days + "天 " + hours + "小时 " + minutes + "分";
    if (hours) return hours + "小时 " + minutes + "分 " + secs + "秒";
    return minutes + "分 " + secs + "秒";
  }

  function selectOne(selector, host) {
    return (host || document).querySelector(selector);
  }

  function setText(selector, value, host) {
    var node = selectOne(selector, host);
    if (node && node.textContent !== String(value)) node.textContent = String(value);
  }

  function updateWindow(key, data) {
    var card = selectOne('[data-quota="' + key + '"]');
    if (!card) return;
    var available = Boolean(data && data.available);
    var remaining = available ? numberOrNull(data.remaining_percent) : null;
    var used = available ? numberOrNull(data.used_percent) : null;
    card.setAttribute("data-available", available ? "true" : "false");
    card.classList.toggle("is-unavailable", !available);
    card.classList.toggle("is-low", available && remaining !== null && remaining <= 20);
    setText('[data-role="remaining"]', percent(remaining), card);
    setText('[data-role="remaining-small"]', percent(remaining), card);
    setText('[data-role="used"]', percent(used), card);
    var bar = selectOne('[data-role="bar"]', card);
    if (bar) bar.style.width = available && remaining !== null ? Math.max(0, Math.min(100, remaining)) + "%" : "0%";
    var reset = selectOne('[data-role="reset-time"]', card);
    if (reset) {
      var epoch = available ? numberOrNull(data.resets_at) : null;
      reset.setAttribute("data-epoch", epoch === null ? "" : String(epoch));
      reset.textContent = available ? dateTime(epoch, false) : "当前账户响应中未提供这个额度窗口";
    }
  }

  function setCollectorStatus(status) {
    var badge = selectOne('[data-role="collector-status"]');
    if (!badge) return;
    var states = {
      live: ["采集正常", "is-live"], delayed: ["采集延迟", "is-paused"],
      stale: ["数据过期", "is-error"], error: ["采集异常", "is-error"],
      empty: ["等待首采", "is-paused"]
    };
    var state = states[status] || ["状态未知", "is-paused"];
    badge.textContent = state[0];
    badge.classList.remove("is-live", "is-paused", "is-error");
    badge.classList.add(state[1]);
  }

  function updateError(error) {
    var host = document.getElementById("codex-collector-error");
    if (!host) return;
    if (!error) {
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    var strong = document.createElement("strong");
    var span = document.createElement("span");
    strong.textContent = "最近一次采集失败";
    span.textContent = String(error);
    host.replaceChildren(strong, span);
    host.hidden = false;
  }

  function updateSummary(data) {
    var freshness = data.freshness || {};
    var account = data.account || {};
    var windows = data.windows || {};
    updateWindow("five_hour", windows.five_hour || {});
    updateWindow("weekly", windows.weekly || {});
    setCollectorStatus(freshness.status);
    setText('[data-role="last-success"]', "最近采集：" + dateTime(freshness.last_success_at, true));
    var plan = String(account.plan_type || "unknown").replace(/_/g, " ");
    setText('[data-role="plan-type"]', plan.replace(/\b\w/g, function (c) { return c.toUpperCase(); }));
    setText('[data-role="limit-id"]', account.limit_id || "codex");
    setText('[data-role="reset-credits"]', Number.isInteger(account.reset_credits_available) ? account.reset_credits_available : "—");
    updateError(freshness.last_error);
    var history = data.history || {};
    setText('[data-role="history-start"]', history.started_at ? "历史始于 " + dateTime(history.started_at, false) : "历史记录：等待首采");
  }

  function xAt(ts, start, end) {
    if (end <= start) return MARGIN.left + PLOT_W;
    return MARGIN.left + Math.max(0, Math.min(1, (ts - start) / (end - start))) * PLOT_W;
  }

  function yAt(value) {
    return MARGIN.top + (100 - Math.max(0, Math.min(100, value))) * PLOT_H / 100;
  }

  function xLabel(epoch) {
    var d = new Date(epoch * 1000);
    if (range === "day") return d.toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit", hour12: false});
    if (range === "year") return d.toLocaleDateString("zh-CN", {year: "numeric", month: "2-digit"});
    return d.toLocaleDateString("zh-CN", {month: "2-digit", day: "2-digit"});
  }

  function pathFor(key, start, end) {
    var path = [];
    var drawing = false;
    points.forEach(function (point) {
      var value = numberOrNull(point[key]);
      if (value === null) { drawing = false; return; }
      var x = xAt(Number(point.ts), start, end).toFixed(2);
      var y = yAt(value).toFixed(2);
      path.push((drawing ? "L" : "M") + x + "," + y);
      drawing = true;
    });
    return path.join(" ");
  }

  function circlesFor(key, seriesClass, start, end) {
    var radius = points.length > 380 ? 1.55 : points.length > 180 ? 1.9 : 2.5;
    var out = [];
    points.forEach(function (point, index) {
      var value = numberOrNull(point[key]);
      if (value === null) return;
      out.push('<circle class="codex-node ' + seriesClass + '" data-index="' + index +
        '" cx="' + xAt(Number(point.ts), start, end).toFixed(2) + '" cy="' + yAt(value).toFixed(2) +
        '" r="' + radius + '"></circle>');
    });
    return out.join("");
  }

  function renderChart(data) {
    points = Array.isArray(data.points) ? data.points.slice().sort(function (a, b) { return Number(a.ts) - Number(b.ts); }) : [];
    activeTipIndex = -1;
    tooltip.hidden = true;
    var end = numberOrNull(data.generated_at) || Math.floor(Date.now() / 1000);
    var start = end - (RANGE_SECONDS[range] || RANGE_SECONDS.day);
    var parts = [];
    [0, 25, 50, 75, 100].forEach(function (value) {
      var y = yAt(value).toFixed(2);
      parts.push('<line class="codex-grid-line" x1="' + MARGIN.left + '" y1="' + y + '" x2="' + (WIDTH - MARGIN.right) + '" y2="' + y + '"></line>');
      parts.push('<text class="codex-axis-label y" x="' + (MARGIN.left - 10) + '" y="' + (Number(y) + 4) + '" text-anchor="end">' + value + '%</text>');
    });
    for (var i = 0; i < 5; i++) {
      var tickTs = start + (end - start) * i / 4;
      var x = xAt(tickTs, start, end).toFixed(2);
      parts.push('<line class="codex-tick" x1="' + x + '" y1="' + (HEIGHT - MARGIN.bottom) + '" x2="' + x + '" y2="' + (HEIGHT - MARGIN.bottom + 5) + '"></line>');
      parts.push('<text class="codex-axis-label x" x="' + x + '" y="' + (HEIGHT - 16) + '" text-anchor="middle">' + escapeHtml(xLabel(tickTs)) + '</text>');
    }
    var fivePath = pathFor("five_hour_remaining", start, end);
    var weekPath = pathFor("weekly_remaining", start, end);
    if (fivePath) parts.push('<path class="codex-series-line series-five" d="' + fivePath + '"></path>');
    if (weekPath) parts.push('<path class="codex-series-line series-week" d="' + weekPath + '"></path>');
    parts.push(circlesFor("five_hour_remaining", "series-five", start, end));
    parts.push(circlesFor("weekly_remaining", "series-week", start, end));
    parts.push('<g id="codex-crosshair" class="codex-crosshair" visibility="hidden"><line x1="0" y1="' + MARGIN.top + '" x2="0" y2="' + (HEIGHT - MARGIN.bottom) + '"></line><circle class="series-five" cx="0" cy="0" r="4" visibility="hidden"></circle><circle class="series-week" cx="0" cy="0" r="4" visibility="hidden"></circle></g>');
    svg.innerHTML = parts.join("");
    svg.__chartDomain = {start: start, end: end};
    if (empty) empty.hidden = points.length > 0;
    var summary = selectOne('[data-role="chart-summary"]');
    if (summary) summary.textContent = points.length ? points.length + " 个节点 · " + (RANGE_LABELS[range] || "") : "当前范围暂无数据";
  }

  function nearestIndex(ts) {
    if (!points.length) return -1;
    var low = 0;
    var high = points.length - 1;
    while (low < high) {
      var mid = Math.floor((low + high) / 2);
      if (Number(points[mid].ts) < ts) low = mid + 1;
      else high = mid;
    }
    if (low > 0 && Math.abs(Number(points[low - 1].ts) - ts) <= Math.abs(Number(points[low].ts) - ts)) return low - 1;
    return low;
  }

  function positionTooltip(index) {
    if (index < 0 || !points[index] || !svg.__chartDomain) return;
    var point = points[index];
    var x = xAt(Number(point.ts), svg.__chartDomain.start, svg.__chartDomain.end);
    var five = numberOrNull(point.five_hour_remaining);
    var week = numberOrNull(point.weekly_remaining);
    var crosshair = document.getElementById("codex-crosshair");
    if (crosshair) {
      crosshair.setAttribute("visibility", "visible");
      var line = crosshair.querySelector("line");
      if (line) { line.setAttribute("x1", x); line.setAttribute("x2", x); }
      var fiveDot = crosshair.querySelector("circle.series-five");
      var weekDot = crosshair.querySelector("circle.series-week");
      if (fiveDot) {
        fiveDot.setAttribute("cx", x); fiveDot.setAttribute("cy", five === null ? 0 : yAt(five));
        fiveDot.setAttribute("visibility", five === null ? "hidden" : "visible");
      }
      if (weekDot) {
        weekDot.setAttribute("cx", x); weekDot.setAttribute("cy", week === null ? 0 : yAt(week));
        weekDot.setAttribute("visibility", week === null ? "hidden" : "visible");
      }
    }
    if (activeTipIndex !== index) {
      activeTipIndex = index;
      tooltip.innerHTML = '<time>' + escapeHtml(dateTime(point.ts, true)) + '</time>' +
        '<span><i class="series-five"></i>5 小时 <strong>' + escapeHtml(percent(five)) + '</strong></span>' +
        '<span><i class="series-week"></i>周额度 <strong>' + escapeHtml(percent(week)) + '</strong></span>';
    }
    tooltip.hidden = false;
    var frameRect = frame.getBoundingClientRect();
    var svgRect = svg.getBoundingClientRect();
    var screenX = svgRect.left - frameRect.left + x / WIDTH * svgRect.width;
    var tooltipW = tooltip.offsetWidth || 190;
    var left = Math.max(8, Math.min(frameRect.width - tooltipW - 8, screenX + 12));
    tooltip.style.left = left + "px";
    tooltip.style.top = "16px";
  }

  function pointerMove(event) {
    if (!points.length || !svg.__chartDomain) return;
    var rect = svg.getBoundingClientRect();
    var x = (event.clientX - rect.left) / Math.max(1, rect.width) * WIDTH;
    if (x < MARGIN.left || x > WIDTH - MARGIN.right) return hideTooltip();
    var ratio = (x - MARGIN.left) / PLOT_W;
    var ts = svg.__chartDomain.start + ratio * (svg.__chartDomain.end - svg.__chartDomain.start);
    positionTooltip(nearestIndex(ts));
  }

  function hideTooltip() {
    tooltip.hidden = true;
    activeTipIndex = -1;
    var crosshair = document.getElementById("codex-crosshair");
    if (crosshair) crosshair.setAttribute("visibility", "hidden");
  }

  function updateCountdowns() {
    document.querySelectorAll('[data-quota]').forEach(function (card) {
      var target = selectOne('[data-role="reset-time"]', card);
      var countdown = selectOne('[data-role="countdown"]', card);
      if (!countdown || !target) return;
      var epoch = target.getAttribute("data-epoch");
      countdown.textContent = epoch ? duration(epoch, "等待 Codex 更新") : "—";
    });
    if (payload && payload.freshness) {
      setText('[data-role="next-poll"]', duration(payload.freshness.next_poll_at, "等待调度"));
    }
  }

  function applyPayload(data) {
    payload = data;
    updateSummary(data);
    renderChart(data);
    updateCountdowns();
  }

  function schedulePoll(seconds) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(function () { load(false); }, Math.max(30, Number(seconds) || 180) * 1000);
  }

  function load(manual) {
    if (document.hidden && !manual) { schedulePoll(180); return; }
    var serial = ++requestSerial;
    if (requestController) requestController.abort();
    requestController = typeof AbortController !== "undefined" ? new AbortController() : null;
    if (refreshBtn) refreshBtn.disabled = true;
    fetch(endpoint + "?range=" + encodeURIComponent(range), {
      credentials: "same-origin", cache: "no-store",
      signal: requestController ? requestController.signal : undefined,
      headers: {"Accept": "application/json"}
    }).then(function (response) {
      if (!response.ok || (response.headers.get("content-type") || "").indexOf("application/json") < 0) throw new Error("额度接口不可用");
      return response.json();
    }).then(function (data) {
      if (serial !== requestSerial) return;
      applyPayload(data);
      schedulePoll(data.poll_interval_seconds || 180);
    }).catch(function (error) {
      if (error && error.name === "AbortError") return;
      setCollectorStatus("error");
      schedulePoll(180);
    }).finally(function () {
      if (serial === requestSerial && refreshBtn) refreshBtn.disabled = false;
    });
  }

  document.querySelectorAll("[data-range]").forEach(function (button) {
    button.addEventListener("click", function () {
      var next = button.getAttribute("data-range");
      if (!RANGE_SECONDS[next] || next === range) return;
      range = next;
      document.querySelectorAll("[data-range]").forEach(function (item) {
        var active = item === button;
        item.classList.toggle("active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });
      load(true);
    });
  });
  svg.addEventListener("pointermove", pointerMove);
  svg.addEventListener("pointerdown", pointerMove);
  svg.addEventListener("pointerleave", hideTooltip);
  if (refreshBtn) refreshBtn.addEventListener("click", function () { load(true); });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) load(false);
  });
  window.addEventListener("pagehide", function () {
    if (pollTimer) clearTimeout(pollTimer);
    if (countdownTimer) clearInterval(countdownTimer);
    if (requestController) requestController.abort();
  });

  countdownTimer = setInterval(updateCountdowns, 1000);
  load(true);
})();
