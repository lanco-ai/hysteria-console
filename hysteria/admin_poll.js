(function(){
  // [hidden]{display:none!important} lives in admin.css — no runtime style
  // injection needed here.

  var fmt = window.Hy2UI.formatBytes;
  function setText(el,v){ if(el && el.textContent!==v) el.textContent=v; }
  function setStyle(el,prop,v){ if(el && el.style[prop]!==v) el.style[prop]=v; }
  function setClass(el,cls,on){ if(el && el.classList.contains(cls)!==on) el.classList.toggle(cls,on); }

  // `new URL('', base)` does not throw — it resolves to the *current page*.
  // A button with a missing formaction would therefore be rewritten to POST
  // to /admin instead of the intended endpoint, and try/catch would not
  // notice. Always require an explicit formaction.
  function setFormActionParam(btn, key, value){
    var fa = btn.getAttribute('formaction');
    if (!fa) return;
    try {
      var url = new URL(fa, window.location.href);
      url.searchParams.set(key, value);
      btn.setAttribute('formaction', url.toString());
    } catch (_) {}
  }

  var pollStatus = document.querySelector('[data-role="admin-poll-status"]');
  var pollAnnouncer = document.getElementById('admin-poll-announcer');
  var hasOverview = document.querySelector('.users-table') !== null;
  var needsReload = false;
  var REQUEST_TIMEOUT_MS = 10000;
  var POLL_BASE_MS = 30000;
  var POLL_MAX_MS = 240000;
  var RETRY_JITTER_MS = 4000;

  // Bumped when a mutation starts and again when it finishes. tick() tags
  // each overview fetch with the epoch at send time; if the epoch moved by
  // the time the response lands, the payload may predate the mutation's
  // server-side commit, so it is dropped instead of being compared against
  // rows the mutation already patched (which would trigger a phantom
  // "data changed" needsReload).
  var mutationEpoch = 0;

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
    activeController = controller;
    try {
      return await window.Hy2UI.fetchWithTimeout(url, options, REQUEST_TIMEOUT_MS, controller);
    } finally {
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
    var tickEpoch = mutationEpoch;
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
      if (tickEpoch !== mutationEpoch) {
        // A mutation overlapped this fetch; the payload may predate it.
        // Drop it — never patch, never needsReload. Polling continues.
        consecutiveFailures = 0;
        if (running) setPollStatus('更新 '+stamp(), 'is-live');
        return;
      }
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
    if (!hasOverview || running) return;
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
  if (hasOverview) start();

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
    setEditValue('landing_isp', btn.dataset.landingIsp || '');
    setEditValue('landing_region', btn.dataset.landingRegion || '');
    setEditValue('landing_note', btn.dataset.landingNote || '');
    setEditValue('landing_ip', btn.dataset.landingIp || '');
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
    if (action === 'hysteria-update-apply') return confirm('确认在后台下载、校验并更新 Hysteria？失败会自动回滚。');
    return true;
  }
  document.addEventListener('click', function(ev){
    var editBtn = ev.target.closest('.edit-user');
    if (editBtn) { ev.preventDefault(); openEditDialog(editBtn); return; }
    if (ev.target.closest('[data-dialog-close]')) { ev.preventDefault(); closeEditDialog(); }
  });

  // SubmitEvent.submitter is missing on older Safari and some embedded
  // webviews. The submit handler reads f.__pendingSubmitter as a fallback but
  // nothing used to assign it, so on those engines `action` came out empty and
  // the AJAX path was skipped in favour of a full-page POST. Record the button
  // during the capture phase, before the submit event fires.
  document.addEventListener('click', function(ev){
    var btn = ev.target.closest('button, input[type="submit"], input[type="image"]');
    if (!btn) return;
    var type = (btn.getAttribute('type') || '').toLowerCase();
    if (btn.tagName === 'BUTTON' && type && type !== 'submit') return;
    var form = btn.form || btn.closest('form');
    if (form) form.__pendingSubmitter = btn;
  }, true);

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
  // Single write path for a row's revision. tick() reads the cached
  // row.revision while the DOM carries tr.dataset.revision — patchUserRow
  // used to update only the dataset side, so after any AJAX mutation the
  // next poll saw a phantom change, forced needsReload and stopped polling.
  // Both sides (plus formaction URLs and the edit button) are now written
  // together here, keeping DOM and cache from the same source.
  function setRowRevision(row, revision) {
    var rev = String(revision || '');
    row.revision = rev;
    if (row.tr.dataset.revision !== rev) row.tr.dataset.revision = rev;
    row.tr.querySelectorAll('.user-action').forEach(function (btn) {
      setFormActionParam(btn, 'revision', rev);
    });
    var editBtn = row.tr.querySelector('.edit-user');
    if (editBtn) editBtn.dataset.userRevision = rev;
  }

  function patchUserRow(u) {
    var row = index.get(u.user);
    if (!row) return false;
    var changed = false;
    var allBtns = row.tr.querySelectorAll('.user-action');

    // --- revision sync (all action buttons including toggle) ---
    if (u.revision && String(u.revision) !== row.revision) {
      setRowRevision(row, u.revision);
      changed = true;
    }

    // --- edit button data-user-revision ---
    // setRowRevision already synced it when the revision changed; this branch
    // only fires when the payload carries an empty/absent revision.
    var editBtn = row.tr.querySelector('.edit-user');
    if (!u.revision && editBtn && editBtn.dataset.userRevision !== '') {
      editBtn.dataset.userRevision = '';
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
        setFormActionParam(btn, 'desired', 'enabled');
      } else {
        if (btn.textContent !== '暂停') { btn.textContent = '暂停'; changed = true; }
        if (act !== 'disable-user') { btn.dataset.action = 'disable-user'; changed = true; }
        if (btn.title !== '临时停用：拒绝新连接并断开现有会话，不删除用户') {
          btn.title = '临时停用：拒绝新连接并断开现有会话，不删除用户';
        }
        setFormActionParam(btn, 'desired', 'disabled');
      }
    });

    var badge = row.tr.querySelector('[data-role="disabled-badge"]');
    if (badge) {
      var shouldShow = !!u.disabled;
      var isHidden = badge.hasAttribute('hidden');
      if (shouldShow && isHidden) { badge.removeAttribute('hidden'); changed = true; }
      if (!shouldShow && !isHidden) { badge.setAttribute('hidden', ''); changed = true; }
      // Defence in depth: never depend on [hidden] winning the cascade.
      var wantDisplay = shouldShow ? '' : 'none';
      if (badge.style.display !== wantDisplay) badge.style.display = wantDisplay;
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

  // After a successful mutation the server includes the reload-pending
  // marker state. pending=false means the change is already live; pending=true
  // means the proxy reload is still being applied by the background worker,
  // so we poll the tiny read-only marker endpoint until it clears.
  var RELOAD_STATUS_URL = '/admin/reload-status.json';
  var RELOAD_POLL_FIRST_MS = 600;
  var RELOAD_POLL_NEXT_MS = 850;
  var RELOAD_POLL_MAX_MS = 9000;
  var reloadWatch = null;

  function clearReloadWatch(){
    if (reloadWatch && reloadWatch.timer) clearTimeout(reloadWatch.timer);
    reloadWatch = null;
  }

  // Briefly show a transient outcome in the poll status pill, then hand the
  // pill back to the regular overview poller.
  function flashPollStatus(text, cls, holdMs){
    setPollStatus(text, cls);
    announce(text);
    setTimeout(function(){
      if (running && !reloadWatch) setPollStatus('更新 '+stamp(), 'is-live');
    }, holdMs || 1300);
  }

  function watchReloadStatus(){
    clearReloadWatch();
    var started = Date.now();
    reloadWatch = { timer: null };
    var watch = reloadWatch;
    function done(){
      if (reloadWatch !== watch) return;
      clearReloadWatch();
      flashPollStatus('已生效', 'is-live');
    }
    function overdue(){
      if (reloadWatch !== watch) return;
      clearReloadWatch();
      // The user-state change itself already committed; only the proxy
      // reload is still catching up. This is not a failure.
      setPollStatus('后台仍在生效，请稍后确认', 'is-paused');
      announce('用户状态已保存，代理配置仍在后台生效');
    }
    function poll(){
      if (reloadWatch !== watch) return;
      if (Date.now() - started > RELOAD_POLL_MAX_MS) { overdue(); return; }
      fetchWithTimeout(RELOAD_STATUS_URL, { credentials: 'same-origin', cache: 'no-store' })
        .then(function(r){
          if (r.status === 401 || (r.redirected && new URL(r.url).pathname === '/login')) {
            stop();
            setPollStatus('登录已失效 · 点此登录', 'is-error');
            announce('登录已失效，请重新登录');
            if (pollStatus) pollStatus.dataset.action = 'login';
            clearReloadWatch();
            return null;
          }
          if (!r.ok) throw new Error('reload-status ' + r.status);
          return r.json();
        })
        .then(function(d){
          if (!d || reloadWatch !== watch) return;
          if (d.ok && !d.pending) { done(); return; }
          watch.timer = setTimeout(poll, RELOAD_POLL_NEXT_MS);
        })
        .catch(function(){
          if (reloadWatch !== watch) return;
          watch.timer = setTimeout(poll, RELOAD_POLL_NEXT_MS);
        });
    }
    watch.timer = setTimeout(poll, RELOAD_POLL_FIRST_MS);
  }

  function reportReloadState(reload){
    if (reload && reload.pending) {
      setPollStatus('生效中…', 'is-live');
      watchReloadStatus();
    } else {
      flashPollStatus('已生效', 'is-live');
    }
  }

  var HYSTERIA_UPDATE_STATUS_URL = '/admin/hysteria-update/status.json';
  var HYSTERIA_UPDATE_POLL_FIRST_MS = 600;
  var HYSTERIA_UPDATE_POLL_NEXT_MS = 1500;
  var HYSTERIA_UPDATE_POLL_MAX_MS = 120000;
  var hysteriaUpdateWatch = null;

  function clearHysteriaUpdateWatch(){
    if (hysteriaUpdateWatch && hysteriaUpdateWatch.timer) {
      clearTimeout(hysteriaUpdateWatch.timer);
    }
    hysteriaUpdateWatch = null;
  }

  function finishHysteriaUpdateWatch(data){
    clearHysteriaUpdateWatch();
    var status = data && data.status ? String(data.status) : 'failed';
    if (status === 'done') {
      flashPollStatus('Hysteria 已更新', 'is-live', 2500);
    } else if (status === 'rolled_back') {
      setPollStatus('更新失败，已自动回滚', 'is-paused');
      announce('Hysteria 更新失败，已自动回滚');
    } else if (status === 'skipped') {
      setPollStatus('更新已跳过', 'is-paused');
      announce('Hysteria 更新已跳过');
    } else {
      setPollStatus('Hysteria 更新失败，请查看日志', 'is-error');
      announce('Hysteria 更新或回滚失败，请查看日志');
    }
  }

  function watchHysteriaUpdateStatus(){
    clearHysteriaUpdateWatch();
    var started = Date.now();
    hysteriaUpdateWatch = {timer: null};
    var watch = hysteriaUpdateWatch;
    function poll(){
      if (hysteriaUpdateWatch !== watch) return;
      if (Date.now() - started > HYSTERIA_UPDATE_POLL_MAX_MS) {
        clearHysteriaUpdateWatch();
        setPollStatus('后台更新仍在进行，请稍后确认', 'is-paused');
        announce('Hysteria 后台更新仍在进行');
        return;
      }
      fetchWithTimeout(HYSTERIA_UPDATE_STATUS_URL, {
        credentials: 'same-origin', cache: 'no-store'
      }).then(function(r){
        if (r.status === 401) throw new Error('login_required');
        if (!r.ok) throw new Error('update_status_' + r.status);
        return r.json();
      }).then(function(data){
        if (!data || hysteriaUpdateWatch !== watch) return;
        if (!data.pending) { finishHysteriaUpdateWatch(data); return; }
        watch.timer = setTimeout(poll, HYSTERIA_UPDATE_POLL_NEXT_MS);
      }).catch(function(err){
        if (hysteriaUpdateWatch !== watch) return;
        if (err && err.message === 'login_required') {
          clearHysteriaUpdateWatch();
          setPollStatus('登录已失效 · 点此登录', 'is-error');
          announce('登录已失效，请重新登录');
          return;
        }
        watch.timer = setTimeout(poll, HYSTERIA_UPDATE_POLL_NEXT_MS);
      });
    }
    watch.timer = setTimeout(poll, HYSTERIA_UPDATE_POLL_FIRST_MS);
  }

  // Global single-flight: one mutation at a time across all users. Each
  // mutation can rewrite the whole static-access plan server-side, so
  // overlapping mutations would race on the same config; keep this global
  // rather than per-row.
  var pendingMutation = null;

  // Unified cleanup — single definition point.
  function releaseMutation() {
    pendingMutation = null;
  }

  // Actions handled over AJAX. Everything else falls through to a plain
  // form POST (progressive enhancement).
  var AJAX_ACTIONS = {
    'enable-user': true,
    'disable-user': true,
    'reset-user-usage': true,
    'refresh-user-usage': true,
    'rotate-user-token': true,
    'delete-user': true,
    'reset-all': true,
    'hysteria-update-check': true,
    'hysteria-update-apply': true,
  };

  function mutationErrorMessage(reason) {
    if      (reason === 'login_required')   return '登录已失效，请重新登录';
    else if (reason === 'conflict')         return '用户状态已变化，请刷新后重试';
    else if (reason === 'user_not_found')   return '用户不存在';
    else if (reason === 'invalid_desired')  return '请求状态无效';
    else if (reason === 'state_mismatch')   return '状态同步未完成，请稍后重试';
    else if (reason === 'update_busy')      return '另一个更新正在进行中，请稍后重试';
    else if (reason === 'update_check_failed') return '检查更新失败，请查看日志';
    else if (reason === 'update_schedule_failed') return '后台更新启动失败，请查看日志';
    return '操作失败，请重试';
  }

  // After a token rotation the row's panel/subscription links embed the old
  // token; rewrite every anchor href and copy button from the fresh payload.
  function updateRowLinks(row, links) {
    if (!row || !links) return;
    var cell = row.tr.querySelector('.link-cell');
    if (!cell) return;
    var pairs = [
      ['panel', links.panel],
      ['sub', links.sub],
    ];
    var linkRows = cell.querySelectorAll('.link-row');
    for (var i = 0; i < pairs.length && i < linkRows.length; i++) {
      var url = pairs[i][1];
      if (!url) continue;
      var anchor = linkRows[i].querySelector('a[href]');
      if (anchor) anchor.setAttribute('href', url);
      var copyBtn = linkRows[i].querySelector('.copy-link');
      if (copyBtn) copyBtn.dataset.copy = url;
    }
  }

  function removeUserRow(name) {
    var row = index.get(name);
    if (!row) return 0;
    // Remember the row's last known usage so the caller can deduct it from
    // the header total instead of waiting up to 30s for the next poll.
    var removedUsed = (typeof row.lastUsed === 'number' && row.lastUsed > 0)
      ? row.lastUsed : 0;
    index.delete(name);
    if (row.tr.parentNode) row.tr.parentNode.removeChild(row.tr);
    applyFilter();
    return removedUsed;
  }

  // Shared AJAX mutation pipeline: POST formaction?_json=1, parse the JSON
  // envelope, dispatch to the per-action success handler, and always restore
  // the buttons afterwards. Returns nothing; errors surface inline.
  function performAdminMutation(f, submitter, action, name) {
    // Global single-flight
    if (pendingMutation !== null) return;
    pendingMutation = name || action;

    // Confirm (especially important for destructive actions)
    if (f.__hy2Confirmed) {
      f.__hy2Confirmed = false;
    } else if (!confirmAdminAction(action, name)) {
      releaseMutation();
      return;
    }

    // From here on, any overview fetch already in flight predates this
    // mutation; tick() will drop its response by epoch mismatch.
    mutationEpoch++;

    // Build AJAX URL with _json=1 via URL API
    var actionUrl = (submitter && submitter.getAttribute('formaction')) || f.action || '';
    var ajaxUrl;
    try {
      ajaxUrl = new URL(actionUrl, window.location.href);
      ajaxUrl.searchParams.set('_json', '1');
      ajaxUrl = ajaxUrl.toString();
    } catch (_) {
      ajaxUrl = actionUrl + (actionUrl.indexOf('?') === -1 ? '?' : '&') + '_json=1';
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
    if (name) body.set('user', name);

    var row = submitter ? submitter.closest('tr') : null;
    var btns = row ? row.querySelectorAll('.user-action') : [];
    for (var _i = 0; _i < btns.length; _i++) btns[_i].disabled = true;
    var originalLabel = submitter ? submitter.textContent : '';
    if (submitter) {
      submitter.disabled = true;
      submitter.textContent = '处理中…';
      submitter.setAttribute('aria-busy', 'true');
    }
    if (row) row.setAttribute('aria-busy', 'true');
    var errEl = row && row.querySelector('.row-error');
    if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }

    function reportError(message) {
      if (errEl) {
        errEl.textContent = message;
        errEl.style.display = '';
      } else {
        // Actions without a row (e.g. reset-all) surface errors in the
        // status pill instead of leaving the click silent.
        setPollStatus(message, 'is-error');
        announce(message);
      }
    }

    // restoreLabel: only on failure. After a successful toggle the patched
    // row already carries the new 启用/暂停 label; restoring the stale
    // originalLabel would overwrite it.
    function restoreButtons(restoreLabel) {
      for (var _r = 0; _r < btns.length; _r++) btns[_r].disabled = false;
      if (submitter) {
        submitter.disabled = false;
        if (restoreLabel) submitter.textContent = originalLabel;
        submitter.setAttribute('aria-busy', 'false');
      }
      if (row) row.setAttribute('aria-busy', 'false');
    }

    // Always try to parse JSON regardless of HTTP status, then decide.
    fetchWithTimeout(ajaxUrl, { method: 'POST', credentials: 'same-origin',
                     headers: { 'Accept': 'application/json' }, body: body })
      .then(function (r) {
        if (r.status === 401) {
          stop();
          setPollStatus('登录已失效 · 点此登录', 'is-error');
          announce('登录已失效，请重新登录');
          if (pollStatus) pollStatus.dataset.action = 'login';
          throw new Error('login_required');
        }
        return r.json().catch(function () { return null; }).then(function (data) {
          return { ok: r.ok, status: r.status, data: data };
        });
      })
      .then(function (result) {
        var ok = result.ok;
        var data = result.data;
        if (!ok || !data || !data.ok) {
          var reason = (data && data.reason) ? String(data.reason) : null;
          reportError(mutationErrorMessage(reason));
          throw new Error(reason || 'unknown');
        }
        return handleMutationResult(action, name, data, row);
      })
      .then(function (rowRemoved) {
        // delete-user already detached the row's buttons; restoring them
        // would spin on nodes that are no longer in the document.
        if (!rowRemoved) restoreButtons(!row);
        releaseMutation();
        // Mutation finished and rows were patched; any overview response
        // still in flight may predate the commit — bump again so tick()
        // drops it instead of comparing stale revisions.
        mutationEpoch++;
      })
      .catch(function (err) {
        // If the row was removed before a later step threw, its controls
        // are detached — never restore them.
        if (!(row && row.__detached)) restoreButtons(true);
        releaseMutation();
        mutationEpoch++;
        var msg = (err && err.message) ? String(err.message) : '';
        // Errors raised after the server already confirmed success used to
        // leave the row silent; always surface something actionable.
        if (msg && msg !== 'login_required') {
          var hasInline = errEl && errEl.textContent;
          if (!hasInline) {
            var fallback = '操作失败，请重试';
            if (err && err.code === 'timeout') fallback = '请求超时，请重试';
            else if (msg.indexOf('state_mismatch') !== -1) fallback = '状态同步未完成，请稍后刷新确认';
            else if (msg.indexOf('user_row_missing_in_response') !== -1) fallback = '已提交，但该行刷新失败，请刷新页面';
            reportError(fallback);
          }
        }
        if (window.console && console.warn) console.warn('mutation failed:', action, msg);
      });
  }

  // Per-action success handling. Every handler receives the parsed JSON
  // envelope; shapes are asserted before touching the DOM so a partial or
  // unexpected payload can never half-patch a row.
  // Returns true when the row was removed from the document (delete-user),
  // so the caller skips restoring buttons that no longer exist.
  function handleMutationResult(action, name, data, row) {
    if (action === 'hysteria-update-check') {
      flashPollStatus(
        data.update_available ? '发现 Hysteria 新版本' : 'Hysteria 已是最新',
        'is-live', 2200
      );
      return false;
    }

    if (action === 'hysteria-update-apply') {
      setPollStatus('Hysteria 后台更新中…', 'is-live');
      announce('Hysteria 更新已进入后台队列');
      watchHysteriaUpdateStatus();
      return false;
    }

    if (action === 'enable-user' || action === 'disable-user') {
      // Server confirmed success and returned the fresh row in the same
      // schema as /admin/overview.json — patch directly, no extra fetch.
      var user = data.user;
      if (!user || user.user !== name) {
        throw new Error('user_row_missing_in_response');
      }
      var expectedDisabled = (data.desired === 'disabled');
      if (!!user.disabled !== expectedDisabled) {
        throw new Error('state_mismatch');
      }
      patchUserRow(user);
      reportReloadState(data.reload);
      return false;
    }

    if (action === 'reset-user-usage' || action === 'refresh-user-usage') {
      if (data.user && data.user.user === name) patchUserRow(data.user);
      flashPollStatus(
        action === 'reset-user-usage' ? '已清零 ' + name : '已刷新 ' + name,
        'is-live'
      );
      reportReloadState(data.reload);
      return false;
    }

    if (action === 'rotate-user-token') {
      if (data.user && data.user.user === name) patchUserRow(data.user);
      updateRowLinks(row, data.links);
      var rotatedOk = !data.flash || data.flash.indexOf('err:') !== 0;
      flashPollStatus(
        rotatedOk ? '已重置订阅令牌' : '已重置，代理回收进行中',
        rotatedOk ? 'is-live' : 'is-paused'
      );
      reportReloadState(data.reload);
      return false;
    }

    if (action === 'delete-user') {
      var removedUsed = removeUserRow(name);
      // Mark immediately: if anything below throws, the catch path must
      // know the row's controls are already detached from the document.
      if (row) row.__detached = true;
      if (totalEl && removedUsed > 0 && lastTotal > 0) {
        lastTotal = Math.max(0, lastTotal - removedUsed);
        setText(totalEl, fmt(lastTotal));
      }
      var deletedOk = !data.flash || data.flash.indexOf('err:') !== 0;
      flashPollStatus(
        deletedOk ? '已删除 ' + name : '已删除 ' + name + '，清理仍在进行',
        deletedOk ? 'is-live' : 'is-paused'
      );
      reportReloadState(data.reload);
      return true;
    }

    if (action === 'reset-all') {
      var users = Array.isArray(data.users) ? data.users : [];
      users.forEach(function (u) { patchUserRow(u); });
      var newTotal = Number(data.total_used) || 0;
      if (totalEl && newTotal !== lastTotal) { setText(totalEl, fmt(newTotal)); lastTotal = newTotal; }
      flashPollStatus('已清空全部用量', 'is-live');
      reportReloadState(data.reload);
      return false;
    }
  }

  document.addEventListener('submit', function(ev){
    var f = ev.target;
    if (ev.defaultPrevented || !f || f.tagName !== 'FORM') return;
    var name = f.dataset.user || '';
    var action = f.dataset.action || '';
    var submitter = ev.submitter || f.__pendingSubmitter || null;
    f.__pendingSubmitter = null;  // consume and clear — no stale write
    if (submitter) {
      action = submitter.dataset.action || action;
      name = name || submitter.dataset.user || submitter.value || '';
    }

    // AJAX: shared mutation actions only
    if (AJAX_ACTIONS[action]) {
      ev.preventDefault();
      performAdminMutation(f, submitter, action, name);
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
      var label = btn.querySelector('.copy-label');
      var previousLabel = label ? label.textContent : '';
      var prev = btn.getAttribute('title') || '';
      var prevLabel = btn.getAttribute('aria-label') || prev;
      if (label) label.textContent = '已复制';
      btn.setAttribute('title', '已复制 ✓');
      btn.setAttribute('aria-label', '已复制');
      announce('链接已复制');
      setTimeout(function() {
        btn.classList.remove('copied');
        btn.setAttribute('title', prev);
        btn.setAttribute('aria-label', prevLabel);
        if (label) label.textContent = previousLabel;
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
