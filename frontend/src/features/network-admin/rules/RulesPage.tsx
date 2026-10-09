import { useEffect, useMemo, useState, type FormEvent } from 'react';
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

type RuleParts = { type: string; pattern: string; action: string; extra: string };

/** Splits TYPE,payload,policy[,option]; logical rules carry a parenthesised payload and MATCH has none. */
export function ruleParts(rule: string): RuleParts {
  const comma = rule.indexOf(',');
  const type = (comma < 0 ? rule : rule.slice(0, comma)).trim();
  const rest = comma < 0 ? '' : rule.slice(comma + 1);
  if (type === 'MATCH') return { type, pattern: '', action: rest.split(',')[0]?.trim() ?? '', extra: '' };
  if ((type === 'AND' || type === 'OR' || type === 'NOT') && rest.startsWith('(')) {
    let depth = 0;
    for (let index = 0; index < rest.length; index += 1) {
      if (rest[index] === '(') depth += 1;
      else if (rest[index] === ')' && --depth === 0) {
        const [action = '', ...extra] = rest.slice(index + 2).split(',');
        return { type, pattern: rest.slice(0, index + 1), action: action.trim(), extra: extra.join(',').trim() };
      }
    }
  }
  const [pattern = '', action = '', ...extra] = rest.split(',');
  return { type, pattern: pattern.trim(), action: action.trim(), extra: extra.join(',').trim() };
}

function typeFamily(type: string): string {
  if (type.startsWith('DOMAIN')) return 'domain';
  if (type.includes('IP-CIDR') || type === 'IP-ASN') return 'ip';
  if (type === 'GEOIP' || type === 'GEOSITE') return 'geo';
  if (type === 'RULE-SET') return 'set';
  if (type === 'AND' || type === 'OR' || type === 'NOT') return 'logic';
  if (type === 'MATCH') return 'match';
  return 'other';
}

