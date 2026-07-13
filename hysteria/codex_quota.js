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
  var weeklyEvents = [];
  var weeklyGuides = [];
  var RANGE_SECONDS = {day: 86400, week: 604800, month: 2678400, year: 31622400};
  var RANGE_LABELS = {day: "24 小时", week: "7 天", month: "31 天", year: "1 年"};
  var WIDTH = 1200;
  var HEIGHT = 500;
  var MARGIN = {top: 34, right: 34, bottom: 96, left: 70};
  var PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
  var PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;
  var MAX_VISIBLE_DOTS = 32;
  var MIN_EVENT_LABEL_GAP = 92;

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
    root.classList.toggle("is-week-only", !Boolean(windows.five_hour && windows.five_hour.available));
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
    var radius = 4;
    var color = seriesClass === "series-five" ? "#7467e8" : "#087f83";
    var fill = seriesClass === "series-five" ? color : "#ffffff";
    var out = [];
    var validIndices = [];
    points.forEach(function (point, index) {
      if (numberOrNull(point[key]) !== null) validIndices.push(index);
    });
    var step = Math.max(1, Math.ceil(validIndices.length / MAX_VISIBLE_DOTS));
    validIndices.forEach(function (index, visibleIndex) {
      if (visibleIndex % step !== 0 && visibleIndex !== validIndices.length - 1) return;
      var point = points[index];
      var value = numberOrNull(point[key]);
      out.push('<circle class="codex-node ' + seriesClass + '" data-index="' + index +
        '" cx="' + xAt(Number(point.ts), start, end).toFixed(2) + '" cy="' + yAt(value).toFixed(2) +
        '" r="' + radius + '" fill="' + fill + '" stroke="' + color +
        '" stroke-width="2"></circle>');
    });
    return out.join("");
  }

  function weeklyChangeEvents() {
    var events = [];
    var previous = null;
    points.forEach(function (point, index) {
      var value = numberOrNull(point.weekly_remaining);
      if (value === null) return;
      if (previous && Math.abs(value - previous.value) >= 0.001) {
        events.push({
          index: index,
          ts: Number(point.ts),
          value: value,
          delta: Math.round((value - previous.value) * 100) / 100
        });
      }
      previous = {value: value, index: index};
    });
    return events;
  }

  function selectWeeklyGuides(events, start, end) {
    if (!events.length) return [];
    var maxGuides = Math.max(4, Math.floor(PLOT_W / MIN_EVENT_LABEL_GAP));
    var candidates = events.slice().sort(function (a, b) {
      var aReset = a.delta > 0 ? 1 : 0;
      var bReset = b.delta > 0 ? 1 : 0;
      if (aReset !== bReset) return bReset - aReset;
      if (Math.abs(a.delta) !== Math.abs(b.delta)) return Math.abs(b.delta) - Math.abs(a.delta);
      return b.ts - a.ts;
    });
    var selected = [];
    candidates.some(function (event) {
      var x = xAt(event.ts, start, end);
      var overlaps = selected.some(function (other) {
        return Math.abs(x - xAt(other.ts, start, end)) < MIN_EVENT_LABEL_GAP;
      });
      if (!overlaps) selected.push(event);
      return selected.length >= maxGuides;
    });
    return selected.sort(function (a, b) { return a.ts - b.ts; });
  }

  function eventTimeLabel(epoch) {
    var d = new Date(epoch * 1000);
    var time = d.toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit", hour12: false});
    if (range === "day") return {top: time, bottom: "", width: 64};
    var date = d.toLocaleDateString("zh-CN", {
      year: range === "year" ? "2-digit" : undefined,
      month: "2-digit", day: "2-digit"
    });
    return {top: date, bottom: time, width: range === "year" ? 88 : 76};
  }

  function renderWeeklyEventLayer(start, end) {
    weeklyEvents = weeklyChangeEvents();
    weeklyGuides = selectWeeklyGuides(weeklyEvents, start, end);
    var guideMap = {};
    weeklyGuides.forEach(function (event) { guideMap[event.index] = true; });
    var plotBottom = HEIGHT - MARGIN.bottom;
    var guides = [];
    var nodes = [];

    weeklyEvents.forEach(function (event) {
      var x = xAt(event.ts, start, end);
      var y = yAt(event.value);
      var resetClass = event.delta > 0 ? " is-reset" : "";
      if (guideMap[event.index]) {
        var label = eventTimeLabel(event.ts);
        var labelX = Math.max(MARGIN.left + label.width / 2, Math.min(WIDTH - MARGIN.right - label.width / 2, x));
        var labelY = plotBottom + (label.bottom ? 10 : 13);
        var labelH = label.bottom ? 42 : 28;
        guides.push('<g class="codex-week-event-guide' + resetClass + '" data-index="' + event.index + '">' +
          '<line class="codex-week-event-line" x1="' + x.toFixed(2) + '" y1="' + (y + 7).toFixed(2) +
          '" x2="' + x.toFixed(2) + '" y2="' + (plotBottom + 7) + '"></line>' +
          '<rect x="' + (labelX - label.width / 2).toFixed(2) + '" y="' + labelY + '" width="' + label.width +
          '" height="' + labelH + '" rx="9"></rect>' +
          '<text x="' + labelX.toFixed(2) + '" y="' + (labelY + 18) + '" text-anchor="middle">' + escapeHtml(label.top) +
          (label.bottom ? '<tspan x="' + labelX.toFixed(2) + '" dy="15">' + escapeHtml(label.bottom) + '</tspan>' : '') +
          '</text></g>');
      }
      nodes.push('<circle class="codex-week-event-node' + resetClass + '" data-index="' + event.index +
        '" cx="' + x.toFixed(2) + '" cy="' + y.toFixed(2) + '" r="' + (guideMap[event.index] ? 5 : 3.5) + '"></circle>');
    });
    return {guides: guides.join(""), nodes: nodes.join("")};
  }

  function weeklyDeltaAt(index) {
    var current = points[index] ? numberOrNull(points[index].weekly_remaining) : null;
    if (current === null) return null;
    for (var i = index - 1; i >= 0; i--) {
      var previous = numberOrNull(points[i].weekly_remaining);
      if (previous !== null) return Math.round((current - previous) * 100) / 100;
    }
    return null;
  }

  function signedPercent(value) {
    var n = numberOrNull(value);
    if (n === null) return "";
    if (Math.abs(n) < 0.001) return "0%";
    return (n > 0 ? "+" : "−") + percent(Math.abs(n));
  }

  function latestLabel(key, label, color, start, end, offset) {
    for (var i = points.length - 1; i >= 0; i--) {
      var value = numberOrNull(points[i][key]);
      if (value === null) continue;
      var x = xAt(Number(points[i].ts), start, end);
      var y = Math.max(MARGIN.top + 13, Math.min(HEIGHT - MARGIN.bottom - 13, yAt(value) + offset));
      var boxW = 112;
      var boxX = Math.min(x + 12, WIDTH - MARGIN.right - boxW);
      if (boxX < x + 5) boxX = Math.max(MARGIN.left, x - boxW - 12);
      return '<g class="codex-latest-label"><rect x="' + boxX.toFixed(2) + '" y="' + (y - 14).toFixed(2) +
        '" width="' + boxW + '" height="28" rx="14" fill="#ffffff" stroke="' + color +
        '" stroke-width="2"></rect><text x="' + (boxX + boxW / 2).toFixed(2) + '" y="' + (y + 5).toFixed(2) +
        '" text-anchor="middle" fill="' + color + '" font-size="13" font-weight="750">' +
        escapeHtml(label + " " + percent(value)) + '</text></g>';
    }
    return "";
  }

  function renderChart(data) {
    points = Array.isArray(data.points) ? data.points.slice().sort(function (a, b) { return Number(a.ts) - Number(b.ts); }) : [];
    activeTipIndex = -1;
    tooltip.hidden = true;
    var end = numberOrNull(data.generated_at) || Math.floor(Date.now() / 1000);
    var start = end - (RANGE_SECONDS[range] || RANGE_SECONDS.day);
    var parts = [
      '<title>Codex 每周额度余量与变化时刻</title>',
      '<desc>折线展示剩余额度；强调节点表示周额度发生变化，垂直引导线连接到对应的准确时间。</desc>',
      '<rect x="0" y="0" width="' + WIDTH + '" height="' + HEIGHT + '" fill="#fbfcfe"></rect>',
      '<rect x="' + MARGIN.left + '" y="' + yAt(20).toFixed(2) + '" width="' + PLOT_W +
        '" height="' + (HEIGHT - MARGIN.bottom - yAt(20)).toFixed(2) + '" fill="#fff3f4"></rect>',
      '<text x="' + (MARGIN.left + 10) + '" y="' + (yAt(20) + 18).toFixed(2) +
        '" fill="#b43b52" font-size="12" font-weight="700">低额度注意区 · 20% 以下</text>'
    ];
    [0, 25, 50, 75, 100].forEach(function (value) {
      var y = yAt(value).toFixed(2);
      parts.push('<line class="codex-grid-line" x1="' + MARGIN.left + '" y1="' + y + '" x2="' + (WIDTH - MARGIN.right) +
        '" y2="' + y + '" stroke="#dce3ec" stroke-width="1"></line>');
      parts.push('<text class="codex-axis-label y" x="' + (MARGIN.left - 14) + '" y="' + (Number(y) + 5) +
        '" text-anchor="end" fill="#526176" font-size="13" font-weight="650">' + value + '%</text>');
    });
    for (var i = 0; i < 5; i++) {
      var tickTs = start + (end - start) * i / 4;
      var x = xAt(tickTs, start, end).toFixed(2);
      parts.push('<line x1="' + x + '" y1="' + MARGIN.top + '" x2="' + x + '" y2="' + (HEIGHT - MARGIN.bottom) +
        '" stroke="#edf0f5" stroke-width="1"></line>');
      parts.push('<line class="codex-tick" x1="' + x + '" y1="' + (HEIGHT - MARGIN.bottom) + '" x2="' + x +
        '" y2="' + (HEIGHT - MARGIN.bottom + 7) + '" stroke="#8291a7" stroke-width="1.5"></line>');
      parts.push('<text class="codex-axis-label x" x="' + x + '" y="' + (HEIGHT - 16) +
        '" text-anchor="middle" fill="#526176" font-size="13" font-weight="650">' + escapeHtml(xLabel(tickTs)) + '</text>');
    }
    parts.push('<line class="codex-axis-baseline" x1="' + MARGIN.left + '" y1="' + (HEIGHT - MARGIN.bottom) +
      '" x2="' + (WIDTH - MARGIN.right) + '" y2="' + (HEIGHT - MARGIN.bottom) + '"></line>');
    var showFiveHour = Boolean(data.windows && data.windows.five_hour && data.windows.five_hour.available);
    var fivePath = showFiveHour ? pathFor("five_hour_remaining", start, end) : "";
    var weekPath = pathFor("weekly_remaining", start, end);
    var eventLayer = renderWeeklyEventLayer(start, end);
    parts.push(eventLayer.guides);
    if (fivePath) parts.push('<path class="codex-series-line series-five" d="' + fivePath +
      '" fill="none" stroke="#7467e8" stroke-width="3" stroke-dasharray="9 7" stroke-linecap="round" stroke-linejoin="round"></path>');
    if (weekPath) parts.push('<path class="codex-series-line series-week" d="' + weekPath +
      '" fill="none" stroke="#087f83" stroke-width="4.25" stroke-linecap="round" stroke-linejoin="round"></path>');
    if (showFiveHour) parts.push(circlesFor("five_hour_remaining", "series-five", start, end));
    parts.push(eventLayer.nodes);
    if (showFiveHour) parts.push(latestLabel("five_hour_remaining", "5H", "#7467e8", start, end, -18));
    parts.push(latestLabel("weekly_remaining", "周额度", "#087f83", start, end, 18));
    parts.push('<g id="codex-crosshair" class="codex-crosshair" visibility="hidden"><line x1="0" y1="' + MARGIN.top +
      '" x2="0" y2="' + (HEIGHT - MARGIN.bottom) + '" stroke="#26364d" stroke-width="1.5" stroke-dasharray="5 4"></line>' +
      '<circle class="series-five" cx="0" cy="0" r="6" fill="#ffffff" stroke="#7467e8" stroke-width="3" visibility="hidden"></circle>' +
      '<circle class="series-week" cx="0" cy="0" r="6" fill="#ffffff" stroke="#087f83" stroke-width="3" visibility="hidden"></circle></g>');
    svg.innerHTML = parts.join("");
    svg.__chartDomain = {start: start, end: end};
    if (empty) empty.hidden = points.length > 0;
    var summary = selectOne('[data-role="chart-summary"]');
    if (summary) summary.textContent = points.length ? points.length + " 个采样 · " + weeklyEvents.length +
      " 次周额度变化 · 标注 " + weeklyGuides.length + " 个时刻 · " + (RANGE_LABELS[range] || "") : "当前范围暂无数据";
  }

  function renderRecords(data) {
    var host = selectOne('[data-role="records-body"]');
    var count = selectOne('[data-role="records-count"]');
    if (!host) return;
    var rows = Array.isArray(data.points) ? data.points.slice(-12).reverse() : [];
    var weekOnly = root.classList.contains("is-week-only");
    if (count) count.textContent = rows.length + " 条";
    if (!rows.length) {
      host.innerHTML = '<tr><td colspan="' + (weekOnly ? 3 : 5) + '" class="empty">当前范围暂无采集数据</td></tr>';
      return;
    }
    host.innerHTML = rows.map(function (row) {
      var five = numberOrNull(row.five_hour_remaining);
      var week = numberOrNull(row.weekly_remaining);
      return '<tr><td><time>' + escapeHtml(dateTime(row.ts, true)) + '</time></td>' +
        '<td><strong class="record-value week">' + escapeHtml(percent(week)) + '</strong></td>' +
        '<td>' + escapeHtml(dateTime(row.weekly_resets_at, false)) + '</td>' +
        '<td data-col="five-hour"><strong class="record-value five">' + escapeHtml(percent(five)) + '</strong></td>' +
        '<td data-col="five-hour">' + escapeHtml(dateTime(row.five_hour_resets_at, false)) + '</td></tr>';
    }).join("");
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

  function positionTooltip(index, event) {
    if (index < 0 || !points[index] || !svg.__chartDomain) return;
    var point = points[index];
    var x = xAt(Number(point.ts), svg.__chartDomain.start, svg.__chartDomain.end);
    var five = root.classList.contains("is-week-only") ? null : numberOrNull(point.five_hour_remaining);
    var week = numberOrNull(point.weekly_remaining);
    var weekDelta = weeklyDeltaAt(index);
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
      var tooltipHtml = '<time>' + escapeHtml(dateTime(point.ts, true)) + '</time>';
      if (five !== null) tooltipHtml += '<span><i class="series-five"></i>5 小时 <strong>' + escapeHtml(percent(five)) + '</strong></span>';
      tooltipHtml += '<span><i class="series-week"></i>周额度 <strong>' + escapeHtml(percent(week)) + '</strong></span>';
      if (weekDelta !== null) tooltipHtml += '<span class="event-change"><i></i>' +
        (Math.abs(weekDelta) >= 0.001 ? '较上次变化' : '本次采样') + '<strong>' +
        (Math.abs(weekDelta) >= 0.001 ? escapeHtml(signedPercent(weekDelta)) : '无变化') + '</strong></span>';
      tooltip.innerHTML = tooltipHtml;
    }
    tooltip.hidden = false;
    var frameRect = frame.getBoundingClientRect();
    var tooltipW = tooltip.offsetWidth || 190;
    var tooltipH = tooltip.offsetHeight || 112;
    var cursorX = event ? event.clientX - frameRect.left + frame.scrollLeft : frame.scrollLeft + frame.clientWidth / 2;
    var cursorY = event ? event.clientY - frameRect.top : frame.clientHeight / 2;
    var minLeft = frame.scrollLeft + 8;
    var maxLeft = frame.scrollLeft + frame.clientWidth - tooltipW - 8;
    var left = cursorX + 16;
    if (left > maxLeft) left = cursorX - tooltipW - 16;
    left = Math.max(minLeft, Math.min(maxLeft, left));
    var top = cursorY + 16;
    if (top + tooltipH > frame.clientHeight - 8) top = cursorY - tooltipH - 16;
    top = Math.max(8, Math.min(frame.clientHeight - tooltipH - 8, top));
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }

  function pointerMove(event) {
    if (!points.length || !svg.__chartDomain) return;
    var rect = svg.getBoundingClientRect();
    var x = (event.clientX - rect.left) / Math.max(1, rect.width) * WIDTH;
    if (x < MARGIN.left || x > WIDTH - MARGIN.right) return hideTooltip();
    var ratio = (x - MARGIN.left) / PLOT_W;
    var ts = svg.__chartDomain.start + ratio * (svg.__chartDomain.end - svg.__chartDomain.start);
    positionTooltip(nearestIndex(ts), event);
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
    renderRecords(data);
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
