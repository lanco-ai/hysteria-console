import ReactMarkdown from 'react-markdown';
import activeSubscription from '../../assets/shop-notices/active-subscription.png';
import messageRestriction from '../../assets/shop-notices/message-restriction.png';
import upgradeRestriction from '../../assets/shop-notices/upgrade-restriction.png';
import grokMonthly from '../../assets/shop-notices/grok-supergrok-monthly.png';

const claudeProductIds = new Set(['1000000000005', '1000000000007', '1000000000008']);
const grokProductIds = new Set(['1000000000011']);
const noticeImages = [
  { alias: '/shop-notices/claude/active-subscription', src: activeSubscription, width: 1280, height: 957 },
  { alias: '/shop-notices/claude/message-restriction', src: messageRestriction, width: 2672, height: 1466 },
  { alias: '/shop-notices/claude/upgrade-restriction', src: upgradeRestriction, width: 1798, height: 608 },
  { alias: '/shop-notices/grok/supergrok-monthly', src: grokMonthly, width: 770, height: 1132 },
];

// Only the redemption portals buyers need are clickable; any other URL stays plain text.
const redeemHosts = new Set(['aiflbchengzi.com', 'sub2buy.com']);
function redeemLink(href: string | undefined) {
  try {
    const url = new URL(href ?? '');
    return url.protocol === 'https:' && redeemHosts.has(url.hostname) ? url.href : null;
  } catch { return null; }
}

export function ShopCopyText({ text }: { text: string }) {
  return <p>{text.split(/(https:\/\/[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+)/).map((part, index) => {
    const link = index % 2 ? redeemLink(part) : null;
    return link ? <a key={index} href={link} target="_blank" rel="noopener noreferrer">{part}</a> : part;
  })}</p>;
}

export function isClaudeNoticeProduct(productId: string) { return claudeProductIds.has(productId); }
export function isNoticeProduct(productId: string) { return isClaudeNoticeProduct(productId) || grokProductIds.has(productId); }

export function ShopNotice({ text }: { text: string }) {
  return <div className="shop-notice"><ReactMarkdown skipHtml unwrapDisallowed allowedElements={['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'strong', 'em', 'br', 'img', 'a']} components={{
    a: ({ href, children }) => {
      const link = redeemLink(href);
      return link ? <a href={link} target="_blank" rel="noopener noreferrer">{children}</a> : <>{children}</>;
    },
    img: ({ src, alt }) => {
      const image = noticeImages.find(item => item.alias === src);
      return image ? <img src={image.src} alt={alt || '商品详情示例'} width={image.width} height={image.height} loading="lazy" /> : null;
    },
  }}>{text}</ReactMarkdown></div>;
}
