import asyncio
import httpx
import pytest
from web_api.service_probe import ProbeInput, resolve_target, run_probe


def run(coro):
    return asyncio.run(coro)


def test_local_gateway_mapping_is_exact():
    target = run(resolve_target('https://lancoai.site:9445/v1'))
    assert str(target[0]) == 'http://127.0.0.1:8317/v1'
    for url in ['http://127.0.0.1:8317/v1', 'https://127.0.0.1/v1', 'https://169.254.169.254/v1', 'https://[::1]/v1', 'https://user:pass@lancoai.site:9445/v1', 'https://lancoai.site:9445/v1?url=x']:
        with pytest.raises(ValueError):
            run(resolve_target(url))


def test_dns_is_pinned_and_private_answers_rejected(monkeypatch):
    async def resolve(host, port):
        return ['1.1.1.1']
    monkeypatch.setattr('web_api.service_probe.resolve_addresses', resolve)
    url, headers, extensions = run(resolve_target('https://api.example.com/v1'))
    assert str(url) == 'https://1.1.1.1/v1'
    assert headers['Host'] == 'api.example.com'
    assert extensions['sni_hostname'] == 'api.example.com'
    async def private(host, port):
        return ['1.1.1.1', '10.0.0.1']
    monkeypatch.setattr('web_api.service_probe.resolve_addresses', private)
    with pytest.raises(ValueError):
        run(resolve_target('https://api.example.com/v1'))


def test_models_verified_without_exposing_key():
    def respond(request):
        assert request.url == 'http://127.0.0.1:8317/v1/models'
        assert request.headers['authorization'] == 'Bearer sk-private'
        return httpx.Response(200, json={'data': [{'id': 'model-a'}, {'id': 'model-a'}, {'id': 'model-b'}]})
    result = run(run_probe(ProbeInput(api_base='https://lancoai.site:9445/v1', api_key='sk-private'), transport=httpx.MockTransport(respond)))
    assert result['status'] == 'verified'
    assert result['models'] == ['model-a', 'model-b']
    assert 'sk-private' not in str(result)
    assert result['endpoint'] == '/models'


@pytest.mark.parametrize('code,status', [(401,'authentication_failed'),(403,'permission_denied'),(404,'not_found'),(429,'rate_limited'),(503,'upstream_error'),(302,'redirect_blocked')])
def test_probe_classifies_failure_without_returning_response_body(code, status):
    result = run(run_probe(ProbeInput(api_base='https://lancoai.site:9445/v1', api_key='sk-private'), transport=httpx.MockTransport(lambda request: httpx.Response(code, text='secret upstream body', headers={'Location':'http://127.0.0.1/secret'}))))
    assert result['status'] == status
    assert 'secret upstream body' not in str(result)


def test_chat_requires_real_reply_and_model_list_is_not_chat_proof():
    value = ProbeInput(api_base='https://lancoai.site:9445/v1', api_key='sk-test', kind='chat', model='model-a')
    result = run(run_probe(value, transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'data':[]}))))
    assert result['status'] == 'invalid_response'
    result = run(run_probe(value, transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}]}))))
    assert result['status'] == 'verified'
    assert result['endpoint'] == '/chat/completions'


def test_probe_rejects_large_response_and_header_injection():
    result = run(run_probe(ProbeInput(api_base='https://lancoai.site:9445/v1', api_key='sk-test'), transport=httpx.MockTransport(lambda request: httpx.Response(200,content=b'x' * 600000))))
    assert result['status'] == 'response_too_large'
    with pytest.raises(ValueError):
        ProbeInput(api_base='https://lancoai.site:9445/v1', api_key='sk-test\r\nCookie: secret')


def test_timeout_is_classified_without_error_details():
    def fail(request):
        raise httpx.ReadTimeout('upstream detail with sensitive data')
    result = run(run_probe(ProbeInput(api_base='https://lancoai.site:9445/v1', api_key='sk-test'), transport=httpx.MockTransport(fail)))
    assert result['status'] == 'timeout'
    assert 'sensitive' not in str(result)
