"""Bounded, DNS-pinned web reads and MCP Streamable HTTP transport."""
import asyncio
from html.parser import HTMLParser
import ipaddress
import json
from urllib.parse import urlsplit, urljoin
from uuid import uuid4

import httpx

from .service_probe import resolve_addresses
from .chat_workspace_store import WorkspaceError


def validate_url(raw, *, private=False):
    parsed = urlsplit(raw)
    if (len(raw) > 2048 or parsed.scheme not in ('https', 'http') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or '\\' in raw or any(ord(c) <= 32 for c in raw)):
        raise WorkspaceError('invalid_tool_url')
    if parsed.scheme == 'http' and not private:
        raise WorkspaceError('https_required')
    return httpx.URL(raw)


async def pinned_request(method, raw_url, *, headers=None, payload=None, private=False, wanted_id=None, notification=False, transport=None):
    url = validate_url(raw_url, private=private)
    addresses = await resolve_addresses(url.host, url.port or (443 if url.scheme == 'https' else 80))
    if not addresses or not private and any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise WorkspaceError('private_network_blocked')
    pinned = url.copy_with(host=addresses[0])
    request_headers = {**(headers or {}), 'Host': url.netloc.decode('ascii')}
    async with asyncio.timeout(45):
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=5), trust_env=False,
                                      follow_redirects=False, transport=transport) as client:
            async with client.stream(method, pinned, headers=request_headers, json=payload,
                                     extensions={'sni_hostname': url.host}) as response:
                if notification and response.status_code in (200, 202, 204):
                    return response.status_code, dict(response.headers), b''
                content = bytearray()
                buffer = ''
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 2 * 1024 * 1024:
                        raise WorkspaceError('tool_response_too_large')
                    if wanted_id is not None and 'text/event-stream' in response.headers.get('content-type', ''):
                        # Decode incrementally so split UTF-8 code points remain valid.
                        buffer = content.decode('utf-8', errors='replace').replace('\r\n', '\n')
                        for event in buffer.split('\n\n')[:-1]:
                            data = '\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
                            if not data:
                                continue
                            try:
                                value = json.loads(data)
                            except ValueError:
                                continue
                            if isinstance(value, dict) and value.get('id') == wanted_id:
                                return response.status_code, dict(response.headers), json.dumps(value).encode()
                return response.status_code, dict(response.headers), bytes(content)


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.hidden += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'tr'):
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


async def read_page(url):
    for _ in range(4):
        status, headers, body = await pinned_request('GET', url, headers={'Accept': 'text/html,text/plain', 'User-Agent': 'PersonalLearningWorkspace/1.0'})
        if status in (301, 302, 303, 307, 308) and headers.get('location'):
            url = urljoin(url, headers['location'])
            continue
        if status != 200:
            raise WorkspaceError('web_read_failed', 502)
        media = headers.get('content-type', '').split(';')[0]
        if media not in ('text/html', 'text/plain', 'application/xhtml+xml'):
            raise WorkspaceError('web_content_unsupported')
        text = body.decode('utf-8', errors='replace')
        if media != 'text/plain':
            parser = PageText(); parser.feed(text); text = ''.join(parser.text)
        return {'url': url, 'text': text[:20000], 'truncated': len(text) > 20000}
    raise WorkspaceError('web_redirect_limit')


class MCPClient:
    """One explicit operation per session; never automatically replays tools/call."""
    def __init__(self, server, *, transport=None):
        self.server = server
        self.transport = transport
        self.session = None
        self.version = '2025-11-25'

    async def rpc(self, method, params=None, *, notification=False):
        identifier = str(uuid4())
        message = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if not notification:
            message['id'] = identifier
        headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json', 'MCP-Protocol-Version': self.version}
        if self.server.get('token'):
            headers['Authorization'] = 'Bearer ' + self.server['token']
        if self.session:
            headers['MCP-Session-Id'] = self.session
        status, response_headers, body = await pinned_request('POST', self.server['url'], headers=headers,
            payload=message, private=self.server.get('allow_private', False), wanted_id=identifier,
            notification=notification, transport=self.transport)
        if not 200 <= status < 300:
            raise WorkspaceError('mcp_request_failed', 502)
        if response_headers.get('mcp-session-id'):
            session = response_headers['mcp-session-id']
            if len(session) > 256 or any(not 33 <= ord(c) <= 126 for c in session):
                raise WorkspaceError('mcp_invalid_response', 502)
            self.session = session
        if notification:
            return None
        try:
            value = json.loads(body)
        except ValueError:
            raise WorkspaceError('mcp_invalid_response', 502) from None
        if not isinstance(value, dict) or value.get('id') != identifier or 'error' in value or 'result' not in value:
            raise WorkspaceError('mcp_invalid_response', 502)
        return value['result']

    async def initialize(self):
        result = await self.rpc('initialize', {'protocolVersion': self.version, 'capabilities': {}, 'clientInfo': {'name': 'personal-learning', 'version': '1.0'}})
        if result.get('protocolVersion') not in ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'):
            raise WorkspaceError('mcp_protocol_unsupported')
        self.version = result['protocolVersion']
        await self.rpc('notifications/initialized', notification=True)

    async def tools(self):
        await self.initialize()
        cursor, tools = None, []
        for _ in range(5):
            result = await self.rpc('tools/list', {'cursor': cursor} if cursor else {})
            for tool in result.get('tools', []):
                if (not isinstance(tool, dict) or not isinstance(tool.get('name'), str)
                        or len(tool['name']) > 128 or len(json.dumps(tool)) > 16000):
                    continue
                tools.append({key: tool[key] for key in ('name', 'description', 'inputSchema', 'annotations') if key in tool})
                if len(tools) == 100:
                    return tools
            cursor = result.get('nextCursor')
            if not cursor:
                break
        return tools

    async def call(self, name, arguments):
        await self.initialize()
        return await self.rpc('tools/call', {'name': name, 'arguments': arguments})

    async def close(self):
        if self.session:
            headers = {'MCP-Session-Id': self.session, 'MCP-Protocol-Version': self.version}
            if self.server.get('token'):
                headers['Authorization'] = 'Bearer ' + self.server['token']
            try:
                await pinned_request('DELETE', self.server['url'], headers=headers, private=self.server.get('allow_private', False), notification=True, transport=self.transport)
            except Exception:
                pass
