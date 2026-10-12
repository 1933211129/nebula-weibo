"""All writes are transactionally rolled back; no live credentials or requests."""

import os
import unittest
from datetime import timedelta
from sqlalchemy import select, func, event
from tests.integration import test_recovery as fixtures


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Opt-in MySQL integration tests')
class TargetedRepairTests(unittest.TestCase):
    setUp = fixtures.RecoveryTests.setUp
    tearDown = fixtures.RecoveryTests.tearDown
    job = fixtures.RecoveryTests.job
    ready = fixtures.RecoveryTests.ready

    def seed_post(self, count=0):
        pid = '99999999999999501'
        self.db.upsert(
            self.c,
            self.db.tables['posts'],
            dict(
                post_id=pid,
                user_id='99999999501',
                created_at='2026-07-01T00:00:00+08:00',
                comments_count=count,
                post_text='未确认正文',
                retrieval_status='incomplete',
            ),
        )
        self.db.upsert(self.c, self.db.task_posts, dict(task_id=self.tid, post_id=pid))
        self.db.upsert(
            self.c,
            self.db.audits,
            dict(task_id=self.tid, post_id=pid, displayed_comments_count=count),
        )
        return dict(
            task_id=self.tid,
            kind='comments',
            priority=20,
            payload=dict(post_id=pid, user_id='99999999501', cursor=0),
        )

    def test_explicit_zero_comment_page_completes_audit(self):
        job = self.seed_post()
        self.w.process_result(
            self.c, job, dict(ok=1, data=[], total_number=0, trendsText='已加载全部评论')
        )
        self.assertTrue(
            self.c.scalar(
                select(self.db.audits.c.comments_complete).where(
                    self.db.audits.c.task_id == self.tid
                )
            )
        )

    def test_visible_end_retains_display_count_mismatch(self):
        job = self.seed_post(12)
        self.w.process_result(
            self.c, job, dict(ok=1, data=[], max_id=123, trendsText='已加载全部评论')
        )
        audit = (
            self.c.execute(select(self.db.audits).where(self.db.audits.c.task_id == self.tid))
            .mappings()
            .one()
        )
        self.assertTrue(audit['first_level_exhausted'])
        self.assertFalse(audit['comments_complete'])
        self.assertEqual(audit['incomplete_reason'], ['displayed_count_mismatch'])

    def test_longtext_empty_queues_one_check_and_comments_without_lying(self):
        job = self.seed_post()
        job['kind'] = 'longtext'
        for _ in range(2):
            self.w.process_result(self.c, job, dict(ok=1, data={}))
        kinds = dict(
            self.c.execute(
                select(self.db.jobs.c.kind, func.count())
                .where(self.db.jobs.c.task_id == self.tid)
                .group_by(self.db.jobs.c.kind)
            ).all()
        )
        self.assertEqual(kinds['longtext_check'], 1)
        self.assertEqual(kinds['comments'], 1)
        status = self.c.scalar(
            select(self.db.tables['posts'].c.retrieval_status).where(
                self.db.tables['posts'].c.post_id == job['payload']['post_id']
            )
        )
        self.assertEqual(status, 'incomplete')
        job['kind'] = 'longtext_check'
        with self.assertRaisesRegex(self.w.RemoteError, 'longtext_unconfirmed'):
            self.w.process_result(self.c, job, dict(id=1, isLongText=True, text_raw='短正文'))
        with self.assertRaisesRegex(self.w.RemoteError, 'longtext_unconfirmed'):
            self.w.process_result(self.c, job, dict(id=1, isLongText=False, text_raw='其他帖子'))
        self.w.process_result(
            self.c,
            job,
            dict(id=job['payload']['post_id'], isLongText=False, text_raw='明确完整正文'),
        )
        self.assertEqual(
            self.c.scalar(
                select(self.db.tables['posts'].c.post_text).where(
                    self.db.tables['posts'].c.post_id == job['payload']['post_id']
                )
            ),
            '明确完整正文',
        )

    def test_resume_does_not_retry_content_failures(self):
        rows = (
            self.c.execute(select(self.db.jobs).where(self.db.jobs.c.task_id == self.tid))
            .mappings()
            .all()
        )
        for row, status in zip(rows, ['failed', 'blocked']):
            self.c.execute(
                self.db.jobs.update()
                .where(self.db.jobs.c.id == row['id'])
                .values(status=status, last_error='test')
            )
        self.s.task_action(self.tid, 'pause')
        self.s.task_action(self.tid, 'resume')
        self.assertEqual(self.job(rows[0]['id'])['status'], 'failed')
        self.assertEqual(self.job(rows[1]['id'])['status'], 'pending')
        self.s.task_action(self.tid, 'pause')
        self.s.task_action(self.tid, 'retry')
        self.assertEqual(self.job(rows[0]['id'])['status'], 'pending')
        self.assertEqual(self.job(rows[0]['id'])['payload'], rows[0]['payload'])

    def test_batch_upsert_preserves_non_null_and_uses_one_insert(self):
        queries = []

        def record(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        event.listen(self.c, 'before_cursor_execute', record)
        try:
            self.db.upsert_many(
                self.c,
                self.db.tables['users'],
                [dict(user_id=str(99999999000 + i), screen_name='测试') for i in range(20)],
            )
            self.assertEqual(len(queries), 1)
            self.db.upsert_many(
                self.c,
                self.db.tables['users'],
                [dict(user_id='99999999000', screen_name=None, followers_count=2)],
            )
            self.assertEqual(
                self.c.scalar(
                    select(self.db.tables['users'].c.screen_name).where(
                        self.db.tables['users'].c.user_id == '99999999000'
                    )
                ),
                '测试',
            )
        finally:
            event.remove(self.c, 'before_cursor_execute', record)

    def test_day_gap_splits_only_its_date_and_is_idempotent(self):
        from server.collector.repair import repair_daily_gaps

        task = dict(
            self.c.execute(select(self.db.tasks).where(self.db.tasks.c.id == self.tid))
            .mappings()
            .one()
        )
        start = task['start_at'] + timedelta(days=1)
        wid = self.s.add_window(
            self.c, self.tid, task['query_ids'][0], start, start + timedelta(days=1), adaptive=True
        )
        self.c.execute(
            self.db.windows.update()
            .where(self.db.windows.c.id == wid)
            .values(status='limited', current_page=50)
        )
        self.assertEqual(repair_daily_gaps(self.c, task), 24)
        self.assertEqual(repair_daily_gaps(self.c, task), 0)
        children = (
            self.c.execute(select(self.db.windows).where(self.db.windows.c.parent_id == wid))
            .mappings()
            .all()
        )
        self.assertEqual(len(children), 24)
        self.assertTrue(all(start <= w['start_at'] < start + timedelta(days=1) for w in children))

    def test_page_50_known_499_natural_end_is_done(self):
        job = dict(
            self.c.execute(select(self.db.jobs).where(self.db.jobs.c.task_id == self.tid))
            .mappings()
            .first()
        )
        job['payload'] = dict(job['payload'], page=50)
        self.w.process_result(
            self.c,
            job,
            '<span class="page-info">共50页/499条</span><div mid="99999999999999502"></div>',
        )
        self.assertEqual(
            self.c.scalar(
                select(self.db.windows.c.status).where(
                    self.db.windows.c.id == job['payload']['window_id']
                )
            ),
            'done',
        )

    def test_content_failure_only_backs_off_its_job(self):
        job, cookie = self.pool.claim(self.ids[0])
        before = self.c.scalar(
            select(self.db.cookies.c.next_request_at).where(self.db.cookies.c.id == self.ids[0])
        )
        self.pool.fail(job, self.ids[0], self.w.RemoteError('missing_comment_cursor'))
        after = self.c.scalar(
            select(self.db.cookies.c.next_request_at).where(self.db.cookies.c.id == self.ids[0])
        )
        self.assertEqual(before, after)
        self.assertEqual(self.job(job['id'])['status'], 'pending')
        self.assertGreater(self.job(job['id'])['lease_until'], self.db.utcnow())

    def test_aged_search_gets_fairness_slot(self):
        self.c.execute(
            self.db.jobs.update()
            .where(self.db.jobs.c.task_id == self.tid)
            .values(created_at=self.db.utcnow() - timedelta(seconds=60))
        )
        self.s.enqueue(self.c, self.tid, 'post', 'fairness:post', {'post_id': '1'}, 100)
        job, cookie = self.pool.claim(self.ids[0])
        self.assertEqual(job['kind'], 'search')
