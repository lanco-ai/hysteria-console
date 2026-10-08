import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../shared/icons';
import { PortalShell } from '../public/PortalShell';
import { TrendingEntry, safeRepoUrl, validCount, type Period, type TrendingItem } from './TrendingEntries';

type Snapshot = {
  period: Period;
  source: string;
  source_url: string;
  fetched_at: string | null;
  last_success_at: string | null;
  is_stale: boolean;
  refreshing: boolean;
  items: TrendingItem[];
  error: string | null;
  status: 'ready' | 'loading' | 'unavailable';
  cooldown_seconds: number;
  retry_after_seconds: number;
};

const REFRESH_STARTED_MESSAGE = '刷新已开始，正在获取 GitHub 榜单。';
const PERIODS: Array<[Period, string, string]> = [['weekly', '周榜', '/?view=trending'], ['daily', '日榜', '/?view=trending&period=daily']];

function absoluteTime(value: string | null): string {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.valueOf()) ? date.toLocaleString('zh-CN', { hour12: false }) : '';
}

function relativeTime(value: string | null, now: number): string {
  const time = value ? new Date(value).valueOf() : Number.NaN;
  if (Number.isNaN(time)) return '';
  const minutes = Math.floor(Math.max(0, now - time) / 60_000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} 小时前`;
  return new Date(time).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

function errorLabel(code: string | null): string {
  if (code === 'rate_limited') return 'GitHub 上游限流';
  if (code === 'upstream_denied') return 'GitHub 上游拒绝访问';
  if (code === 'upstream_timeout') return 'GitHub 上游请求超时';
  if (code === 'upstream_unavailable') return 'GitHub 上游暂不可用';
  if (code === 'unsafe_redirect') return 'GitHub 来源地址异常';
  if (code === 'response_too_large') return 'GitHub 响应超出限制';
  if (code === 'parse_error') return 'GitHub 页面格式变化';
  return '榜单更新失败';
}

async function readSnapshot(period: Period, signal?: AbortSignal): Promise<Snapshot> {
  const response = await fetch(`/api/v1/github-trending?period=${period}`, { credentials: 'same-origin', ...(signal ? { signal } : {}) });
  if (!response.ok) throw new Error(String(response.status));
  return response.json() as Promise<Snapshot>;
}

// Public discovery page. Administrators additionally get the manual refresh.
export function GithubTrendingPage({ period, authenticated = false }: { period: Period; authenticated?: boolean }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [language, setLanguage] = useState('');
  const [feedback, setFeedback] = useState('');
  const [posting, setPosting] = useState(false);
  const [reloadEpoch, setReloadEpoch] = useState(0);
  const [receivedAt, setReceivedAt] = useState(Date.now);
  const [now, setNow] = useState(Date.now);
  const generation = useRef(0);
  const requestVersion = useRef(0);
  const activeGet = useRef<AbortController | null>(null);
  const postingRef = useRef(false);

  const elapsedSeconds = Math.max(0, (now - receivedAt) / 1000);
  const cooldownSeconds = Math.max(0, Math.ceil((snapshot?.cooldown_seconds || 0) - elapsedSeconds));
  const retrySeconds = Math.max(0, Math.ceil((snapshot?.retry_after_seconds || 0) - elapsedSeconds));

  useEffect(() => {
    // Countdowns tick every half second; otherwise once a minute keeps "N 分钟前" honest.
    const timer = window.setInterval(() => setNow(Date.now()), snapshot?.cooldown_seconds || snapshot?.retry_after_seconds ? 500 : 30_000);
    return () => window.clearInterval(timer);
  }, [snapshot?.cooldown_seconds, snapshot?.retry_after_seconds]);

  const syncSnapshot = (requestedPeriod: Period, current: number): AbortController => {
    activeGet.current?.abort();
    const controller = new AbortController();
    activeGet.current = controller;
    const version = ++requestVersion.current;
    readSnapshot(requestedPeriod, controller.signal).then(value => {
      if (current !== generation.current || version !== requestVersion.current) return;
      setSnapshot(value);
      setRequestError(null);
      if (!value.refreshing) setFeedback(previous => previous === REFRESH_STARTED_MESSAGE ? '' : previous);
      setReceivedAt(Date.now());
      setNow(Date.now());
      setLoading(false);
    }).catch(error => {
      if (current !== generation.current || version !== requestVersion.current || controller.signal.aborted) return;
      setRequestError(error instanceof Error ? error.message : 'network');
      setLoading(false);
    });
    return controller;
  };

  useEffect(() => {
    const current = ++generation.current;
    let lastSyncAt = Date.now();
    setSnapshot(null);
    setRequestError(null);
    setFeedback('');
    setLoading(true);
    setPosting(false);
    postingRef.current = false;
    syncSnapshot(period, current);
    const refreshCache = () => {
      if (document.visibilityState !== 'visible' || postingRef.current) return;
      lastSyncAt = Date.now();
      syncSnapshot(period, current);
    };
    const timer = window.setInterval(refreshCache, 60_000);
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastSyncAt >= 60_000) refreshCache();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      activeGet.current?.abort();
      generation.current += 1;
      requestVersion.current += 1;
    };
  }, [period, reloadEpoch]);

  useEffect(() => {
    if (!snapshot?.refreshing || snapshot.period !== period) return;
    const current = generation.current;
    let controller: AbortController | null = null;
    const timer = window.setTimeout(() => { if (!postingRef.current) controller = syncSnapshot(period, current); }, 3000);
    return () => { window.clearTimeout(timer); controller?.abort(); };
  }, [snapshot, period]);

  const items = snapshot?.items ?? [];
  // A language chosen on the other board stays selectable, so the filter never silently changes.
  const languages = useMemo(() => Array.from(new Set([...items.map(item => item.language), language].filter((value): value is string => !!value))).sort((a, b) => a.localeCompare(b)), [items, language]);
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return items.filter(item =>
      (!language || item.language === language) &&
      (!query || `${item.full_name} ${item.description || ''}`.toLocaleLowerCase().includes(query))
    );
  }, [items, search, language]);
  const isFiltered = !!search.trim() || !!language;
  const maximum = Math.max(0, ...filtered.map(item => validCount(item.stars_period) ? item.stars_period : 0));
  const featured = isFiltered ? [] : filtered.filter(item => item.source_rank <= 3);
  const others = isFiltered ? filtered : filtered.filter(item => item.source_rank > 3);
  const updated = relativeTime(snapshot?.last_success_at ?? null, now);
  const noCache = !snapshot?.last_success_at;
  const authLost = requestError === '401' || requestError === '403';

  const refresh = async () => {
    if (postingRef.current || posting || snapshot?.refreshing || cooldownSeconds > 0 || retrySeconds > 0) return;
    postingRef.current = true;
    activeGet.current?.abort();
    const version = ++requestVersion.current;
    setPosting(true);
    setFeedback('');
    const current = generation.current;
    try {
      const response = await fetch('/api/v1/github-trending/refresh', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ period }),
      });
      if (!response.ok) throw new Error(String(response.status));
      const value = await response.json() as Snapshot;
      if (current !== generation.current || version !== requestVersion.current) return;
      setSnapshot(value);
      setRequestError(null);
      setReceivedAt(Date.now());
      setNow(Date.now());
      if (value.refreshing) setFeedback(REFRESH_STARTED_MESSAGE);
      else if (value.retry_after_seconds > 0) setFeedback('上游需稍后重试，服务端暂未开始新刷新。');
      else if (value.cooldown_seconds > 0) setFeedback('刷新冷却中，服务端暂未开始新刷新。');
      else setFeedback('当前榜单已是最新缓存。');
    } catch (error) {
      if (current === generation.current && version === requestVersion.current) setRequestError(error instanceof Error ? error.message : 'network');
    } finally {
      if (current === generation.current && version === requestVersion.current) {
        postingRef.current = false;
        setPosting(false);
      }
    }
  };

  const copy = async (item: TrendingItem) => {
    const url = safeRepoUrl(item);
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setFeedback(`${item.full_name} 链接已复制`);
    } catch {
      setFeedback('复制失败，请点击仓库名在 GitHub 打开。');
    }
  };

  const entries = (list: TrendingItem[], isFeatured: boolean) => list.map(item => <TrendingEntry key={`${item.source_rank}:${item.full_name}`} item={item} period={period} maximum={maximum} featured={isFeatured} onCopy={value => void copy(value)}/>);
  return <PortalShell active="trending" pageTitle="GitHub 热榜">
    <div className="trending-page">
      <header className="trending-hero">
        <div>
          <p className="portal-eyebrow">OPEN SOURCE · GITHUB TRENDING</p>
          <h1>GitHub 热榜</h1>
          <p>{period === 'weekly' ? '本周' : '今天'}在 GitHub 上最受关注的开源项目，排名与 GitHub Trending 一致。</p>
        </div>
        <div className="trending-hero-meta">
          {updated ? <span title={absoluteTime(snapshot?.last_success_at ?? null)}>{updated}更新</span> : null}
          {authenticated ? <button className="trending-refresh" type="button" onClick={refresh} disabled={posting || loading || !!snapshot?.refreshing || cooldownSeconds > 0 || retrySeconds > 0}>刷新榜单</button> : null}
        </div>
      </header>
      <div className="trending-toolbar">
        <nav className="trending-period" aria-label="榜单周期">
          {PERIODS.map(([value, label, href]) => <a key={value} href={href} aria-current={period === value ? 'page' : undefined}>{label}</a>)}
        </nav>
        <label className="trending-search"><Icon name="search"/><input type="search" value={search} onChange={event => setSearch(event.target.value)} aria-label="搜索仓库或简介" placeholder="搜索仓库或简介"/></label>
        <select className="trending-language-filter" value={language} onChange={event => setLanguage(event.target.value)} aria-label="按语言筛选">
          <option value="">全部语言</option>
          {languages.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
        {snapshot?.status === 'ready' && items.length ? <span className="trending-count">{isFiltered ? `${filtered.length} / ${items.length}` : items.length} 个项目</span> : null}
      </div>
      <div className="trending-status" aria-live="polite">
        {snapshot?.refreshing ? <span className="trending-notice">正在更新榜单…</span> : null}
        {snapshot?.is_stale && snapshot.last_success_at ? <span className="trending-notice warning">榜单暂未更新到最新，当前显示{updated}的数据</span> : null}
        {authenticated && snapshot?.error ? <span className="trending-notice warning">{errorLabel(snapshot.error)}</span> : null}
        {authenticated && cooldownSeconds > 0 ? <span className="trending-notice">手动刷新冷却剩余 {cooldownSeconds} 秒</span> : null}
        {authenticated && retrySeconds > 0 ? <span className="trending-notice warning">上游重试等待剩余 {retrySeconds} 秒</span> : null}
        {requestError ? <span className="trending-notice warning" role="alert">{authLost ? '管理员登录已失效，请重新登录后再刷新。' : '榜单请求失败，请稍后重试。'}</span> : null}
        {requestError && !authLost ? <button className="trending-retry" type="button" onClick={() => setReloadEpoch(value => value + 1)}>重试</button> : null}
        {feedback ? <span className="trending-feedback" role="status">{feedback}</span> : null}
      </div>
      {loading ? <div className="trending-empty" role="status">正在读取{period === 'weekly' ? '周榜' : '日榜'}…</div> : null}
      {!loading && noCache && snapshot?.status === 'loading' ? <div className="trending-empty" role="status">首次获取 GitHub 榜单中，请稍后查看。</div> : null}
      {!loading && noCache && (snapshot?.status === 'unavailable' || requestError) ? <div className="trending-empty">暂时无法获取榜单，请稍后再试。</div> : null}
      {!loading && snapshot?.last_success_at && items.length === 0 ? <div className="trending-empty">本期榜单暂无项目。</div> : null}
      {!loading && items.length > 0 && filtered.length === 0 ? <div className="trending-empty">没有符合筛选条件的项目。<button className="trending-retry" type="button" onClick={() => { setSearch(''); setLanguage(''); }}>清除筛选</button></div> : null}
      {featured.length ? <ol className="trending-featured" aria-label={`${period === 'weekly' ? '周榜' : '日榜'}前三名`}>{entries(featured, true)}</ol> : null}
      {others.length ? <ol className="trending-list" aria-label={`${period === 'weekly' ? '周榜' : '日榜'}${isFiltered ? '筛选结果' : '其余项目'}`}>{entries(others, false)}</ol> : null}
    </div>
  </PortalShell>;
}
