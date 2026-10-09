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

// Every item has its own icon so the collapsed rail stays readable. 今日计划
// is where sign-in lands; 流量分析 also holds incidents, 路由与出口 the egress
// nodes, and 运维 the health checks and reset log.
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
    label: '工作台',
    items: [
      { key: 'plans', href: '/admin/plans', label: '今日计划', icon: 'calendar' },
      { key: 'shop', href: '/admin/shop', label: '商品管理', icon: 'shop' },
    ],
  },
  {
    label: 'AI 工具',
    items: [
      { key: 'chat', href: '/admin/chat', label: 'AI 对话', icon: 'chat' },
      { key: 'video', href: '/admin/video', label: 'AI 视频', icon: 'video' },
      // 服务中心 mainly holds the API access the AI tools use.
      { key: 'services', href: '/admin/services', label: '服务中心', icon: 'plug' },
    ],
  },
  {
    placement: 'bottom',
    items: [
      { key: 'site', href: '/', label: '访问网站', icon: 'globe' },
      { key: 'settings', href: '/admin/settings', label: '设置', icon: 'settings' },
    ],
  },
];
