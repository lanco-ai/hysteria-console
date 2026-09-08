(function() {
  var sb = document.getElementById('sidebar');
  var sc = document.getElementById('scrim');
  var bt = document.getElementById('sidebar-toggle');
  var cb = document.getElementById('sidebar-close');
  var collapseBtn = document.getElementById('sidebar-collapse');
  var app = document.querySelector('.app');
  var main = document.querySelector('.main');
  var skip = document.querySelector('.skip-link');
  var motionToggle = document.getElementById('sidebar-motion-toggle');
  if (!sb || !sc || !bt || !cb) return;
  if (sb.dataset.shellBound) return;
  sb.dataset.shellBound = 'true';
  function setCollapsed(collapsed) {
    collapsed = Boolean(collapsed) && !isMobile();
    sb.classList.toggle('collapsed', collapsed);
    if (app) app.classList.toggle('sidebar-collapsed', collapsed);
    document.documentElement.classList.toggle('sidebar-pre-collapsed', collapsed);
    if (collapseBtn) {
      collapseBtn.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
      collapseBtn.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
      collapseBtn.setAttribute('title', collapsed ? '展开侧边栏' : '折叠侧边栏');
    }
    try { localStorage.setItem('hy2.sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (e) {}
  }
  try {
    setCollapsed(localStorage.getItem('hy2.sidebar') === 'collapsed');
  } catch (e) {}
  requestAnimationFrame(function() {
    requestAnimationFrame(function() {
      document.documentElement.classList.remove('sidebar-pre-collapsed');
      if (app) app.classList.add('anim-ready');
    });
  });
  if (collapseBtn) collapseBtn.addEventListener('click', function() {
    setCollapsed(!sb.classList.contains('collapsed'));
  });
  if (motionToggle) {
    motionToggle.checked = document.documentElement.classList.contains('sidebar-motion-enabled');
    motionToggle.addEventListener('change', function() {
      var enabled = Boolean(motionToggle.checked);
      document.documentElement.classList.toggle('sidebar-motion-enabled', enabled);
      try {
        if (enabled) localStorage.setItem('hy2.sidebar-motion', 'enabled');
        else localStorage.removeItem('hy2.sidebar-motion');
      } catch (e) {}
    });
  }
  function isMobile() { return window.innerWidth <= 880; }
  function focusableItems() {
    return Array.prototype.slice.call(
      sb.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')
    );
  }
  function setOpen(open, restoreFocus) {
    open = Boolean(open && isMobile());
    sb.classList.toggle('open', open);
    document.body.classList.toggle('sidebar-open', open);
    bt.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (isMobile()) {
      if (open) {
        sb.removeAttribute('inert');
        if (main) main.setAttribute('inert', '');
        if (skip) skip.setAttribute('inert', '');
        var first = sb.querySelector('#sidebar-close, a, button');
        if (first) first.focus();
      } else {
        sb.setAttribute('inert', '');
        if (main) main.removeAttribute('inert');
        if (skip) skip.removeAttribute('inert');
        if (restoreFocus) bt.focus();
      }
    } else {
      sb.removeAttribute('inert');
      if (main) main.removeAttribute('inert');
      if (skip) skip.removeAttribute('inert');
    }
  }
  function close(restoreFocus) { setOpen(false, restoreFocus); }
  bt.addEventListener('click', function() { setOpen(!sb.classList.contains('open')); });
  cb.addEventListener('click', function() { close(true); });
  sc.addEventListener('click', function() { close(true); });
  sb.querySelectorAll('a').forEach(function(link) { link.addEventListener('click', function() { close(false); }); });
  document.addEventListener('keydown', function(ev) {
    if (ev.key === 'Escape' && sb.classList.contains('open')) {
      ev.preventDefault();
      close(true);
      return;
    }
    if (ev.key === 'Tab' && sb.classList.contains('open')) {
      var items = focusableItems();
      if (!items.length) {
        ev.preventDefault();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      var active = document.activeElement;
      if (ev.shiftKey && (active === first || !sb.contains(active))) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && (active === last || !sb.contains(active))) {
        ev.preventDefault();
        first.focus();
      }
    }
  });
  window.addEventListener('resize', function() {
    setOpen(sb.classList.contains('open'));
    if (isMobile()) {
      sb.classList.remove('collapsed');
      if (app) app.classList.remove('sidebar-collapsed');
    } else {
      try {
        setCollapsed(localStorage.getItem('hy2.sidebar') === 'collapsed');
      } catch (e) {}
    }
  });
  document.addEventListener('submit', function(ev) {
    var form = ev.target;
    if (ev.defaultPrevented || !form || form.tagName !== 'FORM') return;
    var message = form.getAttribute('data-confirm');
    if (message) {
      if (!window.confirm(message)) ev.preventDefault();
      else form.__hy2Confirmed = true;
    }
  });
  setOpen(false);
})();
