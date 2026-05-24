(function(){
  function fmt(n){n=Math.max(0,Number(n)||0);var u=['B','KB','MB','GB','TB'],i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return n.toFixed(2)+' '+u[i];}
  function setText(el,v){ if(el && el.textContent!==v) el.textContent=v; }
  function setStyle(el,prop,v){ if(el && el.style[prop]!==v) el.style[prop]=v; }
  function setClass(el,cls,on){ if(el && el.classList.contains(cls)!==on) el.classList.toggle(cls,on); }

  // Build a one-shot index of rows + child cells so we don't re-query the DOM each tick.
  var index = new Map();
  document.querySelectorAll('tr[data-user]').forEach(function(tr) {
    index.set(tr.dataset.user, {
      tr: tr,
      online: tr.querySelector('[data-role="online"]'),
      used: tr.querySelector('[data-role="used"]'),
      bar: tr.querySelector('[data-role="bar"]'),
      detail: tr.querySelector('[data-role="detail"]'),
      spark: tr.querySelector('[data-role="spark"]'),
      lastUsed: -1, lastOnline: -1, lastPercent: -1, lastSpark: '',
      online_n: 0, percent_n: 0,
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

  var timer = null;
  var inflight = false;
  async function tick(){
    if (inflight) return;
    inflight = true;
    try{
      var r=await fetch('/admin/usage.json',{credentials:'same-origin',cache:'no-store'});
      if(!r.ok) return;
      var d=await r.json();
      if (d.total_used !== lastTotal) { setText(totalEl, fmt(d.total_used)); lastTotal = d.total_used; }
      var statusChanged = false;
      (d.users||[]).forEach(function(u){
        var row = index.get(u.user);
        if (!row) return;
        if (u.online !== row.lastOnline) { setText(row.online, String(u.online)); row.lastOnline = u.online; }
        if (u.online !== row.online_n) { row.online_n = u.online; statusChanged = true; }
        if (u.used !== row.lastUsed) { setText(row.used, fmt(u.used)); row.lastUsed = u.used; }
        if (u.percent !== row.lastPercent) {
          setStyle(row.bar, 'width', u.percent.toFixed(1)+'%');
          setClass(row.bar, 'danger', u.percent >= 90);
          setText(row.detail, u.percent.toFixed(1)+'% · ↑'+fmt(u.tx)+' ↓'+fmt(u.rx));
          row.lastPercent = u.percent;
          row.percent_n = u.percent;
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
    } catch(e){} finally { inflight = false; }
  }
  function start(){ if (!timer) { tick(); timer = setInterval(tick, 5000); } }
  function stop(){ if (timer) { clearInterval(timer); timer = null; } }
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
      if(!confirm('确认清空全部用户本月已用流量？')) ev.preventDefault();
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
