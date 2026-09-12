import { useLayoutEffect } from 'react';

export function useInitialFragmentNavigation(targets: ReadonlySet<string>): void {
  useLayoutEffect(() => {
    const fragment = window.location.hash.slice(1);
    if (!targets.has(fragment)) return;
    const target = document.getElementById(fragment);
    if (!target) return;
    target.scrollIntoView();
    if (target.hasAttribute('tabindex')) target.focus({ preventScroll: true });
  }, [targets]);
}
