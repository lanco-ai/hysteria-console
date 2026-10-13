import chatgptImage from '../../assets/shop-chatgpt.png';
import claudeImage from '../../assets/shop-claude.png';
import geminiImage from '../../assets/shop-gemini.png';
import grokImage from '../../assets/shop-grok.png';
import museImage from '../../assets/shop-muse.svg';
import xImage from '../../assets/shop-x.svg';

import smsImage from '../../assets/sms.svg';

export function ShopBrandImage({ category }: { category: string }) {
  if (category === '短信接码') return <img className="shop-brand-square" src={smsImage} alt="短信接码" width={400} height={400}/>;
  const image = category === 'Claude' ? claudeImage : category === 'Grok' ? grokImage : category === 'Gemini' ? geminiImage : category === 'X' ? xImage : category === 'Muse AI' ? museImage : chatgptImage;
  const brand = category === 'GPT' ? 'ChatGPT' : category;
  return <img className={category === 'GPT' ? undefined : 'shop-brand-square'} src={image} alt={brand} width={category === 'GPT' ? 531 : 400} height={category === 'GPT' ? 422 : 400}/>;
}
