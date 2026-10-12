"""Transactional coverage of probe/split, preserved upgrade and compact receipts."""

import os
import unittest
from datetime import timedelta
from sqlalchemy import select, func
from tests.integration import test_hourly_queue as fixtures


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Opt-in MySQL integration tests')
class OptimizationTests(unittest.TestCase):
    setUp = fixtures.HourlyQueueTests.setUp
    tearDown = fixtures.HourlyQueueTests.tearDown
    row = fixtures.HourlyQueueTests.row
    task = fixtures.HourlyQueueTests.task

    def adaptive(self):
        self.tid = self.s.create_task(
            dict(
                name='回滚自适应测试',
                start_at='2026-07-01T00:00',
                end_at='2026-07-03T00:00',
                query_ids=[self.qid],
                cookie_ids=[],
                search_mode='adaptive',
            )
        )

    def test_day_probe_50_pages_splits_before_post_collection(self):
        self.adaptive()
        job = self.row(self.db.jobs)
        result = '<div mid="99999999999999701"></div><ul class="page-list"><li><a href="?page=50">50</a></li></ul><a class="next" href="?page=2">下一页</a>'
        self.w.process_result(self.conn, job, result)
        self.w.process_result(self.conn, job, result)
        counts = dict(
            self.conn.execute(
                select(self.db.windows.c.status, func.count())
                .where(self.db.windows.c.task_id == self.tid)
                .group_by(self.db.windows.c.status)
            ).all()
        )
        self.assertEqual(counts, {'split': 1, 'pending': 25})
        self.assertIsNone(
            self.conn.execute(
                select(self.db.tables['posts'].c.post_id).where(
                    self.db.tables['posts'].c.post_id == '99999999999999701'
                )
            ).first()
        )
        children = (
            self.conn.execute(
                select(self.db.windows)
                .where(self.db.windows.c.parent_id == job['payload']['window_id'])
                .order_by(self.db.windows.c.start_at)
            )
            .mappings()
            .all()
        )
        self.assertEqual(len(children), 24)
        self.assertTrue(all(w['end_at'] - w['start_at'] == timedelta(hours=1) for w in children))
        self.assertTrue(all(a['end_at'] == b['start_at'] for a, b in zip(children, children[1:])))
        self.assertEqual(self.s.task_metrics(self.conn, self.task())['unqueued_query_hours'], 0)

    def test_day_probe_500_results_splits_without_pagination(self):
        self.adaptive()
        job = self.row(self.db.jobs)
        self.w.process_result(
            self.conn,
            job,
            '<span class="page-info">共50页/500条</span><div mid="99999999999999702"></div>',
        )
        status = self.conn.scalar(
            select(self.db.windows.c.status).where(
                self.db.windows.c.id == job['payload']['window_id']
            )
        )
        self.assertEqual(status, 'split')

    def test_sparse_day_uses_day_pages_and_last_page_finishes(self):
        self.adaptive()
        job = self.row(self.db.jobs)
        self.w.process_result(
            self.conn,
            job,
            '<div mid="99999999999999703"></div><ul class="page-list"><li><a href="?page=2">2</a></li></ul><a class="next" href="?page=2">下一页</a>',
        )
        second = next(
            r
            for r in self.conn.execute(
                select(self.db.jobs).where(
                    self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'search'
                )
            ).mappings()
            if r['payload']['page'] == 2
        )
        self.assertEqual(
            self.w.endpoint_for(second, self.conn)[1]['timescope'],
            self.w.endpoint_for(job, self.conn)[1]['timescope'],
        )
        self.w.process_result(self.conn, second, '<div mid="99999999999999704"></div>')
        metrics = self.s.task_metrics(self.conn, self.task())
        self.assertEqual(metrics['covered_query_hours'], 24)
        self.assertEqual(metrics['date_progress'], 50)

    def test_unknown_total_splits_and_empty_day_does_not(self):
        self.adaptive()
        job = self.row(self.db.jobs)
        self.w.process_result(
            self.conn,
            job,
            '<div mid="99999999999999705"></div><a class="next" href="?page=2">下一页</a>',
        )
        self.assertEqual(
            self.conn.scalar(
                select(self.db.windows.c.status).where(
                    self.db.windows.c.id == job['payload']['window_id']
                )
            ),
            'split',
        )
        self.adaptive()
        self.w.process_result(self.conn, self.row(self.db.jobs), '未找到相关结果')
        self.assertEqual(self.s.task_metrics(self.conn, self.task())['covered_query_hours'], 24)

    def test_count_below_500_stays_daily_and_hour_cap_records_gap(self):
        self.adaptive()
        first = self.row(self.db.jobs)
        self.w.process_result(
            self.conn,
            first,
            '<span class="page-info">共50页/499条</span><ul class="page-list"><li><a href="?page=50">50</a></li></ul><div mid="99999999999999706"></div><a class="next" href="?page=2">下一页</a>',
        )
        self.assertEqual(
            self.conn.scalar(
                select(self.db.windows.c.status).where(
                    self.db.windows.c.id == first['payload']['window_id']
                )
            ),
            'running',
        )
        window = self.row(self.db.windows)
        child = self.s.add_window(
            self.conn,
            self.tid,
            self.qid,
            window['start_at'],
            window['start_at'] + timedelta(hours=1),
            adaptive=True,
            parent_id=window['id'],
        )
        self.w.process_result(
            self.conn,
            dict(
                task_id=self.tid,
                kind='search',
                cookie_id=None,
                payload=dict(window_id=child, page=50),
            ),
            '<div mid="99999999999999707"></div>',
        )
        self.assertEqual(
            self.conn.scalar(select(self.db.windows.c.status).where(self.db.windows.c.id == child)),
            'limited',
        )

    def test_compaction_rollback_restores_job_and_no_receipt(self):
        from server.collector.maintenance import compact_task

        self.s.enqueue(self.conn, self.tid, 'post', 'post:rollback', {'post_id': 'rollback'})
        self.conn.execute(
            self.db.jobs.update()
            .where(self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'post')
            .values(status='done', finished_at=self.db.utcnow())
        )
        nested = self.conn.begin_nested()
        self.assertEqual(compact_task(self.conn, self.tid, keep=0), 1)
        nested.rollback()
        self.assertIsNotNone(
            self.conn.execute(
                select(self.db.jobs.c.id).where(
                    self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'post'
                )
            ).first()
        )
        self.assertIsNone(
            self.conn.execute(
                select(self.db.receipts).where(self.db.receipts.c.task_id == self.tid)
            ).first()
        )

    def test_paused_upgrade_keeps_done_hours_and_covers_remaining_gaps(self):
        from server.collector.planning import enable_adaptive

        wid = self.conn.scalar(
            select(self.db.windows.c.id)
            .where(self.db.windows.c.task_id == self.tid)
            .order_by(self.db.windows.c.start_at)
            .limit(1)
        )
        first = next(
            r
            for r in self.conn.execute(
                select(self.db.jobs).where(self.db.jobs.c.task_id == self.tid)
            ).mappings()
            if r['payload']['window_id'] == wid
        )
        self.w.process_result(self.conn, first, '未找到相关结果')
        self.conn.execute(
            self.db.jobs.update().where(self.db.jobs.c.id == first['id']).values(status='done')
        )
        self.conn.execute(
            self.db.tasks.update().where(self.db.tasks.c.id == self.tid).values(status='paused')
        )
        enable_adaptive(self.conn, self.tid)
        enable_adaptive(self.conn, self.tid)
        rows = (
            self.conn.execute(
                select(self.db.windows)
                .where(self.db.windows.c.task_id == self.tid)
                .order_by(self.db.windows.c.start_at)
            )
            .mappings()
            .all()
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['status'], 'done')
        self.assertEqual(rows[0]['end_at'], rows[1]['start_at'])
        self.assertEqual(rows[-1]['end_at'], self.task()['end_at'])
        self.assertEqual(self.s.task_metrics(self.conn, self.task())['covered_query_hours'], 1)
        self.assertEqual(self.task()['status'], 'paused')

    def test_compaction_retains_dedupe_and_failed_checkpoint(self):
        from server.collector.maintenance import compact_task

        first = self.row(self.db.jobs)
        self.w.process_result(self.conn, first, '未找到相关结果')
        self.conn.execute(
            self.db.jobs.update()
            .where(self.db.jobs.c.id == first['id'])
            .values(status='done', finished_at=self.db.utcnow())
        )
        self.s.enqueue(self.conn, self.tid, 'post', 'post:compact', {'post_id': 'compact'})
        self.conn.execute(
            self.db.jobs.update()
            .where(self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'post')
            .values(status='done', finished_at=self.db.utcnow())
        )
        self.s.enqueue(self.conn, self.tid, 'longtext', 'longtext:failed', {'post_id': 'failed'})
        self.conn.execute(
            self.db.jobs.update()
            .where(self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'longtext')
            .values(status='failed', last_error='missing_long_text')
        )
        self.assertEqual(compact_task(self.conn, self.tid, keep=0), 2)
        self.s.enqueue(self.conn, self.tid, 'post', 'post:compact', {'post_id': 'compact'})
        self.s.enqueue(self.conn, self.tid, 'search', first['dedupe_key'], first['payload'])
        self.assertIsNone(
            self.conn.execute(
                select(self.db.jobs).where(
                    self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'post'
                )
            ).first()
        )
        self.assertEqual(self.s.task_metrics(self.conn, self.task())['jobs']['done'], 2)
        self.assertEqual(self.s.task_metrics(self.conn, self.task())['failed_jobs'], 1)
        self.assertEqual(compact_task(self.conn, self.tid, keep=0), 0)
