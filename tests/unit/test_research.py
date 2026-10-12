import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'weibospider'))
from research_client import Client, FetchError
from research_crawler import Crawler, SearchParser
from research_models import comment_record, load_queries, post_record, timestamp, user_record
from research_store import Store


def user(uid=7):
    return dict(id=uid, screen_name='test', verified=False, verified_type=-1)


def post(pid=123):
    return dict(
        mid=str(pid),
        mblogid='test',
        created_at='Wed Jul 01 10:00:00 +0800 2026',
        user=user(),
        text_raw='测试 <literal> #心理健康# @账号 https://example.org',
        comments_count=0,
        attitudes_count=2,
        reposts_count=1,
        pic_num=0,
    )


def comment(cid, **kw):
    result = dict(
        id=cid,
        created_at='Wed Jul 01 10:00:00 +0800 2026',
        user=user(),
        text_raw='公开评论',
        like_counts=0,
        total_number=0,
    )
    result.update(kw)
    return result


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, endpoint, params=None, as_json=True):
        self.calls.append((endpoint, params))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'db.sqlite3')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_query_inventory(self):
        cfg, queries = load_queries()
        self.assertEqual(len(queries), 314)
        self.assertEqual(len({q['query_id'] for q in queries}), 314)
        self.assertIn('互联网医院 精神科', [q['query'] for q in queries])
        self.assertIn('DeepSeek 创伤', [q['query'] for q in queries])
        self.assertEqual(cfg['end_date_exclusive'], '2026-08-01T00:00:00+08:00')

    def test_time_conversion(self):
        self.assertEqual(timestamp('2026-07-31T16:00:00Z'), '2026-08-01T00:00:00+08:00')

    def test_post_fields_and_unknown_favorites(self):
        p = post_record(post())
        self.assertIn('<literal>', p['post_text'])
        self.assertEqual(p['post_id'], '123')
        self.assertTrue(p['is_original'])
        self.assertEqual(p['topics'], ['心理健康'])
        self.assertEqual(p['mentions'], ['账号'])
        self.assertIsNone(p['favorites_count'])

    def test_retweet(self):
        p = post()
        p['retweeted_status'] = dict(mid='9', user=user(8))
        r = post_record(p)
        self.assertEqual(r['reposted_post_id'], '9')
        self.assertEqual(r['reposted_user_id'], '8')
        self.assertFalse(r['is_original'])
        self.assertEqual(r['repost_text'], r['post_text'])

    def test_user_whitelist(self):
        u = user()
        u.update(birthday='private', created_at='old', location='x')
        r = user_record(u)
        self.assertNotIn('birthday', r)
        self.assertNotIn('created_at', r)
        self.assertEqual(r['verified_type'], -1)

    def test_reply_direct_parent_and_root(self):
        r = comment_record(comment(30, reply_comment=dict(id=20, user=user(8))), '123', '10', '7')
        self.assertEqual(r['parent_comment_id'], '20')
        self.assertEqual(r['root_comment_id'], '10')
        self.assertEqual(r['reply_to_user_id'], '8')
        self.assertEqual(r['comment_level'], 2)

    def test_source_location_is_not_device(self):
        r = comment_record(comment(10, source='来自北京'), '123')
        self.assertEqual(r['ip_location'], '来自北京')
        self.assertIsNone(r['comment_source'])
        self.assertIsNone(r['parent_comment_id'])

    def test_missing_nested_reply_user_is_not_root_author(self):
        r = comment_record(comment(30, reply_comment=dict(id=20)), '123', '10', '7')
        self.assertEqual(r['parent_comment_id'], '20')
        self.assertIsNone(r['reply_to_user_id'])

    def test_cross_query_dedup_survives_detail_refresh(self):
        queries = load_queries()[1][:2]
        for q in queries:
            self.store.match('123', q)
        Crawler(FakeClient(post()), self.store).post('123')
        p = self.store.get('posts', '123')
        self.assertEqual(len(p['query_id']), 2)
        self.store.match('123', queries[0])
        self.assertEqual(
            self.store.db.execute('SELECT count(*) FROM post_query_matches').fetchone()[0], 2
        )
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM posts').fetchone()[0], 1)

    def test_long_text_without_continue_tag(self):
        p = post()
        p['isLongText'] = True
        c = FakeClient(p, {'data': {'longTextContent': '全文 #主题#'}})
        r = Crawler(c, self.store).post('123')
        self.assertEqual(r['post_text'], '全文 #主题#')
        self.assertIn('主题', r['topics'])
        self.assertEqual(len(c.calls), 2)

    def test_long_text_failure_preserves_partial(self):
        p = post()
        p['isLongText'] = True
        r = Crawler(FakeClient(p, FetchError('request_budget', 'incomplete')), self.store).post(
            '123'
        )
        self.assertEqual(r['retrieval_status'], 'incomplete')
        self.assertEqual(r['post_text'], p['text_raw'])

    def test_replies_paginate_with_cursor_type(self):
        c = FakeClient(
            {'data': [comment(20)], 'max_id': 99, 'max_id_type': 1},
            {'data': [comment(21)], 'max_id': 0},
        )
        ids, reasons = Crawler(c, self.store).replies(
            '123', dict(comment_id='10', comment_user_id='7', reply_count=2), 2
        )
        self.assertEqual(ids, {'20', '21'})
        self.assertEqual(reasons, [])
        self.assertEqual(c.calls[1][1]['max_id'], 99)
        self.assertEqual(c.calls[1][1]['max_id_type'], 1)

    def test_comments_time_order_and_deduplicate_inline(self):
        c = FakeClient(
            {'data': [comment(10, total_number=1, comments=[comment(20)])], 'max_id': 0},
            {'data': [comment(20)], 'max_id': 0},
        )
        p = post_record(post())
        p['comments_count'] = 2
        audit = Crawler(c, self.store).comments(p)
        self.assertTrue(audit['comments_complete'])
        self.assertEqual(audit['retrieved_comments_count'], 2)
        self.assertEqual(c.calls[0][1]['flow'], 1)

    def test_page_limit_is_not_complete(self):
        c = FakeClient({'data': [comment(10)], 'max_id': 9})
        p = post_record(post())
        p['comments_count'] = 1
        audit = Crawler(c, self.store).comments(p, max_pages=1)
        self.assertFalse(audit['comments_complete'])
        self.assertIn('first_level_page_limit', audit['incomplete_reason'])

    def test_missing_cursor_is_not_complete(self):
        p = post_record(post())
        audit = Crawler(FakeClient({'data': []}), self.store).comments(p)
        self.assertFalse(audit['comments_complete'])
        self.assertIn('missing_comment_cursor', audit['incomplete_reason'])

    def test_repeated_cursor_terminates(self):
        c = FakeClient({'data': [comment(10)], 'max_id': 9}, {'data': [comment(10)], 'max_id': 9})
        audit = Crawler(c, self.store).comments(post_record(post()), max_pages=10)
        self.assertIn('stalled_comment_cursor', audit['incomplete_reason'])
        self.assertEqual(len(c.calls), 2)

    def test_search_icon_next_and_single_quotes(self):
        p = SearchParser()
        p.feed("<div mid='123'></div><a class='next' href='?page=2'><i>a</i></a>")
        self.assertEqual(p.ids, ['123'])
        self.assertTrue(p.has_next)

    def test_empty_search_excludes_recommended_feed(self):
        p = SearchParser()
        p.feed('<div>抱歉，未找到相关结果。</div><div class="card-wrap" mid="123"></div>')
        self.assertTrue(p.no_results)
        self.assertEqual(p.ids, [])

    def test_search_explicit_page_info_count(self):
        for count in (402, 499, 500, 501, 1500):
            p = SearchParser()
            p.feed(f'<span class="page-info">共50页/{count:,}条</span>')
            self.assertEqual(p.total_results, count)
        p = SearchParser()
        p.feed('<div mid="123">共50页/500条</div><a href="?page=50">50</a>')
        self.assertIsNone(p.total_results)

    def test_search_limit_keeps_pending_matches(self):
        q = load_queries()[1][0]
        c = FakeClient('<div mid="123"></div><div mid="124"></div>', post())
        found = Crawler(c, self.store).search(q, '2026-07-01', '2026-08-01', max_posts=1)
        self.assertEqual(len(found), 1)
        self.assertEqual(self.store.get('posts', '124')['retrieval_status'], 'not_fetched')
        self.assertEqual(len(self.store.get('posts', '124')['query_id']), 1)

    def test_credentials_only_sent_to_matching_hosts(self):
        f = Path(self.temp.name) / 'cookie'
        f.write_text('SUB=secret; WBPSESS=hostonly; XSRF-TOKEN=token')
        c = Client(f)
        self.assertEqual(c.cookie_header('s.weibo.com'), 'SUB=secret')
        self.assertIn('WBPSESS', c.cookie_header('weibo.com'))
        with self.assertRaises(FetchError):
            c.get('https://other.example/test')
        self.assertEqual(c.requests, 0)

    def test_exclusive_search_boundary_is_not_saved_as_normal(self):
        q = load_queries()[1][0]
        p = post()
        p['created_at'] = 'Sat Aug 01 00:00:00 +0800 2026'
        c = FakeClient('<div mid="123"></div>', p)
        found = Crawler(c, self.store).search(q, '2026-07-01', '2026-08-01')
        self.assertEqual(found, [])
        saved = self.store.get('posts', '123')
        self.assertEqual(saved['retrieval_status'], 'outside_time_range')
        self.assertIsNone(saved['post_text'])

    def test_budget_failure_keeps_comment_audit(self):
        c = FakeClient(FetchError('request_budget', 'incomplete'))
        audit = Crawler(c, self.store).comments(post_record(post()))
        self.assertFalse(audit['comments_complete'])
        self.assertIn('request_budget', audit['incomplete_reason'])

    def test_unknown_search_page_is_not_empty_success(self):
        q = load_queries()[1][0]
        Crawler(FakeClient('<html>登录</html>'), self.store).search(q, '2026-07-01', '2026-08-01')
        status = self.store.db.execute(
            "SELECT retrieval_status FROM crawl_logs WHERE event='search_window'"
        ).fetchone()[0]
        self.assertEqual(status, 'access_restricted')

    def test_export_preserves_unicode_and_string_ids(self):
        self.store.put('posts', post_record(post(12345678901234567)))
        counts = self.store.export(Path(self.temp.name) / 'export')
        p = json.loads((Path(self.temp.name) / 'export/posts.jsonl').read_text())
        self.assertEqual(p['post_id'], '12345678901234567')
        self.assertEqual(counts['posts'], 1)
        self.assertIsInstance(p['topics'], list)


if __name__ == '__main__':
    unittest.main()
