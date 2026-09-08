(function() {
  var panel = document.querySelector('.user-panel[data-poll-url]');
  if (!panel || panel.dataset.pollBound) return;
  panel.dataset.pollBound = 'true';
  var pollUrl = panel.dataset.pollUrl;
  var statusEl = document.querySelector('[data-role="poll-status"]');
  var statusAnnouncer = document.getElementById('panel-status-announcer');
  var fmtBytes = window.Hy2UI.formatBytes;
  function fmtQuota(n, total) {
    return Number(total) <= 0 ? '不限' : fmtBytes(n);
  }
  function setRole(role, txt) {
    var el = document.querySelector('[data-role="' + role + '"]');
    if (el && txt !== undefined) el.textContent = txt;
  }
  function setStatus(txt, cls) {
    if (!statusEl) return;
    statusEl.textContent = txt;
    statusEl.classList.remove('is-live', 'is-paused', 'is-error');
    if (cls) statusEl.classList.add(cls);
  }
  function announce(txt) {
    if (statusAnnouncer && statusAnnouncer.textContent !== txt) statusAnnouncer.textContent = txt;
  }
  function stamp() {
    return new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  var timer = null, inflight = false, running = false;
  var failures = 0, activeController = null;
  function retryDelay() {
    var exponent = Math.min(failures, 3);
    var base = Math.min(240000, 30000 * Math.pow(2, exponent));
    return Math.min(240000, base + (failures ? Math.floor(Math.random() * 4001) : 0));
  }
  function clearScheduled() {
    if (timer) { clearTimeout(timer); timer = null; }
  }
  function scheduleNext() {
    if (!running || document.hidden || timer) return;
    timer = setTimeout(function() { timer = null; tick(); }, retryDelay());
  }
  function tick() {
    if (inflight || !running || document.hidden) return;
    clearScheduled();
    inflight = true;
    setStatus('刷新中', 'is-live');
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    activeController = controller;
    var timedOut = false;
    var timeout = setTimeout(function() {
      timedOut = true;
      if (controller) controller.abort();
    }, 8000);
    fetch(pollUrl, { credentials: 'same-origin', cache: 'no-store', signal: controller ? controller.signal : undefined })
      .then(function(r) {
        if (r.status === 401) {
          stop();
          setStatus('登录已失效', 'is-error');
          announce('登录已失效，请重新登录');
          return null;
        }
        if (r.status === 403) {
          return r.json()
            .catch(function() { return { error: 'forbidden' }; })
            .then(function(payload) {
              return { accessError: payload.error || 'forbidden' };
            });
        }
        return r.ok ? r.json() : null;
      })
      .catch(function(error) {
        if (error && error.name === 'AbortError' && !timedOut) {
          return { stopped: true };
        }
        return null;
      })
      .then(function(d) {
        if (d && d.stopped) return;
        if (d && d.accessError) {
          stop();
          var accessMessages = {
            disabled: '账号已停用，请联系管理员',
            expired: '账号已到期，请联系管理员续费',
            password_change_required: '请先修改初始密码',
            forbidden: '账号状态已变化，请重新登录'
          };
          var accessMessage = accessMessages[d.accessError] || accessMessages.forbidden;
          setStatus(accessMessage, 'is-error');
          announce(accessMessage);
          return;
        }
        if (!d) {
          failures = Math.min(failures + 1, 8);
          if (statusEl && statusEl.textContent !== '登录已失效') {
            setStatus(timedOut ? '请求超时 · 稍后重试' : '更新失败 · 稍后重试', 'is-error');
            announce(timedOut ? '用量更新请求超时，系统稍后自动重试' : '用量自动更新失败，系统稍后自动重试');
          }
          return;
        }
        failures = 0;
        setRole('used', fmtBytes(d.used_bytes));
        setRole('remain', fmtQuota(d.remain_bytes, d.total_bytes));
        setRole('online', d.online);
        setRole('device-limit', Number(d.max_devices) === 0 ? '· 设备不限' : '/ ' + d.max_devices);
        var p = Number(d.percent);
        setRole('percent', Number(d.total_bytes) <= 0 ? '不限' : p.toFixed(2) + '%');
        setRole('txrx', '上传 ' + fmtBytes(d.tx_bytes) + ' · 下载 ' + fmtBytes(d.rx_bytes));
        var bar = document.querySelector('[data-role="bar"]');
        if (bar) {
          bar.style.width = Number(d.total_bytes) <= 0 ? '0%' : p.toFixed(2) + '%';
          bar.classList.toggle('danger', Number(d.total_bytes) > 0 && p >= 90);
          bar.classList.toggle('unlimited', Number(d.total_bytes) <= 0);
          bar.setAttribute('aria-valuenow', Number(d.total_bytes) <= 0 ? '0' : p.toFixed(2));
          bar.setAttribute('aria-valuetext', Number(d.total_bytes) <= 0 ? '不限' : p.toFixed(2) + '%');
        }
        setStatus('更新于 ' + stamp(), 'is-live');
      })
      .finally(function() {
        clearTimeout(timeout);
        if (activeController === controller) activeController = null;
        inflight = false;
        scheduleNext();
      });
  }
  function start() {
    if (running) return;
    running = true;
    failures = 0;
    tick();
  }
  function stop() {
    running = false;
    clearScheduled();
    if (activeController) activeController.abort();
  }
  document.addEventListener('visibilitychange', function() { if (document.hidden) { stop(); setStatus('已暂停', 'is-paused'); } else start(); });
  window.addEventListener('pagehide', stop);
  start();
})();
