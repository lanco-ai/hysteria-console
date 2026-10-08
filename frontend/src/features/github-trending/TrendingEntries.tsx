import { useState } from 'react';
import { Icon } from '../../shared/icons';

export type Period = 'weekly' | 'daily';
export type TrendingItem = {
  source_rank: number;
  full_name: string;
  html_url: string;
  description: string | null;
  language: string | null;
  stars_total: number | null;
  stars_period: number | null;
  forks_count: number | null;
};

export function validCount(value: number | null): value is number {
  return value !== null && Number.isSafeInteger(value) && value >= 0;
}

export function count(value: number | null): string {
  return validCount(value) ? value.toLocaleString('en-US') : '—';
}

export function safeRepoUrl(item: TrendingItem): string | null {
  try {
    const url = new URL(item.html_url);
    if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.port || url.username || url.password || url.search || url.hash) return null;
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(item.full_name) || url.pathname !== `/${item.full_name}`) return null;
    return url.href;
  } catch {
    return null;
  }
}

function Avatar({ item }: { item: TrendingItem }) {
  const [failed, setFailed] = useState(false);
  const owner = item.full_name.split('/')[0] || '';
  const valid = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(owner) && !owner.includes('--');
  return <span className="trending-avatar" aria-hidden="true">
    {!failed && valid ? <img src={`/api/v1/github-trending/avatar/${owner}`} alt="" loading="lazy" onError={() => setFailed(true)}/> : owner.charAt(0).toUpperCase() || '?'}
  </span>;
}

function Progress({ value, maximum }: { value: number | null; maximum: number }) {
  if (!validCount(value)) return null;
  const ratio = maximum > 0 ? value / maximum : 0;
  return <span className="trending-progress" aria-hidden="true">
    <span className="trending-progress-fill" data-ratio={String(ratio)} style={{ width: `${Math.max(0, Math.min(100, ratio * 100))}%` }}/>
  </span>;
}

type EntryProps = {
  item: TrendingItem;
  period: Period;
  maximum: number;
  featured: boolean;
  onCopy: (item: TrendingItem) => void;
};

// Everything a visitor needs is visible at once: no expansion, no animated counters.
export function TrendingEntry({ item, period, maximum, featured, onCopy }: EntryProps) {
  const url = safeRepoUrl(item);
  const gained = period === 'weekly' ? '本周新增' : '今日新增';
  return <li className={`trending-entry${featured ? ' is-featured' : ''}`} value={item.source_rank}>
    <span className="trending-rank" aria-hidden="true">{item.source_rank}</span>
    <Avatar item={item}/>
    <div className="trending-main">
      <h2>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{item.full_name}</a> : item.full_name}</h2>
      <p>{item.description || '暂无简介'}</p>
      <div className="trending-meta">
        <span className="trending-language">{item.language || '语言未标注'}</span>
        <span>总 Star <b>{count(item.stars_total)}</b></span>
        <span>Fork <b>{count(item.forks_count)}</b></span>
      </div>
    </div>
    <div className="trending-heat">
      <strong>{count(item.stars_period)}</strong>
      <small>{gained} Star</small>
      <Progress value={item.stars_period} maximum={maximum}/>
    </div>
    {url
      ? <button className="trending-copy" type="button" aria-label={`复制链接：${item.full_name}`} title="复制链接" onClick={() => onCopy(item)}><Icon name="copy"/></button>
      : <span className="trending-copy is-unavailable">链接不可用</span>}
  </li>;
}
