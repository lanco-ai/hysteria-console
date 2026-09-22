import { useEffect, useState, type FormEvent } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { useFormAction } from '../../../shared/useFormAction';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { mutateRules, RULES_ENDPOINT, parseRules, saveRules } from './requests';
import type { AdminRulePack, RulesOperationAction, RulesOperationMutation } from './types';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>;
  return <div className="card"><div className="err" role="alert">规则加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>;
}

function fields(form: HTMLFormElement): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of new FormData(form)) if (typeof value === 'string') result[key] = value;
  return result;
}

function isSystemRule(rule: string): boolean {
  const type = rule.split(',', 1)[0]?.trim();
  return type === 'RULE-SET' || type === 'GEOIP' || type === 'MATCH';
}

function ruleParts(rule: string): { type: string; pattern: string; action: string; extra: string } {
  const [type = '', pattern = '', action = '', extra = ''] = rule.split(',');
  return { type, pattern, action, extra };
}

function operationMessage(result: RulesOperationMutation): string {
  if (result.ok) {
    if (result.action === 'add') return '规则已添加；用户下次拉取订阅时生效';
    if (result.action === 'delete') return '规则已删除；用户下次拉取订阅时生效';
    return result.user ? `规则包已应用到 ${result.user}` : '规则包已应用到全局模板；用户下次拉取订阅时生效';
  }
  const messages: Record<string, string> = {
    pattern_empty: '请输入匹配值',
    invalid_rule_type: '规则类型不支持',
    invalid_pattern: '匹配值包含非法字符或过长',
    invalid_action: '动作不支持',
    invalid_extra: '附加选项不支持',
    invalid_rule_schema: '规则格式无效',
    invalid_index: '规则序号无效',
    index_out_of_range: '规则已不存在，请刷新后重试',
    invalid_rule_pack: '规则包不存在',
    invalid_rule_pack_scope: '应用范围无效',
    load_failed: '模板加载失败，服务器未修改',
  };
  if (result.error === 'revision_conflict') return '模板已被其他操作更新；请刷新后重试';
  if (result.error === 'user_not_found') return '用户不存在或已被删除';
  return messages[result.code ?? ''] ?? '规则操作失败，服务器未修改';
}

