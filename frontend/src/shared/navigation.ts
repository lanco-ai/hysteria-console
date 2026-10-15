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

// This site exposes network management only. Other services own their menus.
export const navigationGroups: NavigationGroup[] = [
  {
    label: '网络管理',
    items: [
      { key: 'dashboard', href: '/admin', label: '用户', icon: 'users' },
      { key: 'usage', href: '/admin/usage', label: '流量分析', icon: 'traffic' },
      { key: 'config', href: '/admin/config', label: '路由与出口', icon: 'config' },
      { key: 'operations', href: '/admin/health', label: '运维', icon: 'pulse' },
    ],
  },
  {
    placement: 'bottom',
    items: [
      { key: 'settings', href: '/admin/settings', label: '设置', icon: 'settings' },
    ],
  },
];
