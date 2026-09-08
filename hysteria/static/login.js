(function() {
  var form = document.getElementById('form-admin');
  if (!form || form.dataset.loginBound) return;
  form.dataset.loginBound = 'true';
  var password = document.getElementById('admin-password');
  var toggle = document.getElementById('login-password-toggle');
  var button = form.querySelector('.auth-submit');
  var progress = document.getElementById('login-progress');
  toggle.hidden = false;
  toggle.addEventListener('click', function() {
    var visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    toggle.textContent = visible ? '隐藏' : '显示';
    toggle.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    toggle.setAttribute('aria-pressed', String(visible));
  });
  function reset() {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.querySelector('.auth-submit-text').textContent = '登录控制台';
    progress.textContent = '';
    password.type = 'password';
    toggle.textContent = '显示';
    toggle.setAttribute('aria-label', '显示密码');
    toggle.setAttribute('aria-pressed', 'false');
  }
  form.addEventListener('submit', function(event) {
    if (button.disabled) { event.preventDefault(); return; }
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.querySelector('.auth-submit-text').textContent = '正在验证…';
    progress.textContent = '正在验证登录信息';
  });
  window.addEventListener('pageshow', reset);
})();
