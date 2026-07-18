"""HTTP request helpers for the local subscription/admin service."""
import ipaddress
import re
from urllib.parse import parse_qs, urlparse

MAX_FORM_BYTES = 256 * 1024
MAX_FORM_FIELDS = 128
SESSION_TTL_DEFAULT = 86400
_INVALID_PERCENT_ESCAPE = re.compile(r'%(?![0-9A-Fa-f]{2})')


class RequestTooLarge(Exception):
    pass


class BadRequest(Exception):
    pass


def parse_form(handler, *, max_bytes=MAX_FORM_BYTES):
    if handler.headers.get('Transfer-Encoding') is not None:
        raise BadRequest
    raw_content_type = str(handler.headers.get('Content-Type') or '')
    if raw_content_type:
        content_type_parts = [
            part.strip().lower()
            for part in raw_content_type.split(';')
        ]
        if (
            content_type_parts[0]
            != 'application/x-www-form-urlencoded'
            or any(
                part != 'charset=utf-8'
                for part in content_type_parts[1:]
            )
        ):
            raise BadRequest
    get_all = getattr(handler.headers, 'get_all', None)
    if callable(get_all):
        content_lengths = get_all('Content-Length', [])
    else:
        raw_length = handler.headers.get('Content-Length')
        content_lengths = [] if raw_length is None else [raw_length]
    if len(content_lengths) != 1:
        raise BadRequest
    try:
        raw_length = str(content_lengths[0])
        if not raw_length.isascii() or not raw_length.isdigit():
            raise ValueError
        length = int(raw_length)
    except (TypeError, ValueError):
        raise BadRequest
    if length > max_bytes:
        raise RequestTooLarge
    try:
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise BadRequest
        body = raw.decode('utf-8')
        if not body:
            return {}
        if _INVALID_PERCENT_ESCAPE.search(body):
            raise BadRequest
        return parse_qs(
            body,
            max_num_fields=MAX_FORM_FIELDS,
            strict_parsing=True,
            errors='strict',
            separator='&',
        )
    except (UnicodeDecodeError, ValueError):
        raise BadRequest


def sanitize_host(raw_host):
    raw = str(raw_host or '').strip()
    if (
        not raw
        or ',' in raw
        or '/' in raw
        or '\\' in raw
        or '@' in raw
        or '%' in raw
    ):
        return '127.0.0.1'
    try:
        parsed = urlparse(f'//{raw}')
        hostname = parsed.hostname
        # Accessing .port performs strict numeric/range validation.
        parsed.port
    except (TypeError, ValueError):
        return '127.0.0.1'
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return '127.0.0.1'
    candidate = hostname.lower().rstrip('.')
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        if re.fullmatch(r'[0-9.]+', candidate):
            return '127.0.0.1'
        if len(candidate) > 253:
            return '127.0.0.1'
        label_re = re.compile(
            r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
        )
        labels = candidate.split('.')
        if not labels or any(not label_re.fullmatch(label) for label in labels):
            return '127.0.0.1'
        return candidate
    if address.version == 6:
        # RFC 3986 requires brackets around IPv6 literals in URL authority.
        if not raw.startswith('['):
            return '127.0.0.1'
        return f'[{address.compressed}]'
    return str(address)


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


def _normalized_ip(raw):
    value = str(raw or '').strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_loopback(address):
    if address is None:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, 'ipv4_mapped', None)
    return bool(mapped and mapped.is_loopback)


def request_client_ip(handler):
    """Return the real client IP when the request came through local nginx.

    The application only listens on loopback in production, so forwarding
    headers are trusted exclusively when the immediate peer is also loopback.
    This prevents a direct non-local caller from choosing its own rate-limit
    bucket while avoiding the opposite failure mode where every nginx-proxied
    visitor is treated as ``127.0.0.1``.
    """
    peer_raw = handler.client_address[0] if getattr(handler, 'client_address', None) else ''
    peer = _normalized_ip(peer_raw)
    if not _is_loopback(peer):
        return str(peer) if peer is not None else str(peer_raw or '')

    candidates = [
        handler.headers.get('X-Real-IP', ''),
        (handler.headers.get('X-Forwarded-For', '') or '').split(',', 1)[0],
    ]
    for raw in candidates:
        address = _normalized_ip(raw)
        if address is not None:
            return str(address)
    return str(peer) if peer is not None else str(peer_raw or '')


def _default_port(scheme):
    return 443 if scheme == 'https' else 80


def _request_origin(handler):
    raw_host = str(handler.headers.get('Host', '') or '').strip()
    try:
        parsed_host = urlparse(f'//{raw_host}')
        hostname = (parsed_host.hostname or '').lower().rstrip('.')
        host_port = parsed_host.port
    except (TypeError, ValueError):
        return None
    if not hostname or sanitize_host(raw_host) == '127.0.0.1' and hostname != '127.0.0.1':
        return None

    scheme = (
        str(handler.headers.get('X-Forwarded-Proto') or '')
        .split(',', 1)[0].strip().lower()
    )
    if scheme not in ('http', 'https'):
        scheme = None
    forwarded_port = (
        str(handler.headers.get('X-Forwarded-Port') or '')
        .split(',', 1)[0].strip()
    )
    if forwarded_port.isdigit() and 1 <= int(forwarded_port) <= 65535:
        port = int(forwarded_port)
    elif host_port is not None:
        port = int(host_port)
    elif scheme:
        port = _default_port(scheme)
    else:
        port = None
    return hostname, port, scheme


def _url_matches_request_host(handler, raw_url):
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return False
    if (
        parsed.scheme not in ('http', 'https')
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    request_origin = _request_origin(handler)
    if request_origin is None:
        return False
    hostname, port, scheme = request_origin
    candidate_hostname = parsed.hostname.lower().rstrip('.')
    try:
        candidate_port = parsed.port or _default_port(parsed.scheme)
    except ValueError:
        return False
    if hostname != candidate_hostname:
        return False
    if scheme and parsed.scheme != scheme:
        return False
    if port is not None and candidate_port != port:
        return False
    return True


def is_same_origin_post(handler):
    """Best-effort CSRF guard for browser-driven POSTs.

    Modern browsers provide Sec-Fetch-Site from network-layer request context;
    unlike a normal form field it cannot be forged by page JavaScript.  Prefer
    that signal so reverse proxies and privacy modes that emit ``Origin: null``
    do not reject a genuine same-origin admin form.  Origin/Referer remain the
    fallback for older browsers and local scripts keep their legacy behavior.
    """
    fetch_site = str(handler.headers.get('Sec-Fetch-Site') or '').strip().lower()
    if fetch_site == 'cross-site':
        return False
    if fetch_site in ('same-origin', 'none'):
        return True
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
