import { useEffect, useMemo, useRef, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';

type Period = 'weekly' | 'daily';
type TrendingItem = {
  source_rank: number;
  full_name: string;
  html_url: string;
  description: string | null;
  language: string | null;
  stars_total: number | null;
  stars_period: number | null;
  forks_count: number | null;
};
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

function safeRepoUrl(item: TrendingItem): string | null {
  try {
    const url = new URL(item.html_url);
    if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.port || url.username || url.password || url.search || url.hash) return null;
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(item.full_name) || url.pathname !== `/${item.full_name}`) return null;
    return url.href;
  } catch {
    return null;
  }
}

function count(value: number | null): string {
  return value === null || !Number.isSafeInteger(value) || value < 0 ? '—' : value.toLocaleString('en-US');
}

function timeLabel(value: string | null): string {
  if (!value) return '尚无成功记录';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '时间未知' : date.toLocaleString('zh-CN', { hour12: false });
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

export function GithubTrendingPage() {
  const [period, setPeriod] = useState<Period>('weekly');
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
    if (!snapshot?.cooldown_seconds && !snapshot?.retry_after_seconds) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
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
      const code = error instanceof Error ? error.message : 'network';
      if (code === '401' || code === '403') {
        setSnapshot(null);
        setFeedback('');
      }
      setRequestError(code);
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

  const languages = useMemo(() => Array.from(new Set((snapshot?.items || []).map(item => item.language).filter((value): value is string => !!value))).sort((a, b) => a.localeCompare(b)), [snapshot]);
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return (snapshot?.items || []).filter(item =>
      (!language || item.language === language) &&
      (!query || `${item.full_name} ${item.description || ''}`.toLocaleLowerCase().includes(query))
    );
  }, [snapshot, search, language]);

  const changePeriod = (next: Period) => {
    if (next === period) return;
    setSearch('');
    setLanguage('');
    setPosting(false);
    postingRef.current = false;
    setPeriod(next);
  };

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
      if (current === generation.current && version === requestVersion.current) {
        const code = error instanceof Error ? error.message : 'network';
        if (code === '401' || code === '403') setSnapshot(null);
        setRequestError(code);
      }
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
      setFeedback('复制失败，请使用“在 GitHub 查看”打开链接。');
    }
  };

  const authExpired = requestError === '401' || requestError === '403';
  const noCache = !snapshot?.last_success_at;
  return <AdminShell active="github-trending" pageTitle="GitHub 热榜" subtitle="开源发现 · GitHub Trending">
    <div className="admin-page trending-page">
      <header className="trending-intro">
        <div><p className="trending-eyebrow">OPEN SOURCE DISCOVERY</p><h2>GitHub 热榜</h2><p>浏览 GitHub Trending 的真实日榜与周榜。排名保留来源顺序。</p></div>
        <div className="trending-source"><span>来源</span><strong>GitHub Trending</strong><small>最近成功获取：{timeLabel(snapshot?.last_success_at || null)}</small></div>
      </header>
      <section className="admin-section" aria-label="GitHub 热榜控制台">
        <div className="trending-controls">
          <div className="trending-period" role="group" aria-label="榜单周期">
            <button type="button" aria-pressed={period === 'weekly'} onClick={() => changePeriod('weekly')}>周榜</button>
            <button type="button" aria-pressed={period === 'daily'} onClick={() => changePeriod('daily')}>日榜</button>
          </div>
          <button className="btn secondary" type="button" onClick={refresh} disabled={posting || loading || !!snapshot?.refreshing || cooldownSeconds > 0 || retrySeconds > 0 || authExpired}>手动刷新</button>
        </div>
        <div className="trending-status" aria-live="polite">
          {snapshot?.is_stale && snapshot.last_success_at ? <span className="trending-notice warning">正在显示上次成功缓存</span> : null}
          {snapshot?.refreshing ? <span className="trending-notice">后台刷新中…</span> : null}
          {snapshot?.error ? <span className="trending-notice warning">{errorLabel(snapshot.error)}</span> : null}
          {cooldownSeconds > 0 ? <span className="trending-notice">手动刷新冷却剩余 {cooldownSeconds} 秒</span> : null}
          {retrySeconds > 0 ? <span className="trending-notice warning">上游重试等待剩余 {retrySeconds} 秒</span> : null}
          {snapshot && !snapshot.is_stale && !snapshot.refreshing && !snapshot.error && snapshot.status === 'ready' ? <span className="trending-notice">已加载缓存榜单</span> : null}
          {requestError ? <span className="trending-notice warning" role="alert">{requestError === '401' ? '管理员登录已失效。' : requestError === '403' ? '需要管理员权限。' : '榜单请求失败，请重试。'}</span> : null}
          {feedback ? <span className="trending-feedback" role="status">{feedback}</span> : null}
          {requestError === '401' ? <a className="btn secondary" href="/login">前往登录</a> : null}
          {requestError && !authExpired ? <button className="btn secondary" type="button" onClick={() => setReloadEpoch(value => value + 1)}>重试</button> : null}
        </div>
        {snapshot?.status === 'ready' && snapshot.items.length > 0 ? <div className="trending-filters">
          <label>搜索仓库或简介<input type="search" value={search} onChange={event => setSearch(event.target.value)} aria-label="搜索仓库或简介" placeholder="owner/repo 或简介"/></label>
          <label>语言（本榜内筛选）<select value={language} onChange={event => setLanguage(event.target.value)} aria-label="语言（本榜内筛选）"><option value="">全部语言</option>{languages.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          {search || language ? <button className="trending-clear" type="button" onClick={() => { setSearch(''); setLanguage(''); }}>清除筛选</button> : null}
          <small>本榜内筛选 · {filtered.length} / {snapshot.items.length} 个项目</small>
        </div> : null}
        {loading ? <div className="trending-empty" role="status">正在读取{period === 'weekly' ? '周榜' : '日榜'}缓存…</div> : null}
        {!loading && noCache && snapshot?.status === 'loading' ? <div className="trending-empty" role="status">首次获取 GitHub 榜单中，请稍后查看。</div> : null}
        {!loading && noCache && (snapshot?.status === 'unavailable' || requestError) ? <div className="trending-empty">暂时无法获取榜单。{retrySeconds ? `约 ${retrySeconds} 秒后可重试。` : '请稍后重试。'}</div> : null}
        {!loading && snapshot?.last_success_at && snapshot.items.length === 0 ? <div className="trending-empty">本期榜单暂无项目。</div> : null}
        {!loading && snapshot?.items.length && filtered.length === 0 ? <div className="trending-empty">本榜内没有符合筛选条件的仓库。</div> : null}
        {filtered.length > 0 ? <ol className="trending-list" aria-label={`${period === 'weekly' ? '周榜' : '日榜'}仓库`}>
          {filtered.map(item => {
            const url = safeRepoUrl(item);
            return <li className="trending-repo" key={`${item.source_rank}:${item.full_name}`}>
              <span className="trending-rank">#{item.source_rank}</span>
              <div className="trending-repo-main"><h2>{item.full_name}</h2><p>{item.description || '暂无简介'}</p><span className="trending-language">{item.language || '语言未标注'}</span></div>
              <dl className="trending-metrics"><div><dt>总 Stars</dt><dd>{count(item.stars_total)}</dd></div><div><dt>{period === 'weekly' ? '本周 Stars' : '今日 Stars'}</dt><dd>{count(item.stars_period)}</dd></div><div><dt>Forks</dt><dd>{count(item.forks_count)}</dd></div></dl>
              <div className="trending-actions">{url ? <><a href={url} target="_blank" rel="noopener noreferrer">在 GitHub 查看</a><button type="button" onClick={() => void copy(item)}>复制链接</button></> : <span>链接不可用</span>}</div>
            </li>;
          })}
        </ol> : null}
      </section>
    </div>
  </AdminShell>;
}
