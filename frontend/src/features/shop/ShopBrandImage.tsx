import chatgptImage from '../../assets/shop-chatgpt.png';
import claudeImage from '../../assets/shop-claude.png';
import geminiImage from '../../assets/shop-gemini.png';
import grokImage from '../../assets/shop-grok.png';

export function ShopBrandImage({ category }: { category: string }) {
  const image = category === 'Claude' ? claudeImage : category === 'Grok' ? grokImage : category === 'Gemini' ? geminiImage : chatgptImage;
  const brand = category === 'GPT' ? 'ChatGPT' : category;
  return <img className={category === 'GPT' ? undefined : 'shop-brand-square'} src={image} alt={brand} width={category === 'GPT' ? 531 : 400} height={category === 'GPT' ? 422 : 400}/>;
}
