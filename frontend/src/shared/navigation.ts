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
    ],
  },
  {
    placement: 'bottom',
    items: [
      { key: 'site', href: '/', label: '商城首页', icon: 'globe' },
    ],
  },
];
