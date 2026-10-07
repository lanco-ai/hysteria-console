import ReactMarkdown from 'react-markdown';
import activeSubscription from '../../assets/shop-notices/active-subscription.png';
import messageRestriction from '../../assets/shop-notices/message-restriction.png';
import upgradeRestriction from '../../assets/shop-notices/upgrade-restriction.png';
import grokMonthly from '../../assets/shop-notices/grok-supergrok-monthly.png';

const claudeProductIds = new Set(['1000000000005', '1000000000007', '1000000000008']);
const grokProductIds = new Set(['1000000000011']);
const grokNoticeLinks = new Set(['https://sub2buy.com/#/grok', 'https://t.me/buy_gptplus', 'https://t.me/buygpt_plus', 'https://t.me/bkbk58']);
const noticeImages = [
  { alias: '/shop-notices/claude/active-subscription', src: activeSubscription, width: 1280, height: 957 },
  { alias: '/shop-notices/claude/message-restriction', src: messageRestriction, width: 2672, height: 1466 },
  { alias: '/shop-notices/claude/upgrade-restriction', src: upgradeRestriction, width: 1798, height: 608 },
  { alias: '/shop-notices/grok/supergrok-monthly', src: grokMonthly, width: 770, height: 1132 },
];

export function isClaudeNoticeProduct(productId: string) { return claudeProductIds.has(productId); }
export function isNoticeProduct(productId: string) { return isClaudeNoticeProduct(productId) || grokProductIds.has(productId); }

export function ShopNotice({ text, productId }: { text: string; productId: string }) {
  return <div className="shop-notice"><ReactMarkdown skipHtml unwrapDisallowed allowedElements={['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'strong', 'em', 'br', 'img', 'a']} components={{
    a: ({ href, children }) => grokProductIds.has(productId) && href && grokNoticeLinks.has(href) ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <>{children}</>,
    img: ({ src, alt }) => {
      const image = noticeImages.find(item => item.alias === src);
      return image ? <img src={image.src} alt={alt || '商品详情示例'} width={image.width} height={image.height} loading="lazy" /> : null;
    },
  }}>{text}</ReactMarkdown></div>;
}
