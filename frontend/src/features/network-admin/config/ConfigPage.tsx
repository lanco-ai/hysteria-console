import { useEffect, useMemo, useRef, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { CONFIG_ENDPOINT, parseConfig, saveConfig } from './requests';

// Matches the editor's CSS line height so line numbers and jumps line up.
const LINE_HEIGHT = 20;
const REQUIRED_LISTS = ['proxies', 'proxy-groups', 'rules'];

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) { if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>; return <div className="card"><div className="err" role="alert">模板加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>; }

type DraftCheck = { ok: true; value: Record<string, unknown>; missing: string[] } | { ok: false; message: string };

function syntaxMessage(text: string, error: unknown): string {
  const message = error instanceof Error ? error.message : '';
  const lineColumn = message.match(/line (\d+) column (\d+)/);
  if (lineColumn) return `第 ${lineColumn[1]} 行第 ${lineColumn[2]} 列附近有语法错误`;
  const position = message.match(/position (\d+)/);
  if (!position) return 'JSON 语法错误';
  const before = text.slice(0, Number(position[1]));
  return `第 ${before.split('\n').length} 行第 ${before.length - before.lastIndexOf('\n')} 列附近有语法错误`;
}

function checkDraft(text: string): DraftCheck {
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return { ok: false, message: '模板顶层必须是 JSON 对象' };
    const record = value as Record<string, unknown>;
    return { ok: true, value: record, missing: REQUIRED_LISTS.filter(key => !Array.isArray(record[key])) };
  } catch (error) {
    return { ok: false, message: syntaxMessage(text, error) };
  }
}

/** Line numbers of the top-level keys in two-space formatted JSON. */
function keyLines(text: string): [string, number][] {
  const found: [string, number][] = [];
  text.split('\n').forEach((line, index) => {
    const match = line.match(/^ {2}"([^"]+)"\s*:/);
    if (match?.[1]) found.push([match[1], index + 1]);
  });
  return found;
}

const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const named = (value: unknown, key: string): string => { const item = record(value)[key]; return typeof item === 'string' ? item : ''; };

function TemplateOverview({ config, fromDraft, openRules }: { config: Record<string, unknown>; fromDraft: boolean; openRules?: (() => void) | undefined }) {
  const proxies = list(config.proxies);
  const groups = list(config['proxy-groups']);
  const rules = list(config.rules);
  const providers = Object.keys(record(config['rule-providers'])).length;
  const dns = record(config.dns);
  const nameservers = list(dns.nameserver).length;
  const types = new Map<string, number>();
  for (const proxy of proxies) { const type = named(proxy, 'type') || '未知'; types.set(type, (types.get(type) ?? 0) + 1); }
  const facts: [string, string][] = [
    ['运行模式', String(config.mode ?? '—')],
    ['混合端口', String(config['mixed-port'] ?? '—')],
    ['日志级别', String(config['log-level'] ?? '—')],
    ['DNS', Object.keys(dns).length ? `${String(dns['enhanced-mode'] ?? '标准')} · ${nameservers} 个上游` : '未配置'],
  ];
  return <aside className="template-overview" aria-labelledby="template-overview-title">
    <div className="template-overview-head">
      <h2 className="template-overview-title" id="template-overview-title">模板概览</h2>
      <span className="small faint">{fromDraft ? '随草稿实时更新' : '草稿无效，显示已保存版本'}</span>
    </div>
    <dl className="template-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <div className="template-stats">
      <div className="template-stat"><div className="template-stat-head"><strong>{proxies.length}</strong><span>个节点</span></div>{types.size ? <div className="template-stat-detail">{[...types].map(([type, count]) => count > 1 ? `${type} ×${count}` : type).join(' · ')}</div> : null}</div>
      <div className="template-stat"><div className="template-stat-head"><strong>{groups.length}</strong><span>个策略组</span></div>{groups.length ? <div className="template-chips">{groups.map((group, index) => <span className="template-chip" key={`${named(group, 'name')}-${index}`} title={named(group, 'type')}>{named(group, 'name') || '未命名'}</span>)}</div> : null}</div>
      <div className="template-stat"><div className="template-stat-head"><strong>{providers}</strong><span>个规则集</span></div></div>
      <div className="template-stat"><div className="template-stat-head"><strong>{rules.length}</strong><span>条规则</span></div>{openRules ? <button className="template-link" type="button" onClick={openRules}>在「路由规则」中管理 →</button> : null}</div>
    </div>
    <p className="small faint template-note">下次拉取订阅生效 · 保存校验结构与版本 · 用户凭证由服务端注入</p>
  </aside>;
}

