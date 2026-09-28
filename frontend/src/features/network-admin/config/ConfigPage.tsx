import { useEffect, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { CONFIG_ENDPOINT, parseConfig, saveConfig } from './requests';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) { if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>; return <div className="card"><div className="err" role="alert">模板加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>; }

export function ConfigPanel({ active = true }: { active?: boolean }) {
  const config = useReadResource(CONFIG_ENDPOINT, { validate: parseConfig });
  const [draft, setDraft] = useState('');
  const [revision, setRevision] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (active && config.status === 'success' && !dirty) config.retry();
  }, [active]);
  useEffect(() => { if (config.status === 'success' && !dirty) { setDraft(JSON.stringify(config.data.config, null, 2)); setRevision(config.data.revision); } }, [config.status, config.data, dirty]);
  const format = () => { try { setDraft(JSON.stringify(JSON.parse(draft), null, 2)); setMessage('已格式化 JSON'); } catch { setMessage('JSON 格式无效，请先修正后再格式化'); } };
  const save = async () => { setBusy(true); setMessage('正在保存…'); const controller = new AbortController(); try { const result = await saveConfig({ config_json: draft, template_revision: revision }, controller.signal); if (result.ok) { setRevision(result.revision); setDirty(false); setMessage('模板已保存；用户下次拉取订阅时生效'); } else if (result.error === 'revision_conflict') setMessage('模板已被其他操作更新；草稿保留，请刷新后合并'); else setMessage(result.code === 'invalid_json' ? 'JSON 格式错误' : '模板结构无效，服务器未修改'); } catch (error) { setMessage(error instanceof Error ? error.message : '保存失败，请刷新核对'); } finally { setBusy(false); } };
  return <>
    {config.status === 'error' ? <ErrorState error={config.error} retry={config.retry}/> : null}
    {config.status === 'loading' ? <LoadingState label="正在加载模板…"/> : null}
    {config.status === 'success' ? <div className="admin-page settings-page">
      {message ? <div className="flash" role="status">{message}</div> : null}
      <section className="code-panel"><div className="code-panel-header"><div><div className="code-panel-title">模板 JSON</div><div className="small faint">下次拉取订阅生效 · 保存校验结构与版本 · 用户凭证由服务端注入</div></div><div className="code-panel-actions"><button className="btn btn-ghost btn-sm" type="button" onClick={format} disabled={busy}>格式化 JSON</button></div></div><div className="code-panel-body"><label className="sr-only" htmlFor="config-editor">订阅模板 JSON</label><textarea id="config-editor" className="code-area code-tall" spellCheck={false} value={draft} onChange={event => { setDraft(event.target.value); setDirty(true); }} /> <div className="row mt-md"><button className="btn btn-primary" type="button" onClick={() => void save()} disabled={busy || !dirty}>保存订阅模板</button><button className="btn secondary" type="button" onClick={() => { setDraft(JSON.stringify(config.data.config, null, 2)); setRevision(config.data.revision); setDirty(false); setMessage('已恢复最新版本'); }} disabled={busy || !dirty}>放弃草稿</button></div></div></section>
    </div> : null}
  </>;
}

export function ConfigPage({ publicHost }: { publicHost: string }) {
  return <AdminShell active="config" pageTitle="模板与路由" subtitle={publicHost}><ConfigPanel/></AdminShell>;
}
