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

// Plans, AI tools and open-source discovery belong to this site.
export const navigationGroups: NavigationGroup[] = [
  {
    label: '工作台',
    items: [
      { key: 'plans', href: '/admin/plans', label: '今日计划', icon: 'calendar' },
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
      { key: 'trending', href: '/trending', label: '开源发现', icon: 'trending' },
    ],
  },
];