export function RulesPanel({ active = true }: { active?: boolean }) {
  const rules = useReadResource(RULES_ENDPOINT, { validate: parseRules });
  const formAction = useFormAction();
  const [draft, setDraft] = useState('');
  const [revision, setRevision] = useState('');
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState('');
  const [pack, setPack] = useState('');
  const [scope, setScope] = useState<'global' | 'user'>('global');
  const [user, setUser] = useState('');

  useEffect(() => {
    if (active && rules.status === 'success' && !dirty) rules.retry();
  }, [active]);

  useEffect(() => {
    if (rules.status === 'success' && !dirty) {
      setDraft(rules.data.rules.join('\n'));
      setRevision(rules.data.revision);
      setPack(current => current || rules.data.packs[0]?.key || '');
    }
  }, [rules.status, rules.data, dirty]);

  const runOperation = async (action: RulesOperationAction, payload: Record<string, string>, successMessage?: string) => {
    setMessage('正在提交…');
    await formAction.run(
      signal => mutateRules(action, payload, signal),
      {
        onResult: result => {
          if (result.ok) {
            if (result.revision) setRevision(result.revision);
            setDirty(false);
            setMessage(successMessage ?? operationMessage(result));
            rules.retry();
          } else {
            setMessage(operationMessage(result));
          }
        },
        onError: () => setMessage('请求失败或超时，请刷新核对'),
      },
    );
  };

  const save = async () => {
    setMessage('正在保存…');
    await formAction.run(
      signal => saveRules({ rules_raw: draft, template_revision: revision }, signal),
      {
        onResult: result => {
          if (result.ok) {
            setRevision(result.revision);
            setDirty(false);
            setMessage('路由规则已保存；用户下次拉取订阅时生效');
            rules.retry();
          } else if (result.error === 'revision_conflict') {
            setMessage('模板已被其他操作更新；草稿保留，请刷新后合并');
          } else {
            setMessage('规则格式无效，服务器未修改');
          }
        },
        onError: () => setMessage('保存失败或超时，请刷新核对'),
      },
    );
  };

  const submit = (event: FormEvent<HTMLFormElement>, action: RulesOperationAction) => {
    event.preventDefault();
    void runOperation(action, fields(event.currentTarget));
  };

  return <>
    {rules.status === 'error' ? <ErrorState error={rules.error} retry={rules.retry}/> : null}
    {rules.status === 'loading' ? <LoadingState label="正在加载规则…"/> : null}
    {rules.status === 'success' ? <div className="admin-page">
      {message ? <div className="flash" role="status">{message}</div> : null}
      <div className="rules-ops-grid">
        <section className="op-panel">
          <div className="op-panel-title">规则包</div>
          <div className="op-panel-desc">全局模板影响所有用户；单个用户会写入个人 Clash 覆盖项。</div>
          <form method="post" action="/admin/rule-pack/apply" className="op-form" onSubmit={event => submit(event, 'pack')}>
            <input type="hidden" name="template_revision" value={revision}/>
            <div className="op-form-grid">
              <div className="field"><label htmlFor="rule-pack">规则包</label><select id="rule-pack" name="pack" className="select" value={pack} onChange={event => setPack(event.target.value)} disabled={!rules.data.packs.length || formAction.busy}>{rules.data.packs.length ? rules.data.packs.map((item: AdminRulePack) => <option key={item.key} value={item.key}>{item.label} · {item.description}</option>) : <option value="">暂无规则包</option>}</select></div>
              <div className="field"><label htmlFor="rule-pack-scope">应用范围</label><select id="rule-pack-scope" name="scope" className="select" value={scope} onChange={event => { const next = event.target.value as 'global' | 'user'; setScope(next); if (next === 'global') setUser(''); }} disabled={formAction.busy}><option value="global">全局模板</option><option value="user">单个用户</option></select></div>
              <div className="field"><label htmlFor="rule-pack-user">用户</label><select id="rule-pack-user" name="user" className="select" value={user} onChange={event => setUser(event.target.value)} disabled={scope !== 'user' || formAction.busy} aria-describedby="rule-pack-user-help"><option value="">选择用户</option>{rules.data.users.map(name => <option key={name} value={name}>{name}</option>)}</select><span id="rule-pack-user-help" className="field-help" role="status">{scope === 'user' ? `${rules.data.users.length} 位用户可选` : '选择“单个用户”后可选择用户'}</span></div>
            </div>
            <div className="op-form-footer"><span className="op-footer-hint">应用后更新所选范围</span><button className="btn btn-secondary" type="submit" disabled={formAction.busy || !pack || (scope === 'user' && !user)}>应用规则包</button></div>
          </form>
        </section>

        <section className="op-panel">
          <div className="op-panel-title">添加自定义规则</div>
          <form method="post" action="/admin/rules/add" className="op-form" onSubmit={event => submit(event, 'add')}>
            <input type="hidden" name="template_revision" value={revision}/>
            <div className="op-form-grid">
              <div className="field"><label htmlFor="new-rule-type">规则类型</label><select id="new-rule-type" name="rule_type" className="select" defaultValue="DOMAIN-SUFFIX" disabled={formAction.busy}><option value="DOMAIN-SUFFIX">DOMAIN-SUFFIX（域名后缀）</option><option value="DOMAIN-KEYWORD">DOMAIN-KEYWORD（域名关键词）</option><option value="DOMAIN">DOMAIN（完整域名）</option><option value="IP-CIDR">IP-CIDR（IP 段）</option></select></div>
              <div className="field"><label htmlFor="new-rule-pattern">匹配值</label><input id="new-rule-pattern" name="pattern" required className="input" placeholder="example.com 或 10.0.0.0/8" disabled={formAction.busy}/></div>
              <div className="field"><label htmlFor="new-rule-action">动作</label><select id="new-rule-action" name="action" className="select" defaultValue="DIRECT" disabled={formAction.busy}><option value="DIRECT">直连 (DIRECT)</option><option value="🚀 节点选择">代理 (🚀 节点选择)</option><option value="REJECT">拦截 (REJECT)</option></select></div>
              <div className="field"><label htmlFor="new-rule-extra">附加选项</label><select id="new-rule-extra" name="extra" className="select" defaultValue="" disabled={formAction.busy}><option value="">无</option><option value="no-resolve">no-resolve（跳过 DNS 解析）</option></select></div>
            </div>
            <div className="op-form-footer"><span className="op-footer-hint">将插入规则列表最前</span><button className="btn btn-primary" type="submit" disabled={formAction.busy}>+ 添加规则</button></div>
          </form>
        </section>
      </div>

      <details className="rules-raw-editor">
        <summary className="rules-raw-summary"><span>直接编辑全部规则</span><span className="rules-raw-badge">高级操作</span></summary>
        <div className="rules-raw-body"><div className="rules-raw-help">每行一条规则，格式：<code>TYPE,匹配值,动作</code>。保存后同步到所有订阅模板。</div><label className="sr-only" htmlFor="rules-raw">全部路由规则</label><textarea id="rules-raw" className="rules-raw-textarea" spellCheck={false} value={draft} onChange={event => { setDraft(event.target.value); setDirty(true); }} disabled={formAction.busy}/><div className="row mt-md"><button className="btn btn-danger" type="button" onClick={() => void save()} disabled={formAction.busy || !dirty}>覆盖全部规则</button><button className="btn secondary" type="button" onClick={() => { setDraft(rules.data.rules.join('\n')); setRevision(rules.data.revision); setDirty(false); setMessage('已恢复最新版本'); }} disabled={formAction.busy || !dirty}>放弃草稿</button></div></div>
      </details>

      <section className="admin-section"><div className="admin-section-header"><h2 className="admin-section-title">当前规则列表</h2><div className="small">{rules.data.rules.length} 条</div></div><div className="admin-section-body no-pad"><div className="data-table-wrap" tabIndex={0} aria-label="路由规则，可横向滚动"><table className="data-table"><thead><tr><th>#</th><th>类型</th><th>匹配</th><th>动作</th><th>操作</th></tr></thead><tbody>{rules.data.rules.length ? rules.data.rules.map((rule, index) => { const parts = ruleParts(rule); const system = isSystemRule(rule); return <tr key={`${index}-${rule}`} className={system ? 'system-row' : undefined}><th scope="row">{index + 1}</th><td>{parts.type || '—'}</td><td className="break"><code>{parts.pattern || rule}</code></td><td>{parts.action || '—'}{parts.extra ? <span className="small"> ({parts.extra})</span> : null}</td><td>{system ? <span className="small faint">内置</span> : <form method="post" action="/admin/rules/delete" onSubmit={event => submit(event, 'delete')}><input type="hidden" name="index" value={index}/><input type="hidden" name="expected_rule" value={rule}/><input type="hidden" name="template_revision" value={revision}/><button className="btn btn-danger btn-sm" type="submit" disabled={formAction.busy}>删除</button></form>}</td></tr>; }) : <tr><td colSpan={5} className="empty">暂无规则</td></tr>}</tbody></table></div></div></section>
    </div> : null}
  </>;
}

export function RulesPage({ publicHost }: { publicHost: string }) {
  return <AdminShell active="config" pageTitle="模板与路由" subtitle={`${publicHost} · 订阅匹配顺序`} topbarExtra={<span className="badge poll-status">版本受保护</span>}><RulesPanel/></AdminShell>;
}
