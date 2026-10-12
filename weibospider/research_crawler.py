"""Small-scale research crawler with explicit truncation and relationship tracking."""
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from research_client import FetchError
from research_models import (BEIJING, body, comment_record, ident, now, post_record,
                             text_fields, timestamp, user_record)

API = 'https://weibo.com/ajax/statuses/'


class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.has_next = False
        self.page_numbers = []
        self.no_results = False
        self.pagination_pages = []
        self.total_results = None
        self._page_list_depth = 0
        self._summary_tag = None

    def feed(self, data):
        super().feed(data)
        # Empty searches include unrelated recommended feed cards with valid mids.
        # Those are not search hits, even though their markup looks identical.
        if '未找到相关结果' in data:
            self.no_results = True
        if self.no_results:
            self.ids.clear()
            self.has_next = False
            self.page_numbers.clear()
            self.pagination_pages.clear()
            self.total_results = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        if tag == 'ul' and ('page-list' in classes or self._page_list_depth):
            self._page_list_depth += 1
        if set(classes).intersection(('m-result', 'result-count', 'search_num', 'page-info')):
            self._summary_tag = tag
        mid = attrs.get('mid', '')
        if mid.isdigit() and mid not in self.ids:
            self.ids.append(mid)
        if tag == 'a':
            if 'next' in attrs.get('class', '').split():
                self.has_next = True
            match = re.search(r'(?:[?&])page=(\d+)', attrs.get('href', ''))
            if match:
                self.page_numbers.append(int(match.group(1)))
                if self._page_list_depth:
                    self.pagination_pages.append(int(match.group(1)))

    def handle_endtag(self, tag):
        if tag == 'ul' and self._page_list_depth:
            self._page_list_depth -= 1
        if tag == self._summary_tag:
            self._summary_tag = None

    def handle_data(self, data):
        if self._summary_tag:
            match = re.search(r'共\s*\d+\s*页\s*/\s*([\d,，]+)\s*条', data)
            if not match:
                match = re.search(r'(?:找到|共)\s*约?\s*([\d,，]+)\s*条', data)
            if match:
                self.total_results = int(re.sub('[,，]', '', match.group(1)))


