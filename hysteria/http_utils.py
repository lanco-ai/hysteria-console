"""HTTP request helpers for the local subscription/admin service."""
from urllib.parse import parse_qs, urlparse

MAX_FORM_BYTES = 256 * 1024
SESSION_TTL_DEFAULT = 86400


class RequestTooLarge(Exception):
    pass


class BadRequest(Exception):
    pass


def parse_form(handler, *, max_bytes=MAX_FORM_BYTES):
    try:
        length = int(handler.headers.get('Content-Length', '0') or 0)
    except (TypeError, ValueError):
        raise BadRequest
    if length < 0:
        raise BadRequest
    if length > max_bytes:
        raise RequestTooLarge
    body = handler.rfile.read(length).decode('utf-8', errors='ignore')
    return parse_qs(body)


def sanitize_host(raw_host):
    h = (raw_host or '').strip()
    if not h:
        return '127.0.0.1'
    if ',' in h:
        h = h.split(',', 1)[0].strip()
    if '/' in h or '\\' in h or '@' in h:
        return '127.0.0.1'
    if h.count(':') <= 1 and ':' in h:
        name, port = h.rsplit(':', 1)
        if name and port.isdigit() and 1 <= int(port) <= 65535:
            h = name
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-[]:')
    if any(ch not in allowed for ch in h):
        return '127.0.0.1'
    return h or '127.0.0.1'


def safe_base_url(host, forwarded_proto, forwarded_port=None):
    scheme = (forwarded_proto or 'http').split(',')[0].strip().lower()
    if scheme not in ('http', 'https'):
        scheme = 'http'
    port = str(forwarded_port or '').split(',', 1)[0].strip()
    if port.isdigit() and 1 <= int(port) <= 65535:
        if not ((scheme == 'http' and port == '80') or
                (scheme == 'https' and port == '443')):
            return f'{scheme}://{host}:{port}'
    return f'{scheme}://{host}'


def is_secure_request(handler):
    proto = (handler.headers.get('X-Forwarded-Proto') or '').split(',', 1)[0].strip().lower()
    return proto == 'https'


def _normalized_hostname(raw):
    host = sanitize_host(raw).lower().rstrip('.')
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    return host


def _url_matches_request_host(handler, raw_url):
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return False
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    return parsed.hostname.lower().rstrip('.') == _normalized_hostname(handler.headers.get('Host', ''))


def is_same_origin_post(handler):
    """Best-effort CSRF guard for browser-driven POSTs.

    Browsers send Origin on modern form POSTs; Referer is a fallback. Missing
    headers are allowed so local scripts and older clients keep working.
    """
    origin = handler.headers.get('Origin')
    if origin is not None:
        return _url_matches_request_host(handler, origin)
    referer = handler.headers.get('Referer')
    if referer:
        return _url_matches_request_host(handler, referer)
    return True


def session_cookie(sid, *, max_age=SESSION_TTL_DEFAULT, secure=False):
    cookie = f'sid={sid}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax'
    if secure:
        cookie += '; Secure'
    return cookie


def clear_session_cookie(*, secure=False):
    cookie = 'sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'
    if secure:
        cookie += '; Secure'
    return cookie
