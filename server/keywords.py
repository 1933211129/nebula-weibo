"""Shared parsing rules for pasted search expressions."""

import re
import unicodedata
from html import unescape


def cross_keywords(left, right):
    a, b = parse_keywords(left), parse_keywords(right)
    if bool(a) != bool(b):
        raise ValueError('组合检索的两组关键词都需要填写')
    if len(a) * len(b) > 1000:
        raise ValueError('组合超过 1000 项，请缩小词组或分批创建')
    return list(dict.fromkeys(' '.join(dict.fromkeys((x, y))) for x in a for y in b))


def missing_terms(text, terms):
    """Literal AND check, Unicode-normalized and case-insensitive; never semantic expansion."""
    clean = unicodedata.normalize('NFKC', unescape(re.sub(r'<[^>]*>', '', text or ''))).casefold()
    return [term for term in terms if unicodedata.normalize('NFKC', term).casefold() not in clean]


def parse_keywords(text, mode='words'):
    if mode not in ('words', 'phrases'):
        raise ValueError('不支持的关键词拆分方式')
    text = re.sub(r'(?m)^\s*[·•●▪◦‣]+\s*', '', text)
    parts = re.split(r'[,，、;；\s]+' if mode == 'words' else r'[,，、;；\r\n]+', text)
    words = list(dict.fromkeys(' '.join(p.split()) for p in parts if p.strip()))
    if len(words) > 1000:
        raise ValueError('每个任务最多支持 1000 个检索词，请分批创建')
    if any(len(word) > 200 for word in words):
        raise ValueError('单个检索词最多 200 个字符，请检查拆分方式')
    return words
