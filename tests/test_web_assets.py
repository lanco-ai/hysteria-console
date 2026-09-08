"""Extracted scripts remain versioned, explicitly allowlisted resources."""

import http.client
import importlib.util

import pytest


def test_script_tag_resolves_to_exact_served_content():
    assert importlib.util.find_spec('web_assets'), 'Extracted scripts need a shared asset registry'
    import web_assets

    entry = web_assets.ASSETS['/static/login.js']
    payload, etag = entry
    assert payload
    assert f'?v={etag.strip(chr(34))}' in web_assets.script_tag('login')
    assert '../subscription_service.py' not in web_assets.ASSETS


def test_extracted_scripts_are_served_and_revalidated_over_http():
    import web_assets

    from tests.test_product_ux_regressions import _running_server

    with _running_server() as server:
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            for path, (payload, etag) in web_assets.ASSETS.items():
                url = path + '?v=' + etag.strip('"')
                connection.request('GET', url, headers={'Host': 'panel.test'})
                response = connection.getresponse()
                assert response.read() == payload
                assert response.status == 200
                assert response.getheader('Cache-Control') == 'public, max-age=31536000, immutable'
                connection.request(
                    'GET', url, headers={'Host': 'panel.test', 'If-None-Match': etag}
                )
                cached = connection.getresponse()
                assert cached.read() == b''
                assert cached.status == 304
        finally:
            connection.close()
    with pytest.raises(KeyError):
        web_assets.script_tag('../subscription_service')
