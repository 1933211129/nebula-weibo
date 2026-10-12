"""Weibo transport; no persistence, no implicit retries, no credential logging."""

from urllib.parse import urlsplit
import threading
import httpx
from server.db import ROOT  # establishes legacy parser import compatibility
from research_client import Client

API = 'https://weibo.com/ajax/statuses/'
_local = threading.local()


def pooled_get(endpoint, **kwargs):
    client = getattr(_local, 'client', None)
    if client is None or client.is_closed:
        client = _local.client = httpx.Client(timeout=25, follow_redirects=False)
    # Credentials are provided explicitly per request; never replay Set-Cookie
    # from a previous account handled by an API executor thread.
    client.cookies.clear()
    return client.get(endpoint, **kwargs)


def close_transport():
    client = getattr(_local, 'client', None)
    if client is not None:
        client.close()
        _local.client = None


class RemoteError(Exception):
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__(code)


def fetch(value, endpoint, params):
    host = urlsplit(endpoint).hostname
    if host not in ('weibo.com', 's.weibo.com') or urlsplit(endpoint).scheme != 'https':
        raise RemoteError('unexpected_host')
    helper = Client(cookie_value=value)
    cookie = helper.cookie_header(host)
    headers = {
        'Cookie': cookie,
        'Referer': 'https://weibo.com/',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
    }
    if host == 'weibo.com':
        for part in cookie.split(';'):
            if part.strip().startswith('XSRF-TOKEN='):
                headers['X-XSRF-TOKEN'] = part.strip().split('=', 1)[1]
    try:
        response = pooled_get(
            endpoint, params=params, headers=headers, timeout=25, follow_redirects=False
        )
    except httpx.HTTPError:
        raise RemoteError('network_error') from None
    code = response.status_code
    if code in (301, 302, 303, 307, 308, 401):
        raise RemoteError('login_expired', code)
    if code in (403, 418):
        raise RemoteError('access_restricted', code)
    if code == 429:
        raise RemoteError('rate_limited', code)
    if code == 404:
        raise RemoteError('not_visible', code)
    if code != 200:
        raise RemoteError('http_' + str(code), code)
    if host == 's.weibo.com':
        return response.text
    try:
        data = response.json()
    except ValueError:
        raise RemoteError('non_json_response', code) from None
    if not isinstance(data, dict):
        raise RemoteError('invalid_response', code)
    if data.get('ok') in (-100, '-100'):
        raise RemoteError('login_expired', code)
    if data.get('ok') in (0, '0', False):
        message = str(data.get('message') or data.get('msg') or '')
        reason = (
            'deleted'
            if '删除' in message
            else 'not_visible'
            if '不存在' in message
            else 'access_restricted'
        )
        raise RemoteError(reason, code)
    return data
