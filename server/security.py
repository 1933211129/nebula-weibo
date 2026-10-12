import hashlib
import json
import os
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request


def cipher():
    return Fernet(os.environ['COOKIE_ENCRYPTION_KEY'].encode())


def normalize_cookie(value):
    value = value.strip()
    if not value or len(value) > 64000 or '\r' in value:
        raise ValueError('Cookie 为空或格式无效')
    if value.startswith('['):
        data = json.loads(value)
        if not isinstance(data, list) or not all(
            isinstance(x, dict)
            and isinstance(x.get('name'), str)
            and isinstance(x.get('value'), str)
            for x in data
        ):
            raise ValueError('请输入浏览器导出的 Cookie 数组或请求头字符串')
        parts = {
            x['name']: x['value']
            for x in data
            if x.get('domain', '').lstrip('.') in ('weibo.com', 's.weibo.com')
        }
        normalized = json.dumps(data, ensure_ascii=False)
    else:
        if '\n' in value:
            raise ValueError('Cookie 请求头不能包含换行')
        parts = dict(p.strip().split('=', 1) for p in value.split(';') if '=' in p)
        normalized = '; '.join(k + '=' + v for k, v in parts.items())
    if not parts.get('SUB'):
        raise ValueError('缺少 SUB 登录凭据，请复制完整 Cookie')
    if any('\n' in str(v) or '\r' in str(v) for v in parts.values()):
        raise ValueError('Cookie 含有非法换行')
    return normalized, hashlib.sha256(parts['SUB'].encode()).hexdigest()


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode()


def same_origin(request: Request):
    """Reject cross-site mutations without user accounts or sessions."""
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        from urllib.parse import urlsplit

        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site') == 'cross-site' or (
            origin and urlsplit(origin).netloc != request.headers.get('host')
        ):
            raise HTTPException(403, '请求来源不匹配')
    return True
