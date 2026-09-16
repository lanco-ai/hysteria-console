import type { IconName } from './icons';

export type NavigationItem = {
  key: string;
  href: string;
  label: string;
  icon: IconName;
};

export type NavigationGroup = {
  label: string;
  items: NavigationItem[];
};

export const navigationGroups: NavigationGroup[] = [
  {
    label: '概览与用量',
    items: [
      { key: 'dashboard', href: '/admin', label: '总览', icon: 'dashboard' },
      { key: 'usage', href: '/admin/usage', label: '流量分析', icon: 'traffic' },
    ],
  },
  {
    label: '运行维护',
    items: [
      { key: 'health', href: '/admin/health', label: '健康状态', icon: 'pulse' },
      { key: 'incidents', href: '/admin/incidents', label: '事故处理', icon: 'pulse' },
      { key: 'logs', href: '/admin/logs', label: '清零日志', icon: 'logs' },
      { key: 'settings', href: '/admin/settings', label: '设置', icon: 'lock' },
    ],
  },
  {
    label: '网络配置',
    items: [
      { key: 'config', href: '/admin/config', label: '模板配置', icon: 'config' },
      { key: 'rules', href: '/admin/rules', label: '路由规则', icon: 'rules' },
      { key: 'landing-egresses', href: '/admin/landing-egresses', label: '家宽出口', icon: 'rules' },
    ],
  },
  {
    label: '工具',
    items: [
      { key: 'chat', href: '/chat', label: 'AI 对话', icon: 'chat' },
    ],
  },
];
