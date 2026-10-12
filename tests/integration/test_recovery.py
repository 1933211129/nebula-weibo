"""Fault injection uses fake credentials and rolled-back MySQL transactions only."""

import os
import uuid
import unittest
from contextlib import nullcontext, contextmanager
from datetime import timedelta
from unittest.mock import patch


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Opt-in MySQL integration tests')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import select
        from server import db, service, worker
        from server.security import encrypt

        self.db, self.s, self.w = db, service, worker
        self.c = db.engine.connect()
        self.tx = self.c.begin()

        @contextmanager
        def transaction():
            with self.c.begin_nested():
                yield self.c

        self.patches = [
            patch.object(db.engine, 'begin', transaction),
            patch.object(db.engine, 'connect', lambda: nullcontext(self.c)),
        ]
        for p in self.patches:
            p.start()
        self.ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        for cid in self.ids:
            self.c.execute(
                db.cookies.insert().values(
                    id=cid,
                    name='回滚模拟测试',
                    encrypted_value=encrypt('SUB=FAKE_TEST_ONLY'),
                    fingerprint=uuid.uuid4().hex,
                    status='active',
                )
            )
        qid = self.c.scalar(select(db.tables['queries'].c.query_id).limit(1))
        self.tid = service.create_task(
            dict(
                name='回滚可靠性测试',
                start_at='2026-07-01T00:00:00',
                end_at='2026-07-01T02:00:00',
                query_ids=[qid],
                cookie_ids=self.ids,
            )
        )
        service.task_action(self.tid, 'start')
        self.pool = worker.WorkerPool()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tx.rollback()
        self.c.close()

    def job(self, jid):
        from sqlalchemy import select

        return self.c.execute(select(self.db.jobs).where(self.db.jobs.c.id == jid)).mappings().one()

    def ready(self):
        self.c.execute(
            self.db.cookies.update()
            .where(self.db.cookies.c.id.in_(self.ids))
            .values(next_request_at=None)
        )
        self.c.execute(
            self.db.jobs.update()
            .where(self.db.jobs.c.task_id == self.tid, self.db.jobs.c.status == 'pending')
            .values(lease_until=None)
        )

    def test_two_cookies_claim_distinct_hours_single_cookie_cannot_double_claim(self):
        first = self.pool.claim(self.ids[0])
        second = self.pool.claim(self.ids[1])
        self.assertNotEqual(first[0]['payload']['window_id'], second[0]['payload']['window_id'])
        self.assertIsNone(self.pool.claim(self.ids[0]))
        self.assertIsNone(self.pool.claim(self.ids[1]))

    def test_success_keeps_checkpoint_without_verbose_logs(self):
        from sqlalchemy import select, func

        job, cookie = self.pool.claim(self.ids[0])
        self.assertTrue(self.pool.execute(job, cookie, fetcher=lambda *_: '未找到相关结果'))
        self.assertEqual(self.job(job['id'])['status'], 'done')
        self.assertEqual(
            self.c.scalar(
                select(func.count())
                .select_from(self.db.attempts)
                .where(self.db.attempts.c.id == job['lease_owner'])
            ),
            0,
        )
        self.assertEqual(
            self.c.scalar(
                select(func.count())
                .select_from(self.db.logs)
                .where(self.db.logs.c.task_id == self.tid, self.db.logs.c.event == 'request_done')
            ),
            0,
        )

    def test_failed_attempt_is_preserved(self):
        from sqlalchemy import select

        job, cookie = self.pool.claim(self.ids[0])
        self.pool.fail(job, cookie['id'], self.w.RemoteError('network_error'))
        self.assertEqual(
            self.c.scalar(
                select(self.db.attempts.c.outcome).where(
                    self.db.attempts.c.id == job['lease_owner']
                )
            ),
            'network_error',
        )

    def test_diagnostic_mode_retains_success_timing(self):
        from sqlalchemy import select

        self.pool.verbose_logs = True
        job, cookie = self.pool.claim(self.ids[0])
        self.assertTrue(self.pool.execute(job, cookie, fetcher=lambda *_: '未找到相关结果'))
        self.assertEqual(
            self.c.scalar(
                select(self.db.attempts.c.outcome).where(
                    self.db.attempts.c.id == job['lease_owner']
                )
            ),
            'done',
        )
        details = self.c.scalar(
            select(self.db.logs.c.details).where(
                self.db.logs.c.task_id == self.tid, self.db.logs.c.event == 'request_done'
            )
        )
        self.assertIn('network_started_at', details)

    def test_task_scoped_worker_cannot_claim_another_tasks_checkpoint(self):
        from sqlalchemy import select

        qid = self.c.scalar(select(self.db.tables['queries'].c.query_id).limit(1))
        other = self.s.create_task(
            dict(
                name='独立作用域测试',
                start_at='2026-07-01T00:00:00',
                end_at='2026-07-01T01:00:00',
                query_ids=[qid],
                cookie_ids=self.ids,
            )
        )
        self.s.task_action(other, 'start')
        isolated = self.w.WorkerPool(task_ids=[other])
        job, _ = isolated.claim(self.ids[0])
        self.assertEqual(job['task_id'], other)

    def test_expired_cookie_preserves_checkpoint_and_explicit_retry(self):
        from sqlalchemy import select

        self.c.execute(
            self.db.cookies.update()
            .where(self.db.cookies.c.id == self.ids[1])
            .values(status='disabled')
        )
        job, cookie = self.pool.claim(self.ids[0])
        self.pool.fail(job, cookie['id'], self.w.RemoteError('login_expired', 200))
        interrupted = self.job(job['id'])
        self.assertEqual(interrupted['status'], 'blocked')
        self.assertEqual(interrupted['payload'], job['payload'])
        self.assertEqual(
            self.c.scalar(select(self.db.tasks.c.status).where(self.db.tasks.c.id == self.tid)),
            'needs_attention',
        )
        self.assertIsNone(self.pool.claim(self.ids[1]))
        self.c.execute(
            self.db.cookies.update()
            .where(self.db.cookies.c.id == cookie['id'])
            .values(status='active')
        )
        self.s.task_action(self.tid, 'retry')
        self.ready()
        restored = self.job(job['id'])
        self.assertEqual(restored['status'], 'pending')
        self.assertEqual(restored['payload'], job['payload'])
        claimed = self.pool.claim(self.ids[0])
        self.assertTrue(self.pool.execute(*claimed, fetcher=lambda *_: '未找到相关结果'))

    def test_expired_cookie_does_not_stop_other_channel(self):
        from sqlalchemy import select

        job, cookie = self.pool.claim(self.ids[0])
        self.pool.fail(job, cookie['id'], self.w.RemoteError('login_expired', 200))
        self.assertEqual(self.job(job['id'])['status'], 'pending')
        self.assertEqual(self.job(job['id'])['payload'], job['payload'])
        self.assertEqual(
            self.c.scalar(select(self.db.tasks.c.status).where(self.db.tasks.c.id == self.tid)),
            'running',
        )
        self.assertIsNone(self.pool.claim(self.ids[0]))
        self.ready()
        self.assertIsNotNone(self.pool.claim(self.ids[1]))

    def test_cookie_failure_threshold_and_success_reset(self):
        from sqlalchemy import select

        cid = self.ids[0]
        self.c.execute(
            self.db.cookies.update().where(self.db.cookies.c.id == cid).values(failure_limit=2)
        )
        job, cookie = self.pool.claim(cid)
        self.pool.fail(job, cid, self.w.RemoteError('network_error'))
        self.assertEqual(
            self.c.scalar(
                select(self.db.cookies.c.consecutive_failures).where(self.db.cookies.c.id == cid)
            ),
            1,
        )
        self.ready()
        self.assertTrue(
            self.pool.execute(*self.pool.claim(cid), fetcher=lambda *_: '未找到相关结果')
        )
        self.assertEqual(
            self.c.scalar(
                select(self.db.cookies.c.consecutive_failures).where(self.db.cookies.c.id == cid)
            ),
            0,
        )
        for _ in range(2):
            self.ready()
            job, cookie = self.pool.claim(cid)
            self.pool.fail(job, cid, self.w.RemoteError('network_error'))
        self.assertEqual(
            self.c.scalar(select(self.db.cookies.c.status).where(self.db.cookies.c.id == cid)),
            'error_paused',
        )
        self.assertEqual(self.job(job['id'])['status'], 'pending')
        self.assertEqual(self.job(job['id'])['attempts'], 0)

    def test_processing_error_does_not_disable_cookie(self):
        from sqlalchemy import select

        cid = self.ids[0]
        self.c.execute(
            self.db.cookies.update().where(self.db.cookies.c.id == cid).values(failure_limit=1)
        )
        job, cookie = self.pool.claim(cid)
        self.pool.fail(job, cid, ValueError('bad schema'))
        self.assertEqual(
            self.c.scalar(select(self.db.cookies.c.status).where(self.db.cookies.c.id == cid)),
            'active',
        )
        self.assertEqual(
            self.c.scalar(
                select(self.db.cookies.c.consecutive_failures).where(self.db.cookies.c.id == cid)
            ),
            0,
        )

    def test_restart_reclaims_lease_and_rejects_late_worker(self):
        job, cookie = self.pool.claim(self.ids[0])
        self.c.execute(
            self.db.jobs.update()
            .where(self.db.jobs.c.id == job['id'])
            .values(lease_until=self.db.utcnow() - timedelta(seconds=1))
        )
        restarted = self.w.WorkerPool()
        restarted.recover_expired()
        self.ready()
        replacement = restarted.claim(self.ids[0])
        self.assertNotEqual(replacement[0]['lease_owner'], job['lease_owner'])
        self.assertFalse(self.pool.execute(job, cookie, fetcher=lambda *_: '未找到相关结果'))
        self.pool.fail(job, cookie['id'], self.w.RemoteError('login_expired', 200))
        self.assertEqual(self.job(replacement[0]['id'])['status'], 'running')
        self.assertTrue(restarted.execute(*replacement, fetcher=lambda *_: '未找到相关结果'))

    def test_transient_failure_backoff_and_three_attempt_limit(self):
        job, cookie = self.pool.claim(self.ids[0])
        for attempt in range(1, 4):
            job = dict(job, attempts=attempt)
            self.pool.fail(job, cookie['id'], self.w.RemoteError('network_error'))
            row = self.job(job['id'])
            self.assertEqual(row['status'], 'failed' if attempt == 3 else 'pending')
            if attempt < 3:
                self.assertGreater(row['lease_until'], self.db.utcnow())
                self.ready()
                # Directly reserve the same failed checkpoint for the next simulated attempt.
                token = str(uuid.uuid4())
                self.c.execute(
                    self.db.jobs.update()
                    .where(self.db.jobs.c.id == job['id'])
                    .values(
                        status='running',
                        lease_owner=token,
                        lease_until=self.db.utcnow() + timedelta(seconds=120),
                    )
                )
                job = dict(job, lease_owner=token)

    def test_failed_processing_does_not_ack(self):
        job, cookie = self.pool.claim(self.ids[0])
        self.assertFalse(self.pool.execute(job, cookie, fetcher=lambda *_: 'unexpected markup'))
        self.assertEqual(self.job(job['id'])['status'], 'pending')
        self.assertIsNone(self.job(job['id'])['finished_at'])

    def test_database_failure_rolls_back_records_and_checkpoint_together(self):
        from sqlalchemy import select, func

        job, cookie = self.pool.claim(self.ids[0])

        def partial_write(conn, *_):
            conn.execute(
                self.db.tables['posts']
                .insert()
                .values(post_id='999999999999999992', retrieval_status='normal')
            )
            raise RuntimeError('simulated database commit failure')

        with patch('server.collector.worker.process_result', side_effect=partial_write):
            self.assertFalse(self.pool.execute(job, cookie, fetcher=lambda *_: '未找到相关结果'))
        self.assertEqual(
            self.c.scalar(
                select(func.count())
                .select_from(self.db.tables['posts'])
                .where(self.db.tables['posts'].c.post_id == '999999999999999992')
            ),
            0,
        )
        self.assertEqual(self.job(job['id'])['status'], 'pending')

    def test_comment_cursor_survives_expiry(self):
        self.s.enqueue(
            self.c,
            self.tid,
            'comments',
            'comments:fake:987',
            dict(post_id='fake', user_id='fake', cursor='987', cursor_type=0),
            100,
        )
        job, cookie = self.pool.claim(self.ids[0])
        self.assertEqual(job['kind'], 'comments')
        self.pool.fail(job, cookie['id'], self.w.RemoteError('login_expired', 200))
        self.s.task_action(self.tid, 'retry')
        self.assertEqual(self.job(job['id'])['payload']['cursor'], '987')
        self.assertEqual(self.job(job['id'])['payload']['cursor_type'], 0)

    def test_comment_primary_key_deduplicates_replay(self):
        from sqlalchemy import select, func
        from server.collector.records import save_comment

        raw = dict(
            id='999999999999999993',
            created_at='Wed Jul 01 10:00:00 +0800 2026',
            text_raw='synthetic test',
            user={'id': '999999999999999993'},
        )
        save_comment(self.c, self.tid, raw, 'fake')
        save_comment(self.c, self.tid, raw, 'fake')
        self.assertEqual(
            self.c.scalar(
                select(func.count())
                .select_from(self.db.task_comments)
                .where(self.db.task_comments.c.task_id == self.tid)
            ),
            1,
        )

    def test_duplicate_search_hit_keeps_one_post_job_and_all_query_matches(self):
        from sqlalchemy import select, func

        qids = self.c.execute(select(self.db.tables['queries'].c.query_id).limit(2)).scalars().all()
        pid = '999999999999999991'
        for qid in qids:
            wid = self.s.add_window(
                self.c, self.tid, qid, self.s.datetime(2026, 7, 1), self.s.datetime(2026, 7, 1, 1)
            )
            job = dict(
                task_id=self.tid,
                kind='search',
                cookie_id=self.ids[0],
                payload=dict(window_id=wid, page=1),
            )
            self.w.process_result(self.c, job, f'<div mid="{pid}"></div>')
        n = self.c.scalar(
            select(func.count())
            .select_from(self.db.jobs)
            .where(self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'post')
        )
        self.assertEqual(n, 1)
        record = (
            self.c.execute(
                select(self.db.tables['posts']).where(self.db.tables['posts'].c.post_id == pid)
            )
            .mappings()
            .one()
        )
        found = (
            self.c.execute(
                select(self.db.keyword_checks.c.query_id).where(
                    self.db.keyword_checks.c.task_id == self.tid,
                    self.db.keyword_checks.c.post_id == pid,
                )
            )
            .scalars()
            .all()
        )
        self.assertEqual(set(found), set(qids))
        self.assertTrue(set(record['query_id'] or []) <= set(qids))


class ResponseTests(unittest.TestCase):
    def test_http_200_expired_cookie_and_http_302(self):
        import httpx
        from server.worker import fetch, RemoteError

        for response in (httpx.Response(200, json={'ok': -100}), httpx.Response(302)):
            with patch('server.collector.transport.pooled_get', return_value=response):
                with self.assertRaises(RemoteError) as caught:
                    fetch('SUB=FAKE_TEST_ONLY', 'https://weibo.com/ajax/statuses/show', {'id': '1'})
                self.assertEqual(caught.exception.code, 'login_expired')
                self.assertEqual(caught.exception.status, response.status_code)
