(function(){
  var editor = document.getElementById('configEditor');
  if (!editor || editor.dataset.editorBound) return;
  editor.dataset.editorBound = 'true';
  var errorDiv = document.getElementById('jsonError');
  function showError(msg) { errorDiv.textContent=msg; errorDiv.classList.add('visible'); editor.classList.add('invalid'); editor.setAttribute('aria-invalid', 'true'); }
  function clearError() { errorDiv.textContent=''; errorDiv.classList.remove('visible'); editor.classList.remove('invalid'); editor.removeAttribute('aria-invalid'); }
  function validateJson() {
    try { JSON.parse(editor.value); clearError(); return true; }
    catch(e) { showError('JSON 语法错误: ' + e.message); return false; }
  }
  document.getElementById('cfgFormat').addEventListener('click', function() {
    try { editor.value = JSON.stringify(JSON.parse(editor.value), null, 2); clearError(); }
    catch(e) { showError('JSON 语法错误: ' + e.message); }
  });
  document.getElementById('cfgCollapse').addEventListener('click', function() {
    try {
      var obj = JSON.parse(editor.value);
      var isCompact = !editor.value.includes('\n');
      editor.value = isCompact ? JSON.stringify(obj, null, 2) : JSON.stringify(obj);
    } catch(e) {}
  });
  var allowFocusExit = false;
  editor.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') { allowFocusExit = true; return; }
    if (e.key !== 'Tab') { allowFocusExit = false; return; }
    if (e.shiftKey || allowFocusExit) { allowFocusExit = false; return; }
    e.preventDefault();
    var s=this.selectionStart, t=this.selectionEnd;
    this.value = this.value.substring(0,s) + '  ' + this.value.substring(t);
    this.selectionStart = this.selectionEnd = s + 2;
  });
  var validateTimer;
  editor.addEventListener('input', function() {
    clearTimeout(validateTimer);
    validateTimer = setTimeout(validateJson, 500);
  });
  document.getElementById('configForm').addEventListener('submit', function(e) {
    if (!validateJson()) { e.preventDefault(); editor.focus(); }
  });
})();
