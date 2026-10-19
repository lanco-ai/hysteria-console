import ReactMarkdown from 'react-markdown';
import activeSubscription from '../../assets/shop-notices/active-subscription.png';
import messageRestriction from '../../assets/shop-notices/message-restriction.png';
import upgradeRestriction from '../../assets/shop-notices/upgrade-restriction.png';
import grokMonthly from '../../assets/shop-notices/grok-supergrok-monthly.png';
import vpnUsers from '../../assets/shop-notices/vpn-users-redacted.png';
import vpnTraffic from '../../assets/shop-notices/vpn-traffic-redacted.png';
import vpnRouting from '../../assets/shop-notices/vpn-routing-redacted.png';
import vpnOperations from '../../assets/shop-notices/vpn-operations-redacted.png';
import vpnLatency from '../../assets/shop-notices/vpn-latency.png';
import grokUserId from '../../assets/shop-notices/grok-userid-guide.png';

const claudeProductIds = new Set(['1000000000005', '1000000000007', '1000000000008']);
const grokProductIds = new Set(['1000000000011', '6000000000004']);
const geminiProductIds = new Set(['2000000000001']);
const museProductIds = new Set(['2000000000002']);
const duolingoProductIds = new Set(['2000000000003']);
const vpnProductIds = new Set(['6000000000002']);
const textProductIds = new Set(['1', '2', '3', '4', '5', '6', '5000000000001', '6000000000001', '6000000000003', '6000000000005', '6000000000006']);
const noticeImages = [
  { alias: '/shop-notices/claude/active-subscription', src: activeSubscription, width: 1280, height: 957 },
  { alias: '/shop-notices/claude/message-restriction', src: messageRestriction, width: 2672, height: 1466 },
  { alias: '/shop-notices/claude/upgrade-restriction', src: upgradeRestriction, width: 1798, height: 608 },
  { alias: '/shop-notices/grok/supergrok-monthly', src: grokMonthly, width: 770, height: 1132 },
  { alias: '/shop-notices/grok/userid-guide', src: grokUserId, width: 1586, height: 992, preview: true },
  { alias: '/shop-notices/vpn/latency', src: vpnLatency, width: 760, height: 380, preview: true },
  { alias: '/shop-notices/vpn/users', src: vpnUsers, width: 1863, height: 844, preview: true },
  { alias: '/shop-notices/vpn/traffic', src: vpnTraffic, width: 1717, height: 916, preview: true },
  { alias: '/shop-notices/vpn/routing', src: vpnRouting, width: 1777, height: 885, preview: true },
  { alias: '/shop-notices/vpn/operations', src: vpnOperations, width: 1604, height: 980, preview: true },
];

// Redemption portals and these exact eligibility/login/tutorial pages are clickable.
const redeemHosts = new Set(['aiflbchengzi.com', 'sub2buy.com']);
const noticeUrls = new Set([
  'https://policies.google.com/terms?hl=zh_CN',
  'https://dev-api.cc/muse.php',
  'https://www.tmail.pk/login',
  'https://muse.ai/',
  'https://www.youtube.com/watch?v=lpsRG9VSDuQ',
  'https://6661231.xyz/',
  'https://grok.com/api/auth/session',
]);
function noticeLink(href: string | undefined) {
  try {
    const url = new URL(href ?? '');
    return (url.protocol === 'https:' && redeemHosts.has(url.hostname)) || noticeUrls.has(url.href) ? url.href : null;
  } catch { return null; }
}

export function ShopCopyText({ text }: { text: string }) {
  return <p>{text.split(/(https:\/\/[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+)/).map((part, index) => {
    const link = index % 2 ? noticeLink(part) : null;
    return link ? <a key={index} href={link} target="_blank" rel="noopener noreferrer">{part}</a> : part;
  })}</p>;
}

export function isClaudeNoticeProduct(productId: string) { return claudeProductIds.has(productId); }
export function isNoticeProduct(productId: string) { return isClaudeNoticeProduct(productId) || grokProductIds.has(productId) || geminiProductIds.has(productId) || museProductIds.has(productId) || duolingoProductIds.has(productId) || vpnProductIds.has(productId) || textProductIds.has(productId); }

// A heading such as "### 规格：3 个月（季卡）" starts copy for that spec alone; it runs to the next heading of
// the same or a higher level.
const SPEC_HEADING = /^(#{1,6})[ \t]+规格[：:][ \t]*(.+?)[ \t#]*$/;
const specName = (text: string) => text.replace(/\s+/g, '').toLowerCase();

/** The copy for the selected spec: sections headed with another of the product's specs are left out.
 * A section naming no spec of the product (one renamed since, say) stays. */
export function variantCopy(text: string, labels: string[], selected: string | undefined): string {
  const others = new Set(labels.filter(label => label !== selected).map(specName));
  others.delete(specName(selected ?? ''));
  let skipping = 0;
  return text.split('\n').filter(line => {
    const level = /^(#{1,6})[ \t]/.exec(line)?.[1]?.length ?? 0;
    if (skipping && (!level || level > skipping)) return false;
    skipping = 0;
    const spec = SPEC_HEADING.exec(line.trimEnd());
    if (spec && others.has(specName(spec[2]!))) { skipping = spec[1]!.length; return false; }
    return true;
  }).join('\n');
}

export function ShopNotice({ text }: { text: string }) {
  return <div className="shop-notice"><ReactMarkdown skipHtml unwrapDisallowed allowedElements={['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'strong', 'em', 'br', 'blockquote', 'img', 'a']} components={{
    a: ({ href, children }) => {
      const link = noticeLink(href);
      return link ? <a href={link} target="_blank" rel="noopener noreferrer">{children}</a> : <>{children}</>;
    },
    img: ({ src, alt }) => {
      const image = noticeImages.find(item => item.alias === src);
      if (!image) return null;
      const label = alt || '商品详情示例';
      const screenshot = <img src={image.src} alt={label} width={image.width} height={image.height} loading="lazy" />;
      return image.preview ? <a className="shop-notice-screenshot" href={image.src} target="_blank" rel="noopener noreferrer" aria-label={`查看大图：${label}`}>{screenshot}</a> : screenshot;
    },
  }}>{text}</ReactMarkdown></div>;
}
