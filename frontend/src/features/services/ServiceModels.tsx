import type { Bookmark } from './ServiceEditor';

function modelGroup(id: string): string {
  const value = id.toLowerCase();
  if (value.includes('gemini')) return 'GEMINI 模型';
  if (/claude|gpt|codex|(?:^|\/)o[134](?:-|$)/.test(value)) return 'CLAUDE 和 GPT 模型';
  if (value.includes('grok')) return 'GROK 模型';
  if (value.includes('deepseek')) return 'DEEPSEEK 模型';
  if (value.includes('qwen')) return 'QWEN 模型';
  return '其他模型';
}

export function ServiceModels({ item, onDetect }: { item: Bookmark; onDetect: () => void }) {
  const ids = [...new Set(item.model_ids || [])];
  const groups = new Map<string, string[]>();
  for (const id of ids) {
    const group = modelGroup(id);
    groups.set(group, [...(groups.get(group) || []), id]);
  }
  const timestamp = item.models_checked_at ? new Date(item.models_checked_at) : null;
  return <details className="service-api-details service-model-details">
    <summary><span>提供的模型 <small>点击展开查看</small></span><span className="service-count">{item.models_checked_at ? ids.length : '待检测'}</span></summary>
    {ids.length ? <div className="service-model-panel">
      {[...groups].map(([name, models]) => <section className="service-model-group" key={name}>
        <div className="service-model-heading"><h4>{name}</h4><span>此分组包含 {models.length} 个模型</span></div>
        <div className="service-model-tags">{models.map(id => <span key={id}>{id}</span>)}</div>
      </section>)}
      <div className="service-model-note"><span>{timestamp && !Number.isNaN(timestamp.getTime()) ? `模型检测于 ${timestamp.toLocaleString()}` : '已保存的模型列表'}</span><button type="button" className="btn service-secondary" onClick={onDetect}>更新模型</button></div>
    </div> : <div className="service-model-empty"><span>{item.models_checked_at ? '本次检测未返回可用模型。' : '尚未检测模型。填写 API Key 后获取此服务的模型列表。'}</span><button type="button" className="btn service-secondary" onClick={onDetect}>检测模型</button></div>}
  </details>;
}
