(function () {
var rulePackScope = document.getElementById('rule-pack-scope');
if (!rulePackScope || rulePackScope.dataset.rulesBound) return;
rulePackScope.dataset.rulesBound = 'true';
var rulePackUser = document.getElementById('rule-pack-user');
function syncRulePackUser() {
  if (!rulePackScope || !rulePackUser) return;
  var needsUser = rulePackScope.value === 'user';
  rulePackUser.disabled = !needsUser;
  rulePackUser.required = needsUser;
  if (!needsUser) rulePackUser.value = '';
}
if (rulePackScope) rulePackScope.addEventListener('change', syncRulePackUser);
syncRulePackUser();
document.addEventListener('submit', function(ev){
  var f = ev.target;
  if (f && f.tagName==='FORM' && f.dataset.action==='delete-rule') {
    if (!confirm('确认删除此规则？')) ev.preventDefault();
  }
});
})();
