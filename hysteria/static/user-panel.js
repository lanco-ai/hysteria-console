(function() {
  var root = document.querySelector('.user-panel');
  if (!root || root.dataset.interactionsBound) return;
  root.dataset.interactionsBound = 'true';
  var profileOptions = document.querySelectorAll('[data-profile-option]');
  var profileBadge = document.getElementById('profile-selected-badge');
  var profileTitle = document.getElementById('profile-selected-title');
  var profileDesc = document.getElementById('profile-selected-desc');
  var profileCopy = document.getElementById('profile-copy');
  var profileOpen = document.getElementById('profile-open');
  var profileShowQr = document.getElementById('profile-show-qr');
  var profileQrPanel = document.getElementById('profile-qr-panel');
  var profileQrImage = document.getElementById('profile-qr-image');
  var profileQrStatus = document.getElementById('profile-qr-status');
  var profileSubUrl = document.getElementById('sub');
  function setQrStatus(text) { if (profileQrStatus) profileQrStatus.textContent = text; }
  function selectProfile(option) {
    if (!option) return;
    profileOptions.forEach(function(item) {
      var selected = item === option;
      item.classList.toggle('selected', selected);
      item.setAttribute('aria-current', selected ? 'true' : 'false');
    });
    var label = option.getAttribute('data-profile-label') || '';
    var desc = option.getAttribute('data-profile-desc') || '';
    var url = option.getAttribute('data-profile-url') || option.href || '';
    var qr = option.getAttribute('data-profile-qr') || '';
    if (profileBadge) profileBadge.textContent = label;
    if (profileTitle) profileTitle.textContent = label;
    if (profileDesc) profileDesc.textContent = desc;
    // Visible subscription URL text must mirror the active profile so the
    // displayed value and the copied value stay in sync after every switch.
    if (profileSubUrl) profileSubUrl.textContent = url;
    if (profileCopy) profileCopy.setAttribute('data-copy', url);
    if (profileOpen) profileOpen.setAttribute('href', url);
    if (profileShowQr) profileShowQr.setAttribute('data-qr', qr);
    if (profileQrImage) profileQrImage.setAttribute('alt', label + '订阅二维码');
    if (profileQrPanel && !profileQrPanel.hidden && profileQrImage) {
      setQrStatus('二维码生成中…');
      profileQrImage.src = qr;
    } else if (profileQrImage) {
      profileQrImage.removeAttribute('src');
    }
  }
  if (profileQrImage) {
    profileQrImage.addEventListener('load', function() { setQrStatus('二维码已生成，可在另一台设备上扫码导入。'); });
    profileQrImage.addEventListener('error', function() {
      setQrStatus('二维码暂不可用，请使用“复制当前模式链接”导入。');
    });
  }
  function flashCopied(btn) {
    var label = btn.querySelector('span');
    var prev = label ? label.textContent : '';
    if (label) label.textContent = '已复制 ✓';
    btn.disabled = true;
    setTimeout(function() { if (label) label.textContent = prev; btn.disabled = false; }, 1400);
  }
  document.addEventListener('click', function(ev) {
    var option = ev.target.closest ? ev.target.closest('[data-profile-option]') : null;
    if (option) { ev.preventDefault(); selectProfile(option); return; }
    var qrButton = ev.target.closest ? ev.target.closest('#profile-show-qr') : null;
    if (qrButton && profileQrPanel && profileQrImage) {
      ev.preventDefault();
      var opening = profileQrPanel.hidden;
      profileQrPanel.hidden = !opening;
      qrButton.setAttribute('aria-expanded', opening ? 'true' : 'false');
      var qrLabel = qrButton.querySelector('span');
      if (qrLabel) qrLabel.textContent = opening ? '隐藏二维码' : '显示二维码';
      if (opening) {
        setQrStatus('二维码生成中…');
        profileQrImage.src = qrButton.getAttribute('data-qr') || '';
      } else {
        profileQrImage.removeAttribute('src');
        setQrStatus('在另一台设备上用客户端扫码导入；二维码仅在这里按需生成。');
      }
      return;
    }
    var btn = ev.target.closest ? ev.target.closest('[data-copy]') : null;
    if (!btn) return;
    var text = btn.getAttribute('data-copy');
    function manualCopy() { if (window.prompt) window.prompt('自动复制不可用，请手动复制下面的链接', text); }
    if (!navigator.clipboard) { manualCopy(); return; }
    navigator.clipboard.writeText(text).then(function() { flashCopied(btn); })
      .catch(manualCopy);
  });
  document.addEventListener('submit', function(ev) {
    var f = ev.target;
    if (f && f.dataset && f.dataset.action === 'rotate-token') {
      if (!confirm('确认重置订阅 Token？旧链接将立即失效。')) ev.preventDefault();
    }
  });

})();
