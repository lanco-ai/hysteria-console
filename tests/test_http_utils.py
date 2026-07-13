from io import BytesIO

import pytest

import http_utils


class _Handler:
    def __init__(self, content_length, body=b''):
        self.headers = {}
        if content_length is not None:
            self.headers['Content-Length'] = content_length
        self.rfile = BytesIO(body)


def test_parse_form_accepts_valid_body():
    form = http_utils.parse_form(_Handler('17', b'user=alice&n=10'))
    assert form['user'] == ['alice']
    assert form['n'] == ['10']


def test_parse_form_rejects_negative_content_length():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('-1'))


def test_parse_form_rejects_invalid_content_length():
    with pytest.raises(http_utils.BadRequest):
        http_utils.parse_form(_Handler('not-an-int'))


def test_parse_form_rejects_oversized_content_length():
    with pytest.raises(http_utils.RequestTooLarge):
        http_utils.parse_form(_Handler(str(http_utils.MAX_FORM_BYTES + 1)))


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
