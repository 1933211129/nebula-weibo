"""Public-content schemas; all timestamps use Beijing time, all IDs are strings."""
import hashlib
import html
import json
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
CONFIG = Path(__file__).with_name('research_queries.json')


def now():
    return datetime.now(BEIJING).isoformat(timespec='seconds')


def timestamp(value):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, '%a %b %d %H:%M:%S %z %Y')
    except ValueError:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING)
    return dt.astimezone(BEIJING).isoformat(timespec='seconds')


def ident(value):
    return str(value) if value not in (None, '', 0, '0') else None


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.links = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'br':
            self.parts.append('\n')
        if tag == 'img':
            self.parts.append(attrs.get('alt', ''))
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])


def plain(value):
    p = TextParser()
    p.feed(value or '')
    return ''.join(p.parts).replace('\u200b', '')


def body(data):
    # text_raw is already plain text: do not strip literal <...> from it.
    if data.get('text_raw') is not None:
        return data['text_raw'].replace('\u200b', '')
    return plain(data.get('text', ''))


def user_record(data):
    if not data or not ident(data.get('idstr') or data.get('id')):
        return None
    return dict(user_id=ident(data.get('idstr') or data.get('id')),
                screen_name=data.get('screen_name'), verified=data.get('verified'),
                verified_type=data.get('verified_type'), verified_reason=data.get('verified_reason'),
                followers_count=data.get('followers_count'), follow_count=data.get('friends_count'),
                statuses_count=data.get('statuses_count'), user_description=data.get('description'),
                profile_retrieved_at=now())


def text_fields(data, text):
    topics = set(re.findall(r'#([^#\n]+)#', text))
    topics.update(t['topic_title'] for t in data.get('topic_struct', []) if t.get('topic_title'))
    mentions = set(re.findall(r'@([^\s@:#，。！？、<>]+)', text))
    urls = set(re.findall(r'https?://[^\s<>\[\]，。！？]+', text))
    for u in data.get('url_struct', []):
        # Preserve source links, not the API's thumbnail or player addresses.
        if u.get('long_url') or u.get('short_url'):
            urls.add(u.get('long_url') or u['short_url'])
    return dict(topics=sorted(topics), mentions=sorted(mentions), urls=sorted(urls))


def post_record(data):
    pid = ident(data.get('mid') or data.get('idstr') or data.get('id'))
    if not pid or not data.get('created_at') or not data.get('user'):
        raise ValueError('invalid_post_schema')
    user = data['user']
    uid = ident(user.get('idstr') or user.get('id'))
    original = data.get('retweeted_status') or {}
    is_repost = bool(original) or bool(data.get('retweet_id'))
    text = body(data)
    created = timestamp(data['created_at'])
    page = data.get('page_info') or {}
    videos = set()
    if page.get('object_type') == 'video' or page.get('type') == 'video':
        videos.add(str(page.get('object_id') or page.get('page_id') or 'primary'))
    for entry in (data.get('mix_media_info') or {}).get('items', []):
        if entry.get('type') == 'video':
            videos.add(str(entry.get('id') or len(videos)))
    images = data.get('pic_num')
    if images is None:
        images = len(data.get('pic_ids') or data.get('pics') or [])
    return dict(post_id=pid, post_url='https://weibo.com/{}/{}'.format(uid, data.get('mblogid') or pid),
                post_text=text, user_id=uid, created_at=created, year_month=created[:7],
                is_original=not is_repost, is_repost=is_repost,
                reposted_post_id=ident(original.get('mid') or original.get('idstr') or original.get('id') or data.get('retweet_id')),
                reposted_user_id=ident((original.get('user') or {}).get('idstr') or (original.get('user') or {}).get('id')),
                repost_text=text if is_repost else None, **text_fields(data, text),
                media_type='image_video' if images and videos else 'video' if videos else 'image' if images else 'none',
                image_count=images, video_count=len(videos), post_source=plain(data.get('source')),
                likes_count=data.get('attitudes_count'), comments_count=data.get('comments_count'),
                reposts_count=data.get('reposts_count'), favorites_count=data.get('favorites_count'),
                ip_location=data.get('region_name'), query_id=[], matched_keyword=[],
                retrieved_at=now(), retrieval_status='normal')


def comment_record(data, post_id, root_id=None, root_user_id=None):
    cid = ident(data.get('idstr') or data.get('id'))
    if not cid or not data.get('created_at'):
        raise ValueError('invalid_comment_schema')
    reply = data.get('reply_comment') or {}
    parent = ident(reply.get('idstr') or reply.get('id')) if root_id else None
    target = ident((reply.get('user') or {}).get('idstr') or (reply.get('user') or {}).get('id')) if root_id else None
    if root_id and (not parent or parent == str(root_id)):
        target = target or root_user_id
    # API models all replies under a root; an explicit reply_comment identifies the direct parent.
    source = plain(data.get('source'))
    location = data.get('region_name')
    if source.startswith(('来自', '发布于', 'IP属地')):
        location = location or source
        source = None
    return dict(comment_id=cid, post_id=str(post_id), parent_comment_id=(parent or root_id) if root_id else None,
                root_comment_id=str(root_id or cid), comment_level=2 if root_id else 1,
                comment_text=body(data), comment_user_id=ident((data.get('user') or {}).get('idstr') or (data.get('user') or {}).get('id')),
                reply_to_user_id=target if root_id else None,
                created_at=timestamp(data['created_at']), likes_count=data.get('like_counts', data.get('like_count')),
                reply_count=data.get('total_number'), comment_source=source or None, ip_location=location,
                retrieved_at=now(), retrieval_status='normal')


def load_queries(path=CONFIG):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    result = []
    for group, words in cfg['groups'].items():
        terms = [(w,) for w in words['direct']]
        terms += [(a, b) for a in words['left'] for b in words['right']]
        for parts in terms:
            query = ' '.join(parts)
            qid = hashlib.sha256((group + '\0' + query).encode()).hexdigest()[:16]
            result.append(dict(query_id=qid, category=group, query=query, terms=list(parts)))
    return cfg, result
