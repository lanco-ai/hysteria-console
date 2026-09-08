(function(){
  try {
    if (localStorage.getItem('hy2.sidebar-motion') === 'enabled') {
      document.documentElement.classList.add('sidebar-motion-enabled');
    }
    if (localStorage.getItem('hy2.sidebar') === 'collapsed') {
      document.documentElement.classList.add('sidebar-pre-collapsed');
    }
  } catch (e) {}
})();
