from io import BytesIO

import pytest

import http_utils


class _Handler:
    def __init__(self, content_length, body=b'', transfer_encoding=None):
        self.headers = {}
        if content_length is not None:
            self.headers['Content-Length'] = content_length
        if transfer_encoding is not None:
            self.headers['Transfer-Encoding'] = transfer_encoding
        self.rfile = BytesIO(body)


def test_parse_form_accepts_valid_body():
    body = b'user=alice&n=10'
    form = http_utils.parse_form(_Handler(str(len(body)), body))
    assert form['user'] == ['alice']
    assert form['n'] == ['10']


def test_parse_form_accepts_explicit_empty_body_for_action_forms():
    assert http_utils.parse_form(_Handler('0')) == {}


def test_parse_form_rejects_truncated_body():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('5', b'a=1'))


def test_parse_form_rejects_invalid_utf8():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('1', b'\xff'))


def test_parse_form_rejects_too_many_fields():
    body = '&'.join(
        f'field-{index}=1'
        for index in range(http_utils.MAX_FORM_FIELDS + 1)
    ).encode()
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler(str(len(body)), body))


def test_parse_form_rejects_negative_content_length():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('-1'))


def test_parse_form_rejects_invalid_content_length():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('not-an-int'))


def test_parse_form_rejects_oversized_content_length():
    with pytest.raises(http_utils.RequestTooLarge):
        http_utils.parse_form(_Handler(str(http_utils.MAX_FORM_BYTES + 1)))


def test_parse_form_requires_one_unambiguous_content_length():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler(None))

    class DuplicateHeaders(dict):
        def get_all(self, name, default):
            if name.lower() == 'content-length':
                return ['3', '3']
            return default

    handler = _Handler('3', b'a=1')
    handler.headers = DuplicateHeaders(handler.headers)
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(handler)


def test_parse_form_rejects_transfer_encoding_and_invalid_escapes():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(
            _Handler('3', b'a=1', transfer_encoding='chunked')
        )
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('5', b'a=%ZZ'))
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('5', b'a=%E4'))


def test_parse_form_rejects_non_form_content_types():
    handler = _Handler('2', b'{}')
    handler.headers['Content-Type'] = 'application/json'
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(handler)

    handler = _Handler('3', b'a=1')
    handler.headers['Content-Type'] = (
        'application/x-www-form-urlencoded; charset=latin-1'
    )
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(handler)


def test_safe_base_url_preserves_nonstandard_https_port():
    assert http_utils.safe_base_url('203.0.113.10', 'https', '9444') == \
        'https://203.0.113.10:9444'
    assert http_utils.safe_base_url('panel.example.com', 'https', '443') == \
        'https://panel.example.com'


def test_safe_base_url_rejects_invalid_forwarded_port():
    assert http_utils.safe_base_url('panel.example.com', 'https', 'not-a-port') == \
        'https://panel.example.com'
    assert http_utils.safe_base_url('panel.example.com', 'https', '70000') == \
        'https://panel.example.com'


def test_sanitize_host_canonicalizes_bracketed_ipv6_without_duplicate_port():
    host = http_utils.sanitize_host('[2001:0db8::1]:9443')

    assert host == '[2001:db8::1]'
    assert http_utils.safe_base_url(host, 'https', '9443') == (
        'https://[2001:db8::1]:9443'
    )


@pytest.mark.parametrize(
    'raw',
    (
        '2001:db8::1',
        '[fe80::1%eth0]:9443',
        'user@panel.example.com',
        'panel.example.com:bad',
        'panel..example.com',
        'panel.example.com,attacker.example',
        '192.168.001.010',
    ),
)
def test_sanitize_host_rejects_ambiguous_or_invalid_authorities(raw):
    assert http_utils.sanitize_host(raw) == '127.0.0.1'


def test_same_origin_post_accepts_matching_origin():
    handler = _Handler(None)
    handler.headers.update({
        'Host': 'panel.example.com:9444',
        'Origin': 'https://panel.example.com:9444',
    })

    assert http_utils.is_same_origin_post(handler) is True


def test_same_origin_post_rejects_mismatched_origin_without_fetch_metadata():
    handler = _Handler(None)
    handler.headers.update({
        'Host': 'panel.example.com:9444',
        'Origin': 'https://other.example.com',
    })

    assert http_utils.is_same_origin_post(handler) is False


def test_same_site_origin_on_a_different_port_is_rejected():
    handler = _Handler(None)
    handler.headers.update({
        'Host': 'panel.example.com:9444',
        'X-Forwarded-Proto': 'https',
        'X-Forwarded-Port': '9444',
        'Origin': 'https://panel.example.com:8443',
        'Sec-Fetch-Site': 'same-site',
    })

    assert http_utils.is_same_origin_post(handler) is False


def test_origin_scheme_must_match_trusted_forwarded_scheme():
    handler = _Handler(None)
    handler.headers.update({
        'Host': 'panel.example.com',
        'X-Forwarded-Proto': 'https',
        'X-Forwarded-Port': '443',
        'Origin': 'http://panel.example.com',
    })

    assert http_utils.is_same_origin_post(handler) is False


def test_same_origin_fetch_metadata_handles_null_origin_from_proxy_or_privacy_mode():
    handler = _Handler(None)
    handler.headers.update({
        'Host': 'panel.example.com:9444',
        'Origin': 'null',
        'Sec-Fetch-Site': 'same-origin',
    })

    assert http_utils.is_same_origin_post(handler) is True


def test_cross_site_fetch_metadata_is_rejected_even_with_matching_origin():
    handler = _Handler(None)
    handler.headers.update({
        'Host': 'panel.example.com:9444',
        'Origin': 'https://panel.example.com:9444',
        'Sec-Fetch-Site': 'cross-site',
    })

    assert http_utils.is_same_origin_post(handler) is False


def test_user_initiated_fetch_metadata_allows_null_origin():
    handler = _Handler(None)
    handler.headers.update({
        'Host': 'panel.example.com:9444',
        'Origin': 'null',
        'Sec-Fetch-Site': 'none',
    })

    assert http_utils.is_same_origin_post(handler) is True
