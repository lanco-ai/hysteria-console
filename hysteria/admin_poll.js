(function(){
  function fmt(n){n=Math.max(0,Number(n)||0);var u=['B','KB','MB','GB','TB'],i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return n.toFixed(2)+' '+u[i];}
  function setText(el,v){ if(el && el.textContent!==v) el.textContent=v; }
  function setStyle(el,prop,v){ if(el && el.style[prop]!==v) el.style[prop]=v; }
  function setClass(el,cls,on){ if(el && el.classList.contains(cls)!==on) el.classList.toggle(cls,on); }

  var pollStatus = document.querySelector('[data-role="admin-poll-status"]');

  function setPollStatus(text, cls){
    if (!pollStatus) return;
    pollStatus.textContent = text;
    pollStatus.classList.remove('is-live', 'is-paused', 'is-error');
    if (cls) pollStatus.classList.add(cls);
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
      online: tr.querySelector('[data-role="online"]'),
      used: tr.querySelector('[data-role="used"]'),
      bar: tr.querySelector('[data-role="bar"]'),
      detail: tr.querySelector('[data-role="detail"]'),
      spark: tr.querySelector('[data-role="spark"]'),
      lastUsed: -1, lastOnline: -1, lastPercent: -1, lastSpark: '',
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
  }
  if (filterInput) filterInput.addEventListener('input', applyFilter);
  document.querySelectorAll('.filter-chips .chip').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('.filter-chips .chip').forEach(function(b){ b.classList.remove('active'); });
      btn.classList.add('active');
      activeChip = btn.dataset.filter || 'all';
      applyFilter();
    });
  });
  applyFilter();

  var timer = null;
  var inflight = false;
  async function tick(){
    if (inflight) return;
    inflight = true;
    setPollStatus('刷新中', 'is-live');
    try{
      var r=await fetch('/admin/usage.json',{credentials:'same-origin',cache:'no-store'});
      if(!r.ok){ setPollStatus('刷新失败 · '+r.status, 'is-error'); return; }
      var d=await r.json();
      if (Array.isArray(d.users) && d.users.length !== index.size) {
        setPollStatus('用户列表已变更', 'is-live');
        window.setTimeout(function(){ window.location.reload(); }, 250);
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
          setText(row.detail, (unlimited ? '不限' : u.percent.toFixed(1)+'%')+' · ↑'+fmt(u.tx)+' ↓'+fmt(u.rx));
          row.lastPercent = u.percent;
          row.percent_n = unlimited ? 0 : u.percent;
          row.lastUnlimited = unlimited;
          statusChanged = true;
        }
        if (u.spark_html && u.spark_html !== row.lastSpark) {
          if (row.spark) row.spark.innerHTML = u.spark_html;
          row.lastSpark = u.spark_html;
        }
      });
      // Re-apply filter if any status-relevant field changed (and a status
      // chip is active, so the membership might shift).
      if (statusChanged && activeChip !== 'all') applyFilter();
      setPollStatus('更新 '+stamp(), 'is-live');
    } catch(e){ setPollStatus('刷新失败', 'is-error'); }
    finally { inflight = false; }
  }
  function start(){ if (!timer) { tick(); timer = setInterval(tick, 5000); } }
  function stop(){
    if (timer) { clearInterval(timer); timer = null; }
    setPollStatus('已暂停', 'is-paused');
  }
  document.addEventListener('visibilitychange', function() {
    if (document.hidden) stop(); else start();
  });
  window.addEventListener('pagehide', stop);
  start();

  document.addEventListener('submit', function(ev){
    var f=ev.target;
    if(!f || f.tagName!=='FORM') return;
    if(f.dataset.action==='delete-user'){
      var name=(f.closest('tr')||{}).dataset && f.closest('tr').dataset.user || '';
      if(!confirm('确认删除用户 '+name+'？此操作不可撤销。')) ev.preventDefault();
    } else if(f.dataset.action==='rotate-user-token'){
      var rn=(f.closest('tr')||{}).dataset && f.closest('tr').dataset.user || '';
      if(!confirm('确认重置用户 '+rn+' 的订阅令牌？旧订阅/面板链接将立即失效。')) ev.preventDefault();
    } else if(f.dataset.action==='disable-user'){
      var dn=(f.closest('tr')||{}).dataset && f.closest('tr').dataset.user || '';
      if(!confirm('确认停用用户 '+dn+'？将拒绝新连接并断开其现有会话。')) ev.preventDefault();
    } else if(f.dataset.action==='reset-all'){
      if(!confirm('确认清空全部用户本周期已用流量？')) ev.preventDefault();
    } else if(f.dataset.action==='delete-rule'){
      if(!confirm('确认删除此规则？')) ev.preventDefault();
    }
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
      btn.setAttribute('title', '已复制 ✓');
      setTimeout(function() { btn.classList.remove('copied'); btn.setAttribute('title', prev); }, 1200);
    }).catch(manualCopy);
  });
})();