function policyKind(action: string): string {
  if (action === 'DIRECT') return 'direct';
  if (action.startsWith('REJECT')) return 'reject';
  return 'proxy';
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
  const [view, setView] = useState<'list' | 'text'>('list');
  const [query, setQuery] = useState('');
  const [policy, setPolicy] = useState('');

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

  const parsed = useMemo(() => (rules.status === 'success' ? rules.data.rules : []).map((rule, index) => ({ rule, index, parts: ruleParts(rule), system: isSystemRule(rule) })), [rules.status, rules.data]);
  const policies = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of parsed) if (item.parts.action) counts.set(item.parts.action, (counts.get(item.parts.action) ?? 0) + 1);
    return [...counts].sort((a, b) => b[1] - a[1]);
  }, [parsed]);

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

  if (rules.status === 'error') return <ErrorState error={rules.error} retry={rules.retry}/>;
  if (rules.status !== 'success') return <LoadingState label="正在加载规则…"/>;
  const needle = query.trim().toLowerCase();
  const visible = parsed.filter(item => (!policy || item.parts.action === policy) && (!needle || item.rule.toLowerCase().includes(needle)));
  const custom = parsed.filter(item => !item.system).length;

  return <div className="admin-page rules-page">
    {message ? <div className="flash" role="status">{message}</div> : null}
    <div className="rules-layout">
      <section className="admin-section rules-list-section" aria-labelledby="rules-title">
        <div className="admin-section-header">
          <div>
            <h2 className="admin-section-title" id="rules-title">当前规则列表</h2>
            <div className="small">{parsed.length} 条 · 自定义 {custom} 条 · 自上而下匹配，先命中先生效</div>
          </div>
          <div className="segmented" role="group" aria-label="规则视图">
            <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>列表</button>
            <button type="button" aria-pressed={view === 'text'} onClick={() => setView('text')}>文本编辑{dirty ? <><span className="segmented-dot" aria-hidden="true"/><span className="sr-only">（有未保存的修改）</span></> : null}</button>
          </div>
        </div>
        {view === 'list' ? <>
          <div className="rules-toolbar">
            <label className="sr-only" htmlFor="rules-search">搜索规则</label>
            <input id="rules-search" className="rules-search" type="search" placeholder="搜索域名、IP 或策略" value={query} onChange={event => setQuery(event.target.value)}/>
            <label className="sr-only" htmlFor="rules-policy">按策略筛选</label>
            <select id="rules-policy" className="select rules-policy-filter" value={policy} onChange={event => setPolicy(event.target.value)}>
              <option value="">全部策略 · {parsed.length}</option>
              {policies.map(([name, count]) => <option key={name} value={name}>{name} · {count}</option>)}
            </select>
            {needle || policy ? <span className="small rules-match-count" role="status">显示 {visible.length} / {parsed.length}</span> : null}
          </div>
          {visible.length ? <ol className="rule-list">{visible.map(({ rule, index, parts, system }) => <li className={`rule-row${system ? ' is-system' : ''}`} key={`${index}-${rule}`}>
            <span className="rule-index">{index + 1}</span>
            <span className={`rule-type family-${typeFamily(parts.type)}`}>{parts.type || '—'}</span>
            <code className="rule-pattern">{parts.pattern || (parts.type === 'MATCH' ? '其余全部流量' : rule)}{parts.extra ? <span className="rule-extra">{parts.extra}</span> : null}</code>
            <span className={`rule-policy policy-${policyKind(parts.action)}`}>{parts.action || '—'}</span>
            <span className="rule-op">{system ? <span className="small faint">内置</span> : <form method="post" action="/admin/rules/delete" onSubmit={event => submit(event, 'delete')}>
              <input type="hidden" name="index" value={index}/>
              <input type="hidden" name="expected_rule" value={rule}/>
              <input type="hidden" name="template_revision" value={revision}/>
              <button className="btn btn-sm rule-delete" type="submit" disabled={formAction.busy} aria-label={`删除第 ${index + 1} 条规则`}>删除</button>
            </form>}</span>
          </li>)}</ol> : <div className="empty">{parsed.length ? '没有符合条件的规则' : '暂无规则'}</div>}
        </> : <div className="rules-raw-body">
          <div className="rules-raw-help">每行一条规则，格式：<code>TYPE,匹配值,动作</code>。保存后覆盖模板中的全部规则，请谨慎操作。</div>
          <label className="sr-only" htmlFor="rules-raw">全部路由规则</label>
          <textarea id="rules-raw" className="rules-raw-textarea" spellCheck={false} value={draft} onChange={event => { setDraft(event.target.value); setDirty(true); }} disabled={formAction.busy}/>
          <div className="rules-raw-actions">
            <span className="small faint">{draft ? draft.split('\n').filter(line => line.trim()).length : 0} 行{dirty ? ' · 有未保存的修改' : ''}</span>
            <button className="btn secondary" type="button" onClick={() => { setDraft(rules.data.rules.join('\n')); setRevision(rules.data.revision); setDirty(false); setMessage('已恢复最新版本'); }} disabled={formAction.busy || !dirty}>放弃草稿</button>
            <button className="btn btn-danger" type="button" onClick={() => void save()} disabled={formAction.busy || !dirty}>覆盖全部规则</button>
          </div>
        </div>}
      </section>

      <aside className="rules-side" aria-label="规则操作">
        <section className="op-panel">
          <h2 className="op-panel-title">添加自定义规则</h2>
          <form method="post" action="/admin/rules/add" className="op-form" onSubmit={event => submit(event, 'add')}>
            <input type="hidden" name="template_revision" value={revision}/>
            <div className="field"><label htmlFor="new-rule-type">规则类型</label><select id="new-rule-type" name="rule_type" className="select" defaultValue="DOMAIN-SUFFIX" disabled={formAction.busy}><option value="DOMAIN-SUFFIX">DOMAIN-SUFFIX（域名后缀）</option><option value="DOMAIN-KEYWORD">DOMAIN-KEYWORD（域名关键词）</option><option value="DOMAIN">DOMAIN（完整域名）</option><option value="IP-CIDR">IP-CIDR（IP 段）</option></select></div>
            <div className="field"><label htmlFor="new-rule-pattern">匹配值</label><input id="new-rule-pattern" name="pattern" required className="input" placeholder="example.com 或 10.0.0.0/8" disabled={formAction.busy}/></div>
            <div className="op-form-pair">
              <div className="field"><label htmlFor="new-rule-action">动作</label><select id="new-rule-action" name="action" className="select" defaultValue="DIRECT" disabled={formAction.busy}><option value="DIRECT">直连 (DIRECT)</option><option value="🚀 节点选择">代理 (🚀 节点选择)</option><option value="REJECT">拦截 (REJECT)</option></select></div>
              <div className="field"><label htmlFor="new-rule-extra">附加选项</label><select id="new-rule-extra" name="extra" className="select" defaultValue="" disabled={formAction.busy}><option value="">无</option><option value="no-resolve">no-resolve（跳过 DNS 解析）</option></select></div>
            </div>
            <button className="btn btn-primary op-submit" type="submit" disabled={formAction.busy}>+ 添加规则</button>
            <span className="op-footer-hint">新规则插入列表最前，优先匹配</span>
          </form>
        </section>

        <section className="op-panel">
          <h2 className="op-panel-title">规则包</h2>
          <div className="op-panel-desc">全局模板影响所有用户；单个用户会写入个人 Clash 覆盖项。</div>
          <form method="post" action="/admin/rule-pack/apply" className="op-form" onSubmit={event => submit(event, 'pack')}>
            <input type="hidden" name="template_revision" value={revision}/>
            <div className="field"><label htmlFor="rule-pack">规则包</label><select id="rule-pack" name="pack" className="select" value={pack} onChange={event => setPack(event.target.value)} disabled={!rules.data.packs.length || formAction.busy}>{rules.data.packs.length ? rules.data.packs.map((item: AdminRulePack) => <option key={item.key} value={item.key}>{item.label} · {item.description}</option>) : <option value="">暂无规则包</option>}</select></div>
            <div className="op-form-pair">
              <div className="field"><label htmlFor="rule-pack-scope">应用范围</label><select id="rule-pack-scope" name="scope" className="select" value={scope} onChange={event => { const next = event.target.value as 'global' | 'user'; setScope(next); if (next === 'global') setUser(''); }} disabled={formAction.busy}><option value="global">全局模板</option><option value="user">单个用户</option></select></div>
              <div className="field"><label htmlFor="rule-pack-user">用户</label><select id="rule-pack-user" name="user" className="select" value={user} onChange={event => setUser(event.target.value)} disabled={scope !== 'user' || formAction.busy} aria-describedby="rule-pack-user-help"><option value="">选择用户</option>{rules.data.users.map(name => <option key={name} value={name}>{name}</option>)}</select></div>
            </div>
            <span id="rule-pack-user-help" className="field-help" role="status">{scope === 'user' ? `${rules.data.users.length} 位用户可选` : '选择“单个用户”后可选择用户'}</span>
            <button className="btn btn-secondary op-submit" type="submit" disabled={formAction.busy || !pack || (scope === 'user' && !user)}>应用规则包</button>
          </form>
        </section>
      </aside>
    </div>
  </div>;
}

export function RulesPage({ publicHost }: { publicHost: string }) {
  return <AdminShell active="config" pageTitle="路由与出口" subtitle={`${publicHost} · 订阅匹配顺序`} topbarExtra={<span className="badge poll-status">版本受保护</span>}><RulesPanel/></AdminShell>;
}
