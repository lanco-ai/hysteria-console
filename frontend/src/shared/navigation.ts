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

export const navigationGroups: NavigationGroup[] = [
  {
    label: '工作台',
    items: [
      { key: 'dashboard', href: '/admin', label: '总览', icon: 'dashboard' },
      { key: 'plans', href: '/admin/plans', label: '今日计划', icon: 'calendar' },
      { key: 'chat', href: '/admin/chat', label: 'AI 对话', icon: 'chat' },
      { key: 'video', href: '/admin/video', label: 'AI 视频', icon: 'video' },
    ],
  },
  {
    label: '网络管理',
    items: [
      { key: 'usage', href: '/admin/usage', label: '流量分析', icon: 'traffic' },
      { key: 'config', href: '/admin/config', label: '模板与路由', icon: 'config' },
      { key: 'landing-egresses', href: '/admin/landing-egresses', label: '家宽出口', icon: 'rules' },
    ],
  },
  {
    label: '运维管理',
    items: [
      { key: 'health', href: '/admin/health', label: '健康状态', icon: 'pulse' },
      { key: 'incidents', href: '/admin/incidents', label: '事故处理', icon: 'pulse' },
      { key: 'logs', href: '/admin/logs', label: '清零日志', icon: 'logs' },
    ],
  },
  {
    label: '服务接入',
    items: [
      { key: 'services', href: '/admin/services', label: '服务中心', icon: 'dashboard' },
    ],
  },
  {
    placement: 'bottom',
    items: [
      { key: 'settings', href: '/admin/settings', label: '设置', icon: 'lock' },
    ],
  },
];