export function ConfigPanel({ active = true, openRules }: { active?: boolean; openRules?: () => void }) {
  const config = useReadResource(CONFIG_ENDPOINT, { validate: parseConfig });
  const [draft, setDraft] = useState('');
  const [revision, setRevision] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const editor = useRef<HTMLTextAreaElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (active && config.status === 'success' && !dirty) config.retry();
  }, [active]);
  useEffect(() => { if (config.status === 'success' && !dirty) { setDraft(JSON.stringify(config.data.config, null, 2)); setRevision(config.data.revision); } }, [config.status, config.data, dirty]);
  const check = useMemo(() => checkDraft(draft), [draft]);
  const jumps = useMemo(() => keyLines(draft), [draft]);
  const lineCount = draft.split('\n').length;
  const format = () => { try { setDraft(JSON.stringify(JSON.parse(draft), null, 2)); setMessage('已格式化 JSON'); } catch { setMessage('JSON 格式无效，请先修正后再格式化'); } };
  const jump = (line: number) => {
    const area = editor.current;
    if (!area) return;
    const offset = draft.split('\n').slice(0, line - 1).reduce((total, text) => total + text.length + 1, 0);
    area.focus({ preventScroll: true });
    area.setSelectionRange(offset, offset);
    area.scrollTop = Math.max(0, (line - 2) * LINE_HEIGHT);
  };
  const save = async () => { setBusy(true); setMessage('正在保存…'); const controller = new AbortController(); try { const result = await saveConfig({ config_json: draft, template_revision: revision }, controller.signal); if (result.ok) { setRevision(result.revision); setDirty(false); setMessage('模板已保存；用户下次拉取订阅时生效'); } else if (result.error === 'revision_conflict') setMessage('模板已被其他操作更新；草稿保留，请刷新后合并'); else setMessage(result.code === 'invalid_json' ? 'JSON 格式错误' : '模板结构无效，服务器未修改'); } catch (error) { setMessage(error instanceof Error ? error.message : '保存失败，请刷新核对'); } finally { setBusy(false); } };

  if (config.status === 'error') return <ErrorState error={config.error} retry={config.retry}/>;
  if (config.status !== 'success') return <LoadingState label="正在加载模板…"/>;
  return <div className="admin-page template-page">
    {message ? <div className="flash" role="status">{message}</div> : null}
    <div className="template-layout">
      <section className="code-panel template-editor" aria-labelledby="template-editor-title">
        <div className="code-panel-header">
          <div className="template-editor-heading">
            <h2 className="code-panel-title" id="template-editor-title">模板 JSON</h2>
            {dirty ? <span className="badge badge-warning">未保存</span> : <span className="badge badge-neutral">已同步</span>}
          </div>
          <div className="code-panel-actions">
            <button className="btn btn-ghost btn-sm" type="button" onClick={format} disabled={busy}>格式化 JSON</button>
            <button className="btn secondary btn-sm" type="button" onClick={() => { setDraft(JSON.stringify(config.data.config, null, 2)); setRevision(config.data.revision); setDirty(false); setMessage('已恢复最新版本'); }} disabled={busy || !dirty}>放弃草稿</button>
            <button className="btn btn-primary btn-sm" type="button" onClick={() => void save()} disabled={busy || !dirty || !check.ok}>保存订阅模板</button>
          </div>
        </div>
        {jumps.length ? <div className="template-jumps" role="group" aria-label="跳转到字段">{jumps.map(([key, line]) => <button className="template-jump" type="button" key={key} onClick={() => jump(line)}>{key}</button>)}</div> : null}
        <div className="code-editor">
          <div className="code-gutter" ref={gutter} aria-hidden="true">{Array.from({ length: lineCount }, (_, index) => index + 1).join('\n')}</div>
          <label className="sr-only" htmlFor="config-editor">订阅模板 JSON</label>
          <textarea id="config-editor" ref={editor} className="code-area" wrap="off" spellCheck={false} value={draft} aria-describedby="template-status" onScroll={event => { if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop; }} onChange={event => { setDraft(event.target.value); setDirty(true); }}/>
        </div>
        <div className={`template-status${check.ok ? (check.missing.length ? ' is-warn' : ' is-ok') : ' is-error'}`} id="template-status">
          {check.ok ? (check.missing.length ? `JSON 有效，但缺少 ${check.missing.join('、')} 列表，保存会被拒绝` : `JSON 有效 · ${Object.keys(check.value).length} 个顶级字段`) : check.message}
          <span className="template-status-size">{lineCount} 行 · {draft.length.toLocaleString()} 字符</span>
        </div>
      </section>
      <TemplateOverview config={check.ok ? check.value : config.data.config} fromDraft={check.ok} openRules={openRules}/>
    </div>
  </div>;
}

export function ConfigPage({ publicHost }: { publicHost: string }) {
  return <AdminShell active="config" pageTitle="路由与出口" subtitle={publicHost}><ConfigPanel/></AdminShell>;
}
