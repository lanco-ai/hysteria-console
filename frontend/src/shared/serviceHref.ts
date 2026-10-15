/** Canonical HTTPS entrances. APIs/auth remain same-origin on all three sites. */
export function serviceHref(value: string, location: Pick<Location, 'href' | 'origin' | 'protocol' | 'port'> = window.location): string {
  // Local HTTP previews and unrelated deployments retain relative navigation.
  if (location.protocol !== 'https:' || !['', '443', '9444', '8444', '8445'].includes(location.port)) return value;
  const url = new URL(value, location.href);
  if (url.origin !== location.origin) return value;
  let port: string | undefined;
  if (url.pathname === '/') port = '';
  else if (url.pathname === '/admin/shop') port = '';
  else if (['/trending', '/admin/plans', '/admin/chat', '/admin/video', '/admin/services'].includes(url.pathname)) port = '8445';
  else if (url.pathname === '/admin' || url.pathname.startsWith('/admin/') || url.pathname === '/user/panel') port = '8444';
  if (port === undefined) return value;
  url.port = port;
  return url.origin === location.origin ? value : url.href;
}
