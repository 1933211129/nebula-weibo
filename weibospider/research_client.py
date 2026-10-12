"""Bounded, low-rate public-content requests. Never log headers or Cookie values."""
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class FetchError(Exception):
    def __init__(self, reason, status='access_restricted'):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class StopRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, cookie_path=None, max_requests=16, delay=3.0, timeout=25, cookie_value=None):
        path = Path(cookie_path or Path(__file__).with_name('cookie.txt'))
        self.cookie = cookie_value or os.environ.get('WEIBO_COOKIE') or path.read_text(encoding='utf-8').strip()
        self.max_requests = max_requests
        self.requests = 0
        self.delay = delay
        self.timeout = timeout
        self.last_request = 0
        self.opener = urllib.request.build_opener(StopRedirect())

    def cookie_header(self, host):
        if self.cookie.startswith('['):
            result = []
            for c in json.loads(self.cookie):
                domain = c.get('domain', '').lstrip('.')
                matches = host == domain or (not c.get('hostOnly') and host.endswith('.' + domain))
                if matches and c.get('path', '/') == '/':
                    result.append(c['name'] + '=' + c['value'])
            return '; '.join(result)
        # The supplied WBPSESS/XSRF cookies are host-only, unlike SUB/SCF.
        return '; '.join(part.strip() for part in self.cookie.split(';')
                         if host == 'weibo.com' or part.strip().split('=', 1)[0] not in ('WBPSESS', 'XSRF-TOKEN'))

    def get(self, endpoint, params=None, as_json=True):
        url = endpoint + ('?' + urllib.parse.urlencode(params) if params else '')
        host = urllib.parse.urlsplit(url).hostname
        if host not in ('weibo.com', 's.weibo.com') or not url.startswith('https://'):
            raise FetchError('unexpected_host')
        for attempt in range(2):
            if self.requests >= self.max_requests:
                raise FetchError('request_budget', 'incomplete')
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            cookie = self.cookie_header(host)
            headers = {'Cookie': cookie, 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
                       'Referer': 'https://weibo.com/', 'Accept': 'application/json,text/html;q=0.9'}
            if host == 'weibo.com':
                for part in cookie.split(';'):
                    if part.strip().startswith('XSRF-TOKEN='):
                        headers['X-XSRF-TOKEN'] = part.strip().split('=', 1)[1]
            self.requests += 1
            self.last_request = time.monotonic()
            try:
                with self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout) as response:
                    text = response.read().decode('utf-8', errors='replace')
            except urllib.error.HTTPError as exc:
                if exc.code >= 500 and attempt == 0:
                    continue
                raise FetchError('http_' + str(exc.code), 'not_visible' if exc.code == 404 else 'access_restricted') from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 0:
                    continue
                raise FetchError('network_error', 'failed') from None
            if not as_json:
                return text
            try:
                data = json.loads(text)
            except ValueError:
                raise FetchError('non_json_response') from None
            if not isinstance(data, dict):
                raise FetchError('unexpected_json_shape', 'failed')
            if data.get('ok') in (-100, '-100'):
                raise FetchError('login_expired', 'access_restricted')
            if data.get('ok') in (0, '0', False):
                message = str(data.get('message') or data.get('msg') or '')
                status = 'deleted' if '删除' in message else 'not_visible' if '不存在' in message else 'access_restricted'
                raise FetchError('api_error', status)
            return data
