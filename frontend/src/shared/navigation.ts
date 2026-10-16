import type { IconName } from './icons';

export type NavigationItem = {
  key: string;
  href: string;
  label: string;
  icon: IconName;
};

export type NavigationGroup = {
  label?: string;
  items: NavigationItem[];
  placement?: 'bottom';
};

// Merchant navigation stays inside the shop site.
export const navigationGroups: NavigationGroup[] = [
  {
    label: '商城',
    items: [
      { key: 'shop', href: '/admin/shop', label: '商品管理', icon: 'shop' },
      { key: 'inventory', href: '/admin/shop?view=inventory', label: '本店库存', icon: 'logs' },
      { key: 'procurement', href: '/admin/shop?view=procurement', label: '采购预订', icon: 'calendar' },
    ],
  },
  {
    placement: 'bottom',
    items: [
      { key: 'site', href: '/', label: '商城首页', icon: 'globe' },
    ],
  },
];
