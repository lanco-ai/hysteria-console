import { useEffect, useState, type PointerEvent } from 'react';
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
    {!failed && valid ? <img src={`/api/v1/github-trending/avatar/${owner}`} alt="" onError={() => setFailed(true)}/> : owner.charAt(0).toUpperCase() || '?'}
  </span>;
}

function Progress({ value, maximum }: { value: number | null; maximum: number }) {
  if (!validCount(value)) return <span className="trending-progress-missing">—</span>;
  const ratio = maximum > 0 ? value / maximum : 0;
  return <span className="trending-progress" aria-label={`热度比例 ${Math.round(ratio * 100)}%`}>
    <span className="trending-progress-fill" data-ratio={String(ratio)} style={{ width: `${Math.max(0, Math.min(100, ratio * 100))}%` }}/>
  </span>;
}

const animatedSnapshots = new Set<string>();

function CountUp({ value, snapshotKey }: { value: number | null; snapshotKey: string }) {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    if (!validCount(value) || value === 0 || window.matchMedia('(prefers-reduced-motion: reduce)').matches || animatedSnapshots.has(snapshotKey)) {
      setShown(value);
      return;
    }
    animatedSnapshots.add(snapshotKey);
    if (animatedSnapshots.size > 256) animatedSnapshots.delete(animatedSnapshots.values().next().value as string);
    let frame = 0;
    const started = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / 650);
      setShown(Math.round(value * (1 - (1 - progress) ** 3)));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    setShown(0);
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, snapshotKey]);
  return <>{count(shown)}</>;
}

function Actions({ item, onCopy }: { item: TrendingItem; onCopy: (item: TrendingItem) => void }) {
  const url = safeRepoUrl(item);
  return <div className="trending-actions" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    {url ? <>
      <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`在 GitHub 查看：${item.full_name}`} title="在 GitHub 查看"><Icon name="open"/></a>
      <button type="button" aria-label={`复制链接：${item.full_name}`} title="复制链接" onClick={() => onCopy(item)}><Icon name="copy"/></button>
    </> : <span>链接不可用</span>}
  </div>;
}

type EntryProps = {
  item: TrendingItem;
  period: Period;
  maximum: number;
  expanded: boolean;
  onToggle: () => void;
  onCopy: (item: TrendingItem) => void;
  snapshotKey: string;
};

function EntryFrame({ item, period, maximum, expanded, onToggle, onCopy, snapshotKey, focus }: EntryProps & { focus: boolean }) {
  const label = period === 'weekly' ? '本周 Stars' : '今日 Stars';
  const detailsId = `trending-details-${period}-${item.source_rank}`;
  const onPointerMove = (event: PointerEvent<HTMLLIElement>) => {
    if (!focus || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const rect = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty('--pointer-x', `${event.clientX - rect.left}px`);
    event.currentTarget.style.setProperty('--pointer-y', `${event.clientY - rect.top}px`);
  };
  return <li className={`trending-repo ${focus ? 'trending-top-card' : 'trending-repo-row'}${expanded ? ' is-expanded' : ''}`} value={item.source_rank} onPointerMove={onPointerMove}>
    <button className="trending-entry-toggle" type="button" aria-expanded={expanded} aria-controls={detailsId} aria-label={`${item.full_name}，${label} ${count(item.stars_period)}，${expanded ? '收起详情' : '展开详情'}`} onClick={onToggle}/>
    <span className="trending-rank" aria-hidden="true">#{item.source_rank}</span>
    <Avatar item={item}/>
    <div className="trending-repo-main"><h2>{item.full_name}</h2><p>{item.description || '暂无简介'}</p></div>
    {focus ? <>
      <div className="trending-period-stat"><small>{label}</small><strong><CountUp value={item.stars_period} snapshotKey={`${snapshotKey}:${item.full_name}`}/></strong></div>
      <Progress key={snapshotKey} value={item.stars_period} maximum={maximum}/>
      <div className="trending-secondary"><span className="trending-language">{item.language || '语言未标注'}</span><span>总 Stars <b>{count(item.stars_total)}</b></span><span>Forks <b>{count(item.forks_count)}</b></span></div>
    </> : <>
      <span className="trending-language">{item.language || '语言未标注'}</span>
      <div className="trending-row-period"><small>{label}</small><strong>{count(item.stars_period)}</strong><Progress key={snapshotKey} value={item.stars_period} maximum={maximum}/></div>
      <span className="trending-row-total">总 Stars <b>{count(item.stars_total)}</b></span>
    </>}
    <div className="trending-details" id={detailsId} role="region" aria-label={`${item.full_name} 详情`} hidden={!expanded}>
      <strong>{item.full_name}</strong>
      <p>{item.description || '暂无简介'}</p>
      <dl><div><dt>语言</dt><dd>{item.language || '语言未标注'}</dd></div><div><dt>总 Stars</dt><dd>{count(item.stars_total)}</dd></div><div><dt>Forks</dt><dd>{count(item.forks_count)}</dd></div></dl>
    </div>
    <Actions item={item} onCopy={onCopy}/>
  </li>;
}

export function TopCard(props: EntryProps) { return <EntryFrame {...props} focus/>; }
export function RepoRow(props: EntryProps) { return <EntryFrame {...props} focus={false}/>; }
