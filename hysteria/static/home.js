// Local illustrative previews; no private data is requested.
(function () {
  'use strict';
  var tabs = Array.from(document.querySelectorAll('[data-demo]'));
  var nav = document.querySelector('.site-preview-nav');
  if (!nav || !tabs.length) return;
  nav.setAttribute('role', 'tablist');
  function activate(tab, focus) {
    tabs.forEach(function (item) {
      var selected = item === tab;
      item.setAttribute('role', 'tab');
      item.setAttribute('aria-selected', String(selected));
      item.setAttribute('aria-controls', 'demo-' + item.dataset.demo);
      item.tabIndex = selected ? 0 : -1;
      var panel = document.getElementById('demo-' + item.dataset.demo);
      panel.setAttribute('role', 'tabpanel');
      panel.tabIndex = 0;
      panel.hidden = !selected;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (tab, index) {
    tab.addEventListener('click', function (event) { event.preventDefault(); activate(tab, false); });
    tab.addEventListener('keydown', function (event) {
      var target;
      if (event.key === 'ArrowRight') target = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') target = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') target = 0;
      if (event.key === 'End') target = tabs.length - 1;
      if (event.key === ' ') target = index;
      if (target !== undefined) { event.preventDefault(); activate(tabs[target], true); }
    });
  });
  activate(tabs.find(function (tab) { return tab.hash === location.hash; }) || tabs[0], false);
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) { entry.target.classList.add('site-reveal'); observer.unobserve(entry.target); }
    });
  }, { threshold: 0.1 });
  document.querySelectorAll('.site-feature, .site-console').forEach(function (element) { observer.observe(element); });
  window.addEventListener('pagehide', function () { observer.disconnect(); });
})();
