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
