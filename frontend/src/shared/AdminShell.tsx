import { CodexShell } from './CodexShell';
import type { CodexShellProps } from './CodexShell';

type AdminShellProps = Pick<CodexShellProps, 'active' | 'badge' | 'pageTitle' | 'children' | 'subtitle' | 'topbarExtra'>;

export { applyInitialShellPreferences } from './CodexShell';

export function AdminShell({ active, badge, pageTitle, children, subtitle, topbarExtra }: AdminShellProps) {
  return <CodexShell
    active={active}
    pageTitle={pageTitle}
    {...(badge === undefined ? {} : { badge })}
    {...(subtitle === undefined ? {} : { subtitle })}
    {...(topbarExtra === undefined ? {} : { topbarExtra })}
    agentEnabled
  >{children}</CodexShell>;
}
