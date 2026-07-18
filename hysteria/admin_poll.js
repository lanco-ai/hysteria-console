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
        var row = index.get(u.user);
        if (!row) return;
        if (u.online !== row.lastOnline) { setText(row.online, String(u.online)); row.lastOnline = u.online; }
        if (u.online !== row.online_n) { row.online_n = u.online; statusChanged = true; }
        if (u.used !== row.lastUsed) { setText(row.used, fmt(u.used)); row.lastUsed = u.used; }
        var unlimited = Number(u.total) <= 0;
        if (u.percent !== row.lastPercent || unlimited !== row.lastUnlimited) {
          setStyle(row.bar, 'width', unlimited ? '0%' : u.percent.toFixed(1)+'%');
          setClass(row.bar, 'danger', !unlimited && u.percent >= 90);
          setClass(row.bar, 'unlimited', unlimited);
          if (row.bar) {
            row.bar.setAttribute('aria-valuenow', unlimited ? '0' : u.percent.toFixed(1));
            row.bar.setAttribute('aria-valuetext', unlimited ? '不限' : u.percent.toFixed(1)+'%');
          }
          setText(row.detail, (unlimited ? '不限' : u.percent.toFixed(1)+'%')+' · ↑'+fmt(u.tx)+' ↓'+fmt(u.rx));
          row.lastPercent = u.percent;
          row.percent_n = unlimited ? 0 : u.percent;
          row.lastUnlimited = unlimited;
          statusChanged = true;
        }
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
  var pendingUserAction = null;
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
    var actionBtn = ev.target.closest('.user-action');
    if (actionBtn) { pendingUserAction = actionBtn; return; }
    if (ev.target.closest('[data-dialog-close]')) { ev.preventDefault(); closeEditDialog(); }
  });
  if (editDialog) editDialog.addEventListener('click', function(ev){
    if (ev.target === editDialog) closeEditDialog();
  });
  if (editDialog) editDialog.addEventListener('cancel', function(ev){
    ev.preventDefault();
    closeEditDialog();
  });

  document.addEventListener('submit', function(ev){
    var f=ev.target;
    if(!f || f.tagName!=='FORM') return;
    var submitter=ev.submitter || pendingUserAction;
    pendingUserAction=null;
    var row=f.closest('tr');
    var name=(submitter && (submitter.dataset.user || submitter.value)) ||
      f.dataset.user || (row && row.dataset.user) || '';
    var action=(submitter && submitter.dataset.action) || f.dataset.action || '';
    if(!confirmAdminAction(action, name)) ev.preventDefault();
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
})();