class Crawler:
    def __init__(self, client, store):
        self.client = client
        self.store = store

    def post(self, reference, query=None, allowed_interval=None):
        try:
            data = self.client.get(API + 'show', {'id': reference})
            post = post_record(data)
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            status = getattr(exc, 'status', 'failed')
            self.store.log('post', reference, status, reason=getattr(exc, 'reason', 'invalid_post_schema'))
            # Deleted/non-visible IDs remain represented; do not fabricate a post for a network failure.
            if str(reference).isdigit() and status in ('deleted', 'not_visible'):
                self.store.put('posts', dict(post_id=str(reference), retrieved_at=now(), retrieval_status=status))
            return None
        if allowed_interval:
            created = datetime.fromisoformat(post['created_at'])
            if not allowed_interval[0] <= created < allowed_interval[1]:
                self.store.log('search_boundary', post['post_id'], 'outside_time_range',
                               query_id=query['query_id'] if query else None, created_at=post['created_at'])
                # Keep the search hit, not an out-of-scope body in the study data.
                self.store.put('posts', dict(post_id=post['post_id'], created_at=post['created_at'],
                                           retrieval_status='outside_time_range'))
                return None
        self.store.put('users', user_record(data.get('user')))
        if query:
            self.store.match(post['post_id'], query)
        if data.get('isLongText'):
            try:
                result = self.client.get(API + 'longtext', {'id': data.get('mblogid') or post['post_id']})
                full = result.get('data', {}).get('longTextContent')
                if not isinstance(full, str) or not full:
                    raise FetchError('missing_long_text', 'incomplete')
                post['post_text'] = full.replace('\u200b', '')
                if post['is_repost']:
                    post['repost_text'] = post['post_text']
                merged = dict(data)
                merged['url_struct'] = data.get('url_struct', []) + result.get('data', {}).get('url_struct', [])
                post.update(text_fields(merged, post['post_text']))
            except FetchError as exc:
                post['retrieval_status'] = 'incomplete'
                self.store.log('long_text', post['post_id'], exc.status, reason=exc.reason)
        self.store.sync_matches(post)
        self.store.log('post', post['post_id'], post['retrieval_status'],
                       is_repost=post['is_repost'], is_long_text=bool(data.get('isLongText')), media_type=post['media_type'])
        return post

    def comments(self, post, max_pages=2, max_reply_pages=2, max_threads=3):
        pid = post['post_id']
        root_ids, reply_ids, planned = set(), set(), {}
        reasons = []
        first_exhausted = False
        seen_cursors = set()
        cursor = 0
        cursor_type = None
        for page in range(max_pages):
            params = dict(is_reload=1, id=pid, uid=post['user_id'], is_show_bulletin=2,
                          is_mix=0, count=20, flow=1, max_id=cursor)
            if cursor_type is not None:
                params['max_id_type'] = cursor_type
            try:
                data = self.client.get(API + 'buildComments', params)
                if not isinstance(data.get('data'), list):
                    raise FetchError('invalid_comment_page', 'failed')
            except FetchError as exc:
                reasons.append(exc.reason)
                self.store.log('comment_page', pid, exc.status, reason=exc.reason, cursor=str(cursor), page=page + 1)
                break
            self.store.log('comment_page', pid, 'normal', page=page + 1, cursor=str(cursor), count=len(data['data']))
            for raw in data['data']:
                try:
                    item = comment_record(raw, pid)
                except (ValueError, KeyError, TypeError):
                    reasons.append('invalid_comment_schema')
                    continue
                root_ids.add(item['comment_id'])
                self.store.put('comments', item)
                self.store.put('users', user_record(raw.get('user')))
                inline = raw.get('comments') or []
                for child in inline:
                    try:
                        reply = comment_record(child, pid, item['comment_id'], item['comment_user_id'])
                        self.store.put('comments', reply)
                        self.store.put('users', user_record(child.get('user')))
                        reply_ids.add(reply['comment_id'])
                    except (ValueError, KeyError, TypeError):
                        reasons.append('invalid_inline_reply')
                if raw.get('total_number', 0) or raw.get('more_info') or inline:
                    planned[item['comment_id']] = item
            next_cursor = data.get('max_id')
            if next_cursor is None:
                reasons.append('missing_comment_cursor')
                break
            if str(next_cursor) == '0':
                first_exhausted = True
                break
            if str(next_cursor) in seen_cursors or not data['data']:
                reasons.append('stalled_comment_cursor')
                break
            seen_cursors.add(str(next_cursor))
            cursor, cursor_type = next_cursor, data.get('max_id_type')
        if not first_exhausted and not reasons:
            reasons.append('first_level_page_limit')
        if len(planned) > max_threads:
            reasons.append('reply_thread_limit')
        for root in list(planned.values())[:max_threads]:
            found, errors = self.replies(pid, root, max_reply_pages)
            reply_ids.update(found)
            reasons.extend(errors)
        total = len(root_ids) + len(reply_ids)
        displayed = post.get('comments_count')
        if displayed is None:
            reasons.append('displayed_count_unavailable')
        elif displayed != total:
            reasons.append('displayed_count_mismatch')
        audit = dict(post_id=pid, displayed_comments_count=displayed, retrieved_comments_count=total,
                     retrieved_first_level_count=len(root_ids), retrieved_reply_count=len(reply_ids),
                     comments_complete=first_exhausted and not reasons,
                     incomplete_reason=sorted(set(reasons)), count_scope='current_run', sort_mode='time_flow_1')
        self.store.log('comments_summary', pid, 'normal' if audit['comments_complete'] else 'incomplete', **audit)
        return audit

    def replies(self, pid, root, max_pages):
        found, seen, reasons = set(), set(), []
        cursor, cursor_type, exhausted = 0, None, False
        for page in range(max_pages):
            params = dict(is_reload=1, id=root['comment_id'], uid=root['comment_user_id'], is_show_bulletin=2,
                          is_mix=1, fetch_level=1, max_id=cursor, count=20, flow=1)
            if cursor_type is not None:
                params['max_id_type'] = cursor_type
            try:
                data = self.client.get(API + 'buildComments', params)
                if not isinstance(data.get('data'), list):
                    raise FetchError('invalid_reply_page', 'failed')
            except FetchError as exc:
                reasons.append(exc.reason)
                self.store.log('reply_page', root['comment_id'], exc.status, reason=exc.reason, cursor=str(cursor))
                break
            self.store.log('reply_page', root['comment_id'], 'normal', count=len(data['data']), page=page + 1,
                           cursor=str(cursor), next_cursor=str(data.get('max_id')),
                           max_id_type=data.get('max_id_type'))
            for raw in data['data']:
                try:
                    item = comment_record(raw, pid, root['comment_id'], root['comment_user_id'])
                except (ValueError, KeyError, TypeError):
                    reasons.append('invalid_reply_schema')
                    continue
                self.store.put('comments', item)
                self.store.put('users', user_record(raw.get('user')))
                found.add(item['comment_id'])
            next_cursor = data.get('max_id')
            if next_cursor is None:
                reasons.append('missing_reply_cursor')
                break
            if str(next_cursor) == '0':
                exhausted = True
                break
            if str(next_cursor) in seen or not data['data']:
                reasons.append('stalled_reply_cursor')
                break
            seen.add(str(next_cursor))
            cursor, cursor_type = next_cursor, data.get('max_id_type')
        if not exhausted and not reasons:
            reasons.append('reply_page_limit')
        if root.get('reply_count') is not None and len(found) != root['reply_count']:
            reasons.append('reply_count_mismatch')
        return found, reasons

    def search(self, query, start, end, max_pages=2, max_posts=3):
        """Bounded window scan. Saturated windows split down to one hour within the same budget."""
        self.store.put('queries', query)
        start, end = datetime.fromisoformat(timestamp(start)), datetime.fromisoformat(timestamp(end))
        if start >= end:
            raise ValueError('start must precede exclusive end')
        # Search API accepts hours; allow boundary overfetch then filter post timestamps exactly.
        start_hour = start.replace(minute=0, second=0, microsecond=0)
        end_hour = end.replace(minute=0, second=0, microsecond=0)
        if end_hour < end:
            end_hour += timedelta(hours=1)
        pending = [(start_hour, end_hour)]
        posts, attempted = [], set()
        while pending:
            lo, hi = pending.pop(0)
            window = '{}:{}'.format(lo.isoformat(), hi.isoformat())
            for page in range(1, max_pages + 1):
                try:
                    text = self.client.get('https://s.weibo.com/weibo', dict(q=query['query'], timescope='custom:{}:{}'.format(lo.strftime('%Y-%m-%d-%H'), hi.strftime('%Y-%m-%d-%H')), page=page), as_json=False)
                except FetchError as exc:
                    self.store.log('search_window', query['query_id'], exc.status, window=window, page=page, reason=exc.reason)
                    return posts
                parsed = SearchParser()
                parsed.feed(text)
                if not parsed.ids:
                    no_result = '未找到相关结果' in text
                    self.store.log('search_window', query['query_id'], 'normal' if no_result else 'access_restricted',
                                   window=window, page=page, reason='no_visible_results' if no_result else 'unrecognized_search_page',
                                   complete=no_result)
                    break
                # Keep every observed match before deduplicating detail requests.
                for pid in parsed.ids:
                    self.store.match(pid, query)
                if max(parsed.page_numbers or [1]) >= 50 and hi - lo > timedelta(hours=1):
                    hours = int((hi - lo).total_seconds() // 3600)
                    middle = lo + timedelta(hours=max(1, hours // 2))
                    pending[0:0] = [(lo, middle), (middle, hi)]
                    self.store.log('search_window', query['query_id'], 'split', window=window,
                                   children=[[lo.isoformat(), middle.isoformat()], [middle.isoformat(), hi.isoformat()]],
                                   reason='page_cap_detected')
                    break
                for pid in parsed.ids:
                    if pid in attempted:
                        continue
                    if len(attempted) >= max_posts:
                        self.store.log('search_window', query['query_id'], 'incomplete', window=window, page=page, reason='post_limit')
                        return posts
                    attempted.add(pid)
                    post = self.store.get('posts', pid)
                    if not post or post.get('retrieval_status') != 'normal':
                        post = self.post(pid, query, allowed_interval=(start, end))
                    if post and post.get('created_at'):
                        created = datetime.fromisoformat(post['created_at'])
                        if start <= created < end:
                            posts.append(post)
                        else:
                            # Boundary search matches are evidence, but outside-scope posts are excluded from this result.
                            self.store.log('search_boundary', pid, 'outside_time_range', query_id=query['query_id'])
                if max(parsed.page_numbers or [1]) >= 50 and hi - lo <= timedelta(hours=1):
                    self.store.log('search_window', query['query_id'], 'incomplete', window=window, reason='hourly_search_cap')
                    break
                if not parsed.has_next:
                    self.store.log('search_window', query['query_id'], 'normal', window=window, complete=True, meaning='visible_search_pages_exhausted')
                    break
                if page == max_pages:
                    self.store.log('search_window', query['query_id'], 'incomplete', window=window, reason='search_page_limit')
        return posts
