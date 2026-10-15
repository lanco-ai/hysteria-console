// The document router owns history; the product sheet only asks it to close.
export const SHOP_RETURN_INDEX = '__lancoaiShopReturnIndex';
export const CLOSE_PRODUCT_EVENT = 'lancoai:close-product';

export function closeShopProduct(): void {
  window.dispatchEvent(new Event(CLOSE_PRODUCT_EVENT));
}
