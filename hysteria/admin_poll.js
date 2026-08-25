(function(){
  function fmt(n){n=Math.max(0,Number(n)||0);var u=['B','KB','MB','GB','TB'],i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return n.toFixed(2)+' '+u[i];}
  function setText(el,v){ if(el && el.textContent!==v) el.textContent=v; }
  function setStyle(el,prop,v){ if(el && el.style[prop]!==v) el.style[prop]=v; }
  function setClass(el,cls,on){ if(el && el.classList.contains(cls)!==on) el.classList.toggle(cls,on); }

  var pollStatus = document.querySelector('[data-role="admin-poll-status"]');
  var pollAnnouncer = document.getElementById('admin-poll-announcer');
  var needsReload = false;
  var REQUEST_TIMEOUT_MS = 10000;
  var POLL_BASE_MS = 30000;
  var POLL_MAX_MS = 240000;
  var RETRY_JITTER_MS = 4000;

  function setPollStatus(text, cls){
    if (!pollStatus) return;
    pollStatus.textContent = text;
    pollStatus.classList.remove('is-live', 'is-paused', 'is-error');
    if (cls) pollStatus.classList.add(cls);
  }
  function announce(text){
    if (pollAnnouncer && pollAnnouncer.textContent !== text) pollAnnouncer.textContent = text;
  }
  function stamp(){
    return new Date().toLocaleTimeString([], {hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit'});
  }

  // Build a one-shot index of rows + child cells so we don't re-query the DOM each tick.
  var index = new Map();
  document.querySelectorAll('tr[data-user]').forEach(function(tr) {
    var online_n = Number(tr.getAttribute('data-online') || 0);
    var percent_n = Number(tr.getAttribute('data-percent') || 0);
    index.set(tr.dataset.user, {
      tr: tr,
      revision: tr.dataset.revision || '',
      online: tr.querySelector('[data-role="online"]'),
      used: tr.querySelector('[data-role="used"]'),
      bar: tr.querySelector('[data-role="bar"]'),
      detail: tr.querySelector('[data-role="detail"]'),
      lastUsed: -1, lastOnline: -1, lastPercent: -1,
      online_n: online_n, percent_n: percent_n, lastUnlimited: null,
    });
  });
  var totalEl = document.getElementById('total-used');
  var lastTotal = -1;

  // Client-side filter: name substring + status chip. Pure DOM, no extra requests.
  var filterInput = document.getElementById('user-filter');
  var countEl = document.getElementById('filter-count');
  var activeChip = 'all';
  function applyFilter(){
    var q = (filterInput && filterInput.value || '').trim().toLowerCase();
    var shown = 0;
    index.forEach(function(row, name){
      var nameOk = !q || name.toLowerCase().indexOf(q) !== -1;
      var statusOk = true;
      if (activeChip === 'online') statusOk = row.online_n > 0;
      else if (activeChip === 'over') statusOk = row.percent_n >= 90;
      var visible = nameOk && statusOk;
      if (row.tr.classList.contains('hidden') !== !visible) row.tr.classList.toggle('hidden', !visible);
      if (visible) shown++;
    });
    if (countEl) countEl.textContent = shown + ' / ' + index.size + ' 个';
    var empty = document.getElementById('filter-empty');
    if (empty) empty.hidden = !(index.size > 0 && shown === 0);
  }
  if (filterInput) filterInput.addEventListener('input', applyFilter);
  document.querySelectorAll('.filter-chips .chip').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.filter-chips .chip').forEach(function(b){
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-pressed', 'true');
      activeChip = btn.dataset.filter || 'all';
      applyFilter();
    });
  });
  applyFilter();

  var timer = null;
  var inflight = false;
  var running = false;
  var consecutiveFailures = 0;
  var activeController = null;

  function retryDelay(){
    var exponent = Math.min(consecutiveFailures, 3);
    var base = Math.min(POLL_MAX_MS, POLL_BASE_MS * Math.pow(2, exponent));
    var jitter = consecutiveFailures ? Math.floor(Math.random() * (RETRY_JITTER_MS + 1)) : 0;
    return Math.min(POLL_MAX_MS, base + jitter);
  }
  function clearScheduled(){
    if (timer) { clearTimeout(timer); timer = null; }
  }
  function scheduleNext(){
    if (!running || document.hidden || timer || needsReload) return;
    timer = setTimeout(function(){
      timer = null;
      tick();
    }, retryDelay());
  }
  async function fetchWithTimeout(url, options){
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    var requestOptions = Object.assign({}, options || {});
    if (controller) requestOptions.signal = controller.signal;
    activeController = controller;
    var timeoutId = null;
    var timeoutError = new Error('request timeout');
    timeoutError.code = 'timeout';
    var timeoutPromise = new Promise(function(_resolve, reject){
      timeoutId = setTimeout(function(){
        reject(timeoutError);
        if (controller) controller.abort();
      }, REQUEST_TIMEOUT_MS);
    });
    try {
      return await Promise.race([fetch(url, requestOptions), timeoutPromise]);
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
      if (activeController === controller) activeController = null;
    }
  }
  function markPollFailure(error){
    if (!running && document.hidden) return;
    consecutiveFailures = Math.min(consecutiveFailures + 1, 8);
    var timedOut = error && error.code === 'timeout';
    setPollStatus((timedOut ? '请求超时' : '刷新失败') + ' · 点此重试', 'is-error');
    announce((timedOut ? '自动更新请求超时' : '自动更新失败') + '，可立即重试，系统也会稍后自动重试');
    if (pollStatus) pollStatus.dataset.action = 'retry';
  }
  async function tick(){
    if (inflight) return;
    clearScheduled();
    inflight = true;
    setPollStatus('刷新中', 'is-live');
    try{
      var r=await fetchWithTimeout('/admin/overview.json',{credentials:'same-origin',cache:'no-store'});
      if (r.status === 401 || (r.redirected && new URL(r.url).pathname === '/login')) {
        stop();
        setPollStatus('登录已失效 · 点此登录', 'is-error');
        announce('登录已失效，请重新登录');
        if (pollStatus) pollStatus.dataset.action = 'login';
        return;
      }
      if(!r.ok){
        var httpError = new Error('overview ' + r.status);
        httpError.code = 'http';
        throw httpError;
      }
      var d=await r.json();
      var incoming = new Set((d.users || []).map(function(u){ return u.user; }));
      var listChanged = incoming.size !== index.size;
      if (!listChanged) index.forEach(function(_row, name){ if (!incoming.has(name)) listChanged = true; });
      if (!listChanged) (d.users || []).forEach(function(u){
        var row = index.get(u.user);
        if (row && u.revision && u.revision !== row.revision) listChanged = true;
      });
      if (listChanged) {
        stop();
        needsReload = true;
        setPollStatus('数据有变化 · 点此刷新', 'is-paused');
        announce('用户列表已有变化，请在方便时刷新页面');
        if (pollStatus) pollStatus.dataset.action = 'reload';
        return;
      }
      if (d.total_used !== lastTotal) { setText(totalEl, fmt(d.total_used)); lastTotal = d.total_used; }
      var statusChanged = false;
      (d.users||[]).forEach(function(u){
        var changed = patchUserRow(u);
        if (changed) statusChanged = true;
      });
      // Re-apply filter if any status-relevant field changed (and a status
      // chip is active, so the membership might shift).
      if (statusChanged && activeChip !== 'all') applyFilter();
      consecutiveFailures = 0;
      if (pollStatus) pollStatus.removeAttribute('data-action');
      if (running) setPollStatus('更新 '+stamp(), 'is-live');
    } catch(e){
      if (!(e && e.name === 'AbortError' && !running)) markPollFailure(e);
    }
    finally {
      inflight = false;
      scheduleNext();
    }
  }
  function start(){
    if (running) return;
    running = true;
    consecutiveFailures = 0;
    if (!inflight) tick();
  }
  function stop(){
    running = false;
    clearScheduled();
    if (activeController) activeController.abort();
    setPollStatus('已暂停', 'is-paused');
  }
  document.addEventListener('visibilitychange', function() {
    if (document.hidden) stop(); else start();
  });
  window.addEventListener('pagehide', stop);
  if (pollStatus) pollStatus.addEventListener('click', function(){
    if (pollStatus.dataset.action === 'login') {
      window.location.assign('/login');
    } else if (needsReload || pollStatus.dataset.action === 'reload') {
      window.location.reload();
    } else {
      consecutiveFailures = 0;
      tick();
    }
  });
  start();

  // One edit dialog and one hidden action form replace all per-row forms. This
  // keeps the table DOM small while preserving the same POST contracts.
  var editDialog = document.getElementById('user-edit-dialog');
  var editForm = document.getElementById('user-edit-form');
  var editTitle = document.getElementById('user-edit-title');
  var editTrigger = null;
  function closeEditDialog(){
    if (!editDialog) return;
    if (typeof editDialog.close === 'function' && editDialog.open) editDialog.close();
    else editDialog.removeAttribute('open');
    if (editForm) editForm.reset();
    if (editTrigger && editTrigger.isConnected) editTrigger.focus();
    editTrigger = null;
  }
  function setEditValue(name, value){
    if (!editForm) return;
    var field = editForm.querySelector('[name="'+name+'"]');
    if (field) field.value = value == null ? '' : String(value);
  }
  function openEditDialog(btn){
    if (!editDialog || !editForm || !btn) return;
    editTrigger = btn;
    var user = btn.dataset.editUser || '';
    editForm.reset();
    setEditValue('user', user);
    setEditValue('user_revision', btn.dataset.userRevision || '');
    setEditValue('panel_password', '');
    setEditValue('password', '');
    setEditValue('max_devices', btn.dataset.maxDevices || '2');
    setEditValue('quota_gb', btn.dataset.quotaGb || '150');
    setEditValue('quota_extra_gb', btn.dataset.quotaExtraGb || '0');
    setEditValue('expires_at', btn.dataset.expiresAt || '');
    setEditValue('note', btn.dataset.note || '');
    var metered = editForm.querySelector('[name="guest"]');
    var tuic = editForm.querySelector('[name="tuic_enabled"]');
    if (metered) metered.checked = btn.dataset.metered === '1';
    if (tuic) tuic.checked = btn.dataset.tuicEnabled === '1';
    if (editTitle) editTitle.textContent = '编辑 ' + user;
    if (typeof editDialog.showModal === 'function') editDialog.showModal();
    else editDialog.setAttribute('open', '');
  }
  function confirmAdminAction(action, name){
    if (action === 'delete-user') return confirm('确认删除用户 '+name+'？此操作不可撤销。');
    if (action === 'rotate-user-token') return confirm('确认重置用户 '+name+' 的订阅令牌？旧订阅/面板链接将立即失效。');
    if (action === 'disable-user') return confirm('确认停用用户 '+name+'？将拒绝新连接并断开其现有会话。');
    if (action === 'reset-user-usage') return confirm('确认清零用户 '+name+' 的本周期用量？该流量也会从服务器本周期总计中扣除。');
    if (action === 'refresh-user-usage') return confirm('确认将用户 '+name+' 的用量归零？服务器本周期总计会保留这部分流量。');
    if (action === 'reset-all') return confirm('确认清空全部用户本周期已用流量？');
    if (action === 'delete-rule') return confirm('确认删除此规则？');
    return true;
  }
  document.addEventListener('click', function(ev){
    var editBtn = ev.target.closest('.edit-user');
    if (editBtn) { ev.preventDefault(); openEditDialog(editBtn); return; }
    if (ev.target.closest('[data-dialog-close]')) { ev.preventDefault(); closeEditDialog(); }
  });
  // === DATE VALIDATION FOR expires_at ===
  function validateExpiresAtField(el) {
    if (!el || el.type !== 'date') return true;

    el.setCustomValidity('');

    var val = String(el.value || '').trim();
    if (!val) return true;

    var invalid =
      !/^\d{4}-\d{2}-\d{2}$/.test(val) ||
      el.validity.badInput ||
      el.validity.rangeUnderflow ||
      el.validity.rangeOverflow;

    var year = Number(val.slice(0, 4));

    if (
      invalid ||
      !isFinite(year) ||
      year < 2000 ||
      year > 2099
    ) {
      el.setCustomValidity('请输入 2000-01-01 至 2099-12-31 之间的有效日期');
      return false;
    }

    return true;
  }

  function addDateValidationToForms() {
    document.querySelectorAll(
      'input[name="expires_at"], input[id*="expires-at"]'
    ).forEach(function(field) {
      if (field.dataset.expiryValidationBound === '1') return;

      field.dataset.expiryValidationBound = '1';

      field.addEventListener('input', function() {
        validateExpiresAtField(this);
      });

      field.addEventListener('change', function() {
        validateExpiresAtField(this);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addDateValidationToForms);
  } else {
    addDateValidationToForms();
  }
  if (editDialog) editDialog.addEventListener('click', function(ev){
    if (ev.target === editDialog) closeEditDialog();
  });
  if (editDialog) editDialog.addEventListener('cancel', function(ev){
    ev.preventDefault();
    closeEditDialog();
  });

  // Patch a single user row from a fresh overview payload.
  // Fully self-contained: reads current DOM state and overview payload only,
  // never references variables from the calling scope.
  // Returns false if the row is not in the index (page may have removed it).
  function patchUserRow(u) {
    var row = index.get(u.user);
    if (!row) return false;
    var changed = false;
    var allBtns = row.tr.querySelectorAll('.user-action');

    // --- revision sync (all action buttons including toggle) ---
    if (u.revision && u.revision !== row.tr.dataset.revision) {
      row.tr.dataset.revision = u.revision;
      allBtns.forEach(function (btn) {
        var fa = btn.getAttribute('formaction');
        if (!fa) return;
        try {
          var url = new URL(fa, window.location.href);
          url.searchParams.set('revision', u.revision);
          btn.setAttribute('formaction', url.toString());
        } catch (_) {}
      });
      changed = true;
    }

    // --- edit button data-user-revision ---
    var editBtn = row.tr.querySelector('.edit-user');
    if (editBtn && editBtn.dataset.userRevision !== String(u.revision || '')) {
      editBtn.dataset.userRevision = String(u.revision || '');
      changed = true;
    }

    // --- toggle button + disabled badge (always sync from server state) ---
    var isDisabled = !!u.disabled;
    allBtns.forEach(function (btn) {
      var act = btn.dataset.action;
      if (act !== 'enable-user' && act !== 'disable-user') return;
      if (isDisabled) {
        if (btn.textContent !== '启用') { btn.textContent = '启用'; changed = true; }
        if (act !== 'enable-user') { btn.dataset.action = 'enable-user'; changed = true; }
        if (btn.title !== '恢复该用户的连接权限') { btn.title = '恢复该用户的连接权限'; }
        try {
          var url2 = new URL(btn.getAttribute('formaction') || '', window.location.href);
          url2.searchParams.set('desired', 'enabled');
          btn.setAttribute('formaction', url2.toString());
        } catch (_) {}
      } else {
        if (btn.textContent !== '暂停') { btn.textContent = '暂停'; changed = true; }
        if (act !== 'disable-user') { btn.dataset.action = 'disable-user'; changed = true; }
        if (btn.title !== '临时停用：拒绝新连接并断开现有会话，不删除用户') {
          btn.title = '临时停用：拒绝新连接并断开现有会话，不删除用户';
        }
        try {
          var url3 = new URL(btn.getAttribute('formaction') || '', window.location.href);
          url3.searchParams.set('desired', 'disabled');
          btn.setAttribute('formaction', url3.toString());
        } catch (_) {}
      }
    });

    var badge = row.tr.querySelector('[data-role="disabled-badge"]');
    if (badge) {
      var shouldShow = !!u.disabled;
      var isHidden = badge.hasAttribute('hidden');
      if (shouldShow && isHidden) { badge.removeAttribute('hidden'); changed = true; }
      if (!shouldShow && !isHidden) { badge.setAttribute('hidden', ''); changed = true; }
    }

    // --- online / used / bar / detail ---
    var online_n = Number(u.online) || 0;
    if (u.online !== row.lastOnline) { setText(row.online, String(u.online)); row.lastOnline = u.online; changed = true; }
    if (online_n !== row.online_n) { row.online_n = online_n; changed = true; }
    if (u.used !== row.lastUsed) { setText(row.used, fmt(u.used)); row.lastUsed = u.used; changed = true; }
    var unlimited = Number(u.total) <= 0;
    if (u.percent !== row.lastPercent || unlimited !== row.lastUnlimited) {
      setStyle(row.bar, 'width', unlimited ? '0%' : u.percent.toFixed(1) + '%');
      setClass(row.bar, 'danger', !unlimited && u.percent >= 90);
      setClass(row.bar, 'unlimited', unlimited);
      if (row.bar) {
        row.bar.setAttribute('aria-valuenow', unlimited ? '0' : u.percent.toFixed(1));
        row.bar.setAttribute('aria-valuetext', unlimited ? '不限' : u.percent.toFixed(1) + '%');
      }
      setText(row.detail, (unlimited ? '不限' : u.percent.toFixed(1) + '%') + ' · ↑' + fmt(u.tx) + ' ↓' + fmt(u.rx));
      row.lastPercent = u.percent;
      row.percent_n = unlimited ? 0 : u.percent;
      row.lastUnlimited = unlimited;
      changed = true;
    }

    return changed;
  }

  // Refresh a single user row via the overview JSON endpoint.
  // expectedDisabled: if provided, the expected disabled state after the toggle;
  //   the promise rejects with 'state_mismatch' if the server state differs.
  // Rejects with 'user_not_found_after_toggle' if the user is absent from the overview.
  function refreshUserRow(username, expectedDisabled) {
    return fetchWithTimeout('/admin/overview.json', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) {
        if (r.status === 401) {
          stop();
          setPollStatus('登录已失效 · 点此登录', 'is-error');
          announce('登录已失效，请重新登录');
          if (pollStatus) pollStatus.dataset.action = 'login';
          return null;
        }
        if (!r.ok) throw new Error('overview ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d) { throw new Error('overview_fetch_aborted'); }
        var user = (d.users || []).find(function (u) { return u.user === username; });
        if (!user) { throw new Error('user_not_found_after_toggle'); }
        var row = index.get(username);
        if (!row) { throw new Error('user_row_not_found_after_toggle'); }
        patchUserRow(user);
        var newTotal = Number(d.total_used) || 0;
        if (totalEl && newTotal !== lastTotal) { setText(totalEl, fmt(newTotal)); lastTotal = newTotal; }
        if (expectedDisabled !== undefined) {
          var actualDisabled = !!user.disabled;
          if (actualDisabled !== expectedDisabled) {
            throw new Error('state_mismatch');
          }
        }
      });
  }

  // Global single-flight: one toggle at a time across all users.
  var pendingToggle = null;

  // Unified cleanup — single definition point.
  function releaseToggle() {
    pendingToggle = null;
  }

  document.addEventListener('submit', function(ev){
    var f = ev.target;
    if (!f || f.tagName !== 'FORM') return;
    var name = f.dataset.user || '';
    var action = '';
    var submitter = ev.submitter || f.__pendingSubmitter || null;
    f.__pendingSubmitter = null;  // consume and clear — no stale write
    if (submitter) {
      action = submitter.dataset.action || '';
      name = name || submitter.dataset.user || submitter.value || '';
    }

    // AJAX: enable-user / disable-user only
    if (submitter && (action === 'enable-user' || action === 'disable-user')) {
      ev.preventDefault();

      // Global single-flight
      if (pendingToggle !== null) return;
      pendingToggle = name;

      // Confirm (especially important for disable)
      if (!confirmAdminAction(action, name)) { releaseToggle(); return; }

      // Build AJAX URL with _json=1 via URL API
      var actionUrl = submitter.getAttribute('formaction') || f.action || '';
      var ajaxUrl;
      try {
        ajaxUrl = new URL(actionUrl, window.location.href);
        ajaxUrl.searchParams.set('_json', '1');
        ajaxUrl = ajaxUrl.toString();
      } catch (_) {
        ajaxUrl = actionUrl + '&_json=1';
      }

      // Build POST body as URLSearchParams so the server accepts it.
      // http_utils.parse_form() only handles application/x-www-form-urlencoded.
      var body = new URLSearchParams();
      if (typeof FormData !== 'undefined') {
        var fd = new FormData(f);
        fd.forEach(function (val, key) {
          if (typeof val === 'string') body.append(key, val);
        });
      }
      body.set('user', name);

      var row = submitter.closest('tr');
      var btns = row ? row.querySelectorAll('.user-action') : [];
      for (var _i = 0; _i < btns.length; _i++) btns[_i].disabled = true;
      var originalLabel = submitter.textContent;
      submitter.textContent = '处理中…';
      if (row) row.setAttribute('aria-busy', 'true');
      var errEl = row && row.querySelector('.row-error');
      if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }

      // Always try to parse JSON regardless of HTTP status, then decide.
      fetch(ajaxUrl, { method: 'POST', credentials: 'same-origin',
                       headers: { 'Accept': 'application/json' }, body: body })
        .then(function (r) {
          return r.json().catch(function () { return null; }).then(function (data) {
            return { ok: r.ok, status: r.status, data: data };
          });
        })
        .then(function (result) {
          var ok = result.ok;
          var data = result.data;
          if (!ok || !data || !data.ok) {
            var reason = (data && data.reason) ? String(data.reason) : null;
            var displayMsg;
            if      (reason === 'login_required')          displayMsg = '登录已失效，请重新登录';
            else if (reason === 'conflict')                displayMsg = '用户状态已变化，请刷新后重试';
            else if (reason === 'user_not_found')         displayMsg = '用户不存在';
            else if (reason === 'invalid_desired')        displayMsg = '请求状态无效';
            else if (reason === 'state_mismatch')         displayMsg = '状态同步未完成，请稍后重试';
            else if (reason === 'user_not_found_after_toggle') displayMsg = '用户状态刷新失败，请刷新页面';
            else if (reason === 'user_row_not_found_after_toggle') displayMsg = '当前用户行已变化，请刷新页面';
            else                                          displayMsg = '操作失败，请重试';
            if (errEl) { errEl.textContent = displayMsg; errEl.style.display = ''; }
            throw new Error(reason || 'unknown');
          }
          // Server confirmed success; verify server state matches expected
          var expectedDisabled = (data.desired === 'disabled');
          return refreshUserRow(name, expectedDisabled);
        })
        .then(function () {
          // Success: row is already patched — only restore disabled/aria
          for (var _r = 0; _r < btns.length; _r++) btns[_r].disabled = false;
          if (row) row.setAttribute('aria-busy', 'false');
          releaseToggle();
        })
        .catch(function (err) {
          // Failure: restore all buttons, keep errEl message as-is
          for (var _c = 0; _c < btns.length; _c++) btns[_c].disabled = false;
          submitter.textContent = originalLabel;
          if (row) row.setAttribute('aria-busy', 'false');
          releaseToggle();
          // Known error types already have visible errEl
          if (err && err.message &&
              err.message.indexOf('state_mismatch') === -1 &&
              err.message.indexOf('user_not_found_after_toggle') === -1 &&
              err.message.indexOf('user_row_not_found_after_toggle') === -1 &&
              err.message.indexOf('login_required') === -1 &&
              err.message.indexOf('conflict') === -1 &&
              err.message.indexOf('user_not_found') === -1 &&
              err.message.indexOf('invalid_desired') === -1) {
            if (errEl) { errEl.textContent = '操作失败，请重试'; errEl.style.display = ''; }
          }
          if (window.console && console.warn) console.warn('toggle failed:', err && err.message);
        });
      return;
    }

    // Non-AJAX: confirmation only
    if (!confirmAdminAction(action, name)) ev.preventDefault();
  });

  document.addEventListener('click', function(ev){
    var btn = ev.target.closest('.copy-link');
    if (!btn) return;
    ev.preventDefault();
    var text = btn.dataset.copy || '';
    function manualCopy(){
      if (window.prompt) window.prompt('自动复制不可用，请手动复制下面的链接', text);
    }
    if (!text) return;
    if (!navigator.clipboard) { manualCopy(); return; }
    navigator.clipboard.writeText(text).then(function() {
      btn.classList.add('copied');
      var prev = btn.getAttribute('title') || '';
      var prevLabel = btn.getAttribute('aria-label') || prev;
      btn.setAttribute('title', '已复制 ✓');
      btn.setAttribute('aria-label', '已复制');
      announce('链接已复制');
      setTimeout(function() {
        btn.classList.remove('copied');
        btn.setAttribute('title', prev);
        btn.setAttribute('aria-label', prevLabel);
      }, 1200);
    }).catch(manualCopy);
  });

  // Cost calibrator: toggle fieldset disabled state with checkbox
  var calibratorToggle = document.getElementById('calibrator-auto-enabled');
  if (calibratorToggle) {
    calibratorToggle.addEventListener('change', function() {
      var fieldset = document.querySelector('.calibrator-auto-fields');
      if (fieldset) fieldset.disabled = !calibratorToggle.checked;
    });
  }
})();
