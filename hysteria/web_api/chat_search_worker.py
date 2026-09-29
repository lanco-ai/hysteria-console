"""Disposable public web search; stdout contains bounded normalized results only."""
import json
import resource
import sys
from urllib.parse import urlsplit

resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024,) * 2)

try:
    from ddgs import DDGS
    value = json.loads(sys.stdin.buffer.read(10000))
    results = DDGS(timeout=12).text(value['query'], max_results=6, backend='auto')
    safe = []
    for item in results[:6]:
        url = item.get('href', '')
        parsed = urlsplit(url)
        if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password:
            safe.append({'title': str(item.get('title', ''))[:300], 'url': url[:2048], 'snippet': str(item.get('body', ''))[:2000]})
    sys.stdout.write(json.dumps({'results': safe}, ensure_ascii=False))
except Exception:
    sys.stdout.write('{"error":"web_search_unavailable"}')
    raise SystemExit(1)
