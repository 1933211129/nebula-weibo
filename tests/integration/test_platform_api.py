"""Platform API contract tests. MySQL writes are isolated in a rolled-back transaction."""

import os
import unittest
from contextlib import nullcontext
from unittest.mock import patch


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Opt-in MySQL integration tests')
class PlatformAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import select
        from server import app, db

        self.a, self.db = app, db
        self.conn = db.engine.connect()
        self.tx = self.conn.begin()
        self.patches = [
            patch.object(db.engine, method, lambda: nullcontext(self.conn))
            for method in ('begin', 'connect')
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app.app)
        self.qid = self.conn.scalar(select(db.tables['queries'].c.query_id).limit(1))

    def tearDown(self):
        self.client.close()
        for item in self.patches:
            item.stop()
        self.tx.rollback()
        self.conn.close()

    def create(self, **overrides):
        payload = dict(
            name='界面契约测试（回滚）',
            start_at='2026-07-31T18:00',
            end_at='2026-07-31T20:00',
            query_ids=[self.qid],
            cookie_ids=[],
            search_mode='hourly',
        )
        payload.update(overrides)
        return self.client.post('/api/tasks', json=payload)

    def test_platform_is_accessible_without_login(self):
        self.assertEqual(self.client.get('/api/overview').status_code, 200)
        self.assertEqual(self.create().status_code, 200)

    def test_product_tasks_have_no_total_request_limit(self):
        for payload in ({}, {'request_limit': 200}):
            response = self.create(**payload)
            self.assertEqual(response.status_code, 200)
            detail = self.client.get('/api/tasks/' + response.json()['id']).json()
            self.assertIsNone(detail['request_limit'])

    def test_adaptive_api_and_random_interval_validation(self):
        response = self.create(search_mode='adaptive')
        self.assertEqual(response.status_code, 200)
        tid = response.json()['id']
        self.assertEqual(self.client.get('/api/tasks/' + tid).json()['search_mode'], 'adaptive')
        self.assertEqual(self.client.get(f'/api/tasks/{tid}/checkpoints').json()['total'], 1)
        bad = self.client.post(
            '/api/cookies',
            json=dict(
                name='invalid', value='SUB=FAKE', request_interval_min=2.5, request_interval_max=1
            ),
        )
        self.assertEqual(bad.status_code, 422)

    def test_new_tasks_accumulate_posts_comments_and_query_links(self):
        from sqlalchemy import select, func
        from server.collector.records import process_result, save_comment

        pids = ['99999999999999201', '99999999999999202']
        tids = []
        for word, end, selected in [
            ('累积甲', '2026-08-01T00:00', pids[:1]),
            ('累积乙', '2026-08-02T00:00', pids),
        ]:
            response = self.create(
                query_ids=[], keywords_text=word, end_at=end, include_comments=False
            )
            self.assertEqual(response.status_code, 200)
            tid = response.json()['id']
            tids.append(tid)
            if len(tids) == 2:
                self.assertIsNotNone(
                    self.conn.execute(
                        select(self.db.tables['posts']).where(
                            self.db.tables['posts'].c.post_id == pids[0]
                        )
                    ).first()
                )
            wid = self.conn.scalar(
                select(self.db.windows.c.id).where(self.db.windows.c.task_id == tid).limit(1)
            )
            process_result(
                self.conn,
                dict(
                    task_id=tid, kind='search', cookie_id=None, payload=dict(window_id=wid, page=1)
                ),
                ''.join(f'<div mid="{pid}"></div>' for pid in selected),
            )
            for pid in selected:
                process_result(
                    self.conn,
                    dict(task_id=tid, kind='post', payload={'post_id': pid}),
                    dict(
                        id=pid,
                        created_at='Fri Jul 31 18:30:00 +0800 2026',
                        user={'id': '99999999'},
                        text_raw='累积甲 累积乙',
                        comments_count=1,
                    ),
                )
            save_comment(
                self.conn,
                tid,
                dict(
                    id='99999999999999301',
                    created_at='Fri Jul 31 18:35:00 +0800 2026',
                    user={'id': '99999998'},
                    text_raw='共同评论',
                ),
                pids[0],
            )
        for table, field, ids, count in [
            (self.db.tables['posts'], 'post_id', pids, 2),
            (self.db.tables['comments'], 'comment_id', ['99999999999999301'], 1),
            (self.db.task_posts, 'task_id', tids, 3),
            (self.db.task_comments, 'task_id', tids, 2),
            (self.db.tables['post_query_matches'], 'post_id', pids, 3),
        ]:
            self.assertEqual(
                self.conn.scalar(
                    select(func.count()).select_from(table).where(table.c[field].in_(ids))
                ),
                count,
            )
        # A later, non-overlapping time range starts empty without deleting prior data.
        self.assertEqual(
            self.create(
                query_ids=[],
                keywords_text='累积丙',
                start_at='2026-08-02T00:00',
                end_at='2026-08-03T00:00',
            ).status_code,
            200,
        )
        self.assertEqual(
            self.conn.scalar(
                select(func.count())
                .select_from(self.db.tables['posts'])
                .where(self.db.tables['posts'].c.post_id.in_(pids))
            ),
            2,
        )
        self.assertFalse(self.client.cookies)

    def test_login_endpoints_removed(self):
        self.assertEqual(self.client.get('/api/session').status_code, 404)
        for path in ('/api/login', '/api/logout'):
            self.assertIn(self.client.post(path, json={}).status_code, (404, 405))

    def test_legacy_session_is_ignored(self):
        self.client.cookies.set('weibo_session', 'invalid-old-session')
        self.assertEqual(self.client.get('/api/cookies').status_code, 200)

    def test_cross_site_mutation_is_rejected_without_auth(self):
        response = self.client.post('/api/tasks', json={}, headers={'origin': 'https://other.test'})
        self.assertEqual(response.status_code, 403)

    def test_hour_creation_is_draft_and_exact(self):
        response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        tid = response.json()['id']
        detail = self.client.get('/api/tasks/' + tid).json()
        self.assertEqual(detail['status'], 'draft')
        self.assertEqual(detail['metrics']['total_query_hours'], 2)
        self.assertEqual(detail['request_count'], 0)
        rows = self.client.get(f'/api/tasks/{tid}/checkpoints').json()
        self.assertEqual(rows['total'], 2)
        self.assertEqual(rows['rows'][0]['start_at'], '2026-07-31T18:00:00')

    def test_custom_keywords_and_unrestricted_dates(self):
        from sqlalchemy import select

        response = self.create(
            query_ids=[],
            keywords_text='· 新词一、 新词二；新词一',
            start_at='2018-12-31T23:00',
            end_at='2019-01-01T02:00',
        )
        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get('/api/tasks/' + response.json()['id']).json()
        self.assertEqual(len(detail['query_ids']), 2)
        self.assertEqual(detail['metrics']['total_query_hours'], 6)
        self.assertEqual(
            [(c['month'], c['total']) for c in detail['coverage']],
            [('2018-12', 7200), ('2019-01', 14400)],
        )
        self.assertEqual(detail['status'], 'draft')
        saved = (
            self.conn.execute(
                select(self.db.tables['queries'].c.query).where(
                    self.db.tables['queries'].c.query_id.in_(detail['query_ids'])
                )
            )
            .scalars()
            .all()
        )
        self.assertEqual(set(saved), {'新词一', '新词二'})
        from server.collector.records import endpoint_for

        job = (
            self.conn.execute(select(self.db.jobs).where(self.db.jobs.c.task_id == detail['id']))
            .mappings()
            .first()
        )
        endpoint, params = endpoint_for(dict(job), self.conn)
        self.assertEqual(endpoint, 'https://s.weibo.com/weibo')
        self.assertIn(params['q'], saved)
        self.assertTrue(
            params['timescope'].startswith('custom:2018-12-31-23:')
            or params['timescope'].startswith('custom:2019-01-01-')
        )

    def test_empty_keywords_are_rejected(self):
        self.assertEqual(self.create(query_ids=[], keywords_text='· 、 ,').status_code, 400)

    def test_mixed_cross_product_and_full_body_verification(self):
        from sqlalchemy import select, func
        from server.collector.records import process_result

        response = self.create(
            query_ids=[],
            keywords_text='普通词',
            combination_left='· 互联网医院、在线问诊',
            combination_right='精神科；心理医生',
        )
        self.assertEqual(response.status_code, 200, response.text)
        tid = response.json()['id']
        self.assertEqual(len(self.client.get('/api/tasks/' + tid).json()['query_ids']), 5)
        q = self.db.tables['queries']
        qid = self.conn.scalar(
            select(q.c.query_id).where(q.c.query == '互联网医院 精神科').limit(1)
        )
        wid = self.conn.scalar(
            select(self.db.windows.c.id)
            .where(self.db.windows.c.task_id == tid, self.db.windows.c.query_id == qid)
            .limit(1)
        )
        ids = ['99999999999999101', '99999999999999102', '99999999999999103']
        search = dict(
            task_id=tid, kind='search', cookie_id=None, payload=dict(window_id=wid, page=1)
        )
        process_result(self.conn, search, ''.join(f'<div mid="{p}"></div>' for p in ids))
        for pid, text, long in zip(
            ids, ['互联网医院的精神科', '互联网医院健康服务', '互联网医院'], [False, False, True]
        ):
            process_result(
                self.conn,
                dict(task_id=tid, kind='post', payload={'post_id': pid}),
                dict(
                    id=pid,
                    created_at='Fri Jul 31 18:30:00 +0800 2026',
                    user={'id': '99999999'},
                    text_raw=text,
                    isLongText=long,
                    comments_count=0,
                ),
            )
        statuses = dict(
            self.conn.execute(
                select(self.db.keyword_checks.c.post_id, self.db.keyword_checks.c.status).where(
                    self.db.keyword_checks.c.task_id == tid
                )
            ).all()
        )
        self.assertEqual([statuses[p] for p in ids], ['verified', 'rejected', 'pending'])
        process_result(
            self.conn,
            dict(task_id=tid, kind='longtext', payload={'post_id': ids[2]}),
            {'data': {'longTextContent': '互联网医院提供精神科服务'}},
        )
        associations = (
            self.conn.execute(
                select(self.db.task_posts.c.post_id).where(self.db.task_posts.c.task_id == tid)
            )
            .scalars()
            .all()
        )
        self.assertEqual(set(associations), {ids[0], ids[2]})
        self.assertEqual(
            self.conn.scalar(
                select(func.count())
                .select_from(self.db.jobs)
                .where(self.db.jobs.c.task_id == tid, self.db.jobs.c.kind == 'comments')
            ),
            2,
        )
        detail = self.client.get('/api/tasks/' + tid).json()
        self.assertEqual(detail['keyword_verification']['verified'], 2)
        rejected = next(r for r in detail['keyword_checks'] if r['status'] == 'rejected')
        self.assertEqual(rejected['missing_terms'], ['精神科'])
        # A late discovery after the detail job has already completed is checked from cached body.
        second = self.conn.scalar(
            select(q.c.query_id).where(q.c.query == '在线问诊 精神科').limit(1)
        )
        wid2 = self.conn.scalar(
            select(self.db.windows.c.id)
            .where(self.db.windows.c.task_id == tid, self.db.windows.c.query_id == second)
            .limit(1)
        )
        process_result(
            self.conn,
            dict(search, payload=dict(window_id=wid2, page=1)),
            f'<div mid="{ids[0]}"></div>',
        )
        self.assertEqual(
            self.conn.scalar(
                select(self.db.keyword_checks.c.status).where(
                    self.db.keyword_checks.c.task_id == tid,
                    self.db.keyword_checks.c.post_id == ids[0],
                    self.db.keyword_checks.c.query_id == second,
                )
            ),
            'rejected',
        )
        self.assertEqual(
            self.conn.scalar(
                select(func.count())
                .select_from(self.db.task_posts)
                .where(self.db.task_posts.c.task_id == tid)
            ),
            2,
        )

    def test_incomplete_cross_input_is_rejected(self):
        self.assertEqual(self.create(query_ids=[], combination_left='医院').status_code, 400)

    def test_rejects_partial_hours_and_missing_times(self):
        self.assertEqual(self.create(start_at='2026-07-31T18:30').status_code, 400)
        self.assertEqual(self.create(end_at='2026-07-31T17:00').status_code, 400)
        self.assertEqual(self.client.post('/api/tasks', json={'name': 'test'}).status_code, 422)

    def test_checkpoints_are_bounded_and_scoped(self):
        tid = self.create(start_at='2026-07-01T00:00', end_at='2026-08-01T00:00').json()['id']
        other = self.create().json()['id']
        rows = self.client.get(f'/api/tasks/{tid}/checkpoints?kind=jobs').json()
        self.assertEqual(rows['total'], 24)
        self.assertTrue(all(r['task_id'] == tid for r in rows['rows']))
        self.assertNotEqual(tid, other)
        self.assertEqual(self.client.get(f'/api/tasks/{tid}/checkpoints?page=0').status_code, 422)
        self.assertEqual(self.client.get('/api/tasks/missing/checkpoints').status_code, 404)
        self.assertEqual(self.client.get(f'/api/tasks/{tid}/checkpoints?kind=bad').status_code, 400)

    def test_cookie_response_never_discloses_credentials(self):
        rows = self.client.get('/api/cookies').json()
        for row in rows:
            self.assertNotIn('encrypted_value', row)
            self.assertNotIn('fingerprint', row)
            self.assertNotIn('value', row)

    def test_no_cookie_task_cannot_start(self):
        tid = self.create().json()['id']
        self.assertEqual(self.client.post(f'/api/tasks/{tid}/start').status_code, 400)
        self.assertEqual(self.client.get(f'/api/tasks/{tid}').json()['status'], 'draft')
