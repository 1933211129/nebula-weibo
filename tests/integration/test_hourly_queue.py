"""Transactional MySQL tests: RUN_MYSQL_TESTS=1; all test writes roll back."""

import os
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from unittest.mock import patch


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Opt-in MySQL integration tests')
class HourlyQueueTests(unittest.TestCase):
    def setUp(self):
        from server import service, worker, db

        self.s, self.w, self.db = service, worker, db
        self.conn = db.engine.connect()
        self.tx = self.conn.begin()
        self.begin_patch = patch.object(service.engine, 'begin', lambda: nullcontext(self.conn))
        self.begin_patch.start()
        from sqlalchemy import select

        self.qid = self.conn.scalar(select(db.tables['queries'].c.query_id).limit(1))
        self.tid = service.create_task(
            dict(
                name='回滚测试：逐小时队列',
                start_date='2026-07-01',
                end_date='2026-07-01',
                query_ids=[self.qid],
                cookie_ids=[],
            )
        )

    def tearDown(self):
        self.begin_patch.stop()
        self.tx.rollback()
        self.conn.close()

    def row(self, table):
        from sqlalchemy import select

        return (
            self.conn.execute(select(table).where(table.c.task_id == self.tid)).mappings().first()
        )

    def task(self):
        from sqlalchemy import select

        return dict(
            self.conn.execute(select(self.db.tasks).where(self.db.tasks.c.id == self.tid))
            .mappings()
            .one()
        )

    def test_first_request_is_one_hour_even_for_full_day(self):
        w = self.row(self.db.windows)
        self.assertEqual(w['end_at'] - w['start_at'], timedelta(hours=1))
        endpoint, params = self.w.endpoint_for(self.row(self.db.jobs), self.conn)
        _, start, end = params['timescope'].split(':')
        self.assertEqual(
            datetime.strptime(end, '%Y-%m-%d-%H') - datetime.strptime(start, '%Y-%m-%d-%H'),
            timedelta(hours=1),
        )
        self.assertEqual(params['page'], 1)

    def test_progress_denominator_includes_unqueued_hours(self):
        m = self.s.task_metrics(self.conn, self.task())
        self.assertEqual(m['total_query_hours'], 24)
        self.assertEqual(m['unqueued_query_hours'], 0)
        self.assertEqual(m['date_progress'], 0)
        self.w.process_result(self.conn, self.row(self.db.jobs), '未找到相关结果')
        m = self.s.task_metrics(self.conn, self.task())
        self.assertEqual(m['covered_query_hours'], 1)
        self.assertEqual(m['date_progress'], 4.17)

    def test_exhausted_hour_enqueues_next_exactly_once(self):
        from sqlalchemy import select, func

        job = self.row(self.db.jobs)
        self.conn.execute(
            self.db.tasks.update()
            .where(self.db.tasks.c.id == self.tid)
            .values(end_at=datetime(2026, 7, 3))
        )
        self.w.process_result(self.conn, job, '未找到相关结果')
        self.w.process_result(self.conn, job, '未找到相关结果')
        rows = (
            self.conn.execute(
                select(self.db.windows)
                .where(self.db.windows.c.task_id == self.tid)
                .order_by(self.db.windows.c.start_at)
            )
            .mappings()
            .all()
        )
        self.assertEqual(len(rows), 25)
        self.assertEqual(rows[0]['end_at'], rows[1]['start_at'])
        self.assertEqual(rows[1]['end_at'].hour, 2)
        self.assertEqual(
            self.conn.scalar(
                select(func.count())
                .select_from(self.db.jobs)
                .where(self.db.jobs.c.task_id == self.tid)
            ),
            25,
        )

    def test_second_page_stays_in_same_hour(self):
        from sqlalchemy import select

        # A real result plus next link stays in the same hourly window.
        first = self.row(self.db.jobs)
        scope = self.w.endpoint_for(first, self.conn)[1]['timescope']
        self.w.process_result(
            self.conn,
            first,
            '<div mid="9988776655"></div><a class="next" href="?page=2">下一页</a>',
        )
        rows = (
            self.conn.execute(
                select(self.db.jobs).where(
                    self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'search'
                )
            )
            .mappings()
            .all()
        )
        self.assertEqual(len(rows), 25)
        second = next(r for r in rows if r['payload']['page'] == 2)
        self.assertEqual(self.w.endpoint_for(second, self.conn)[1]['timescope'], scope)
        self.assertEqual(self.s.task_metrics(self.conn, self.task())['covered_query_hours'], 0)

    def test_cap_retains_gap_and_moves_to_next_hour(self):
        from sqlalchemy import select

        job = dict(self.row(self.db.jobs))
        job['payload'] = dict(job['payload'], page=50)
        self.w.process_result(self.conn, job, '<div mid="99999999999999600"></div>')
        m = self.s.task_metrics(self.conn, self.task())
        self.assertEqual(m['covered_query_hours'], 0)
        self.assertEqual(m['processed_progress'], 4.17)
        rows = (
            self.conn.execute(select(self.db.windows).where(self.db.windows.c.task_id == self.tid))
            .mappings()
            .all()
        )
        self.assertEqual({r['status'] for r in rows}, {'limited', 'pending'})

    def test_final_hour_does_not_overrun_task_end(self):
        from sqlalchemy import select, func

        w = dict(self.row(self.db.windows))
        w['end_at'] = self.task()['end_at']
        w['start_at'] = w['end_at'] - timedelta(hours=1)
        self.s.next_hour(self.conn, self.task(), w)
        self.assertEqual(
            self.conn.scalar(
                select(func.count())
                .select_from(self.db.windows)
                .where(self.db.windows.c.task_id == self.tid)
            ),
            24,
        )

    def test_rejects_daily_window(self):
        with self.assertRaises(ValueError):
            self.s.add_window(
                self.conn, self.tid, self.qid, datetime(2026, 7, 1), datetime(2026, 7, 2)
            )

    def test_last_nonempty_page_finishes_hour_without_another_page(self):
        from sqlalchemy import select

        job = self.row(self.db.jobs)
        self.w.process_result(self.conn, job, '<div mid="9988776656"></div>')
        window = (
            self.conn.execute(
                select(self.db.windows).where(self.db.windows.c.id == job['payload']['window_id'])
            )
            .mappings()
            .one()
        )
        self.assertEqual(window['status'], 'done')
        searches = (
            self.conn.execute(
                select(self.db.jobs.c.payload).where(
                    self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'search'
                )
            )
            .scalars()
            .all()
        )
        self.assertTrue(all(row['page'] == 1 for row in searches))

    def test_empty_search_does_not_queue_recommendations(self):
        from sqlalchemy import select, func

        self.w.process_result(
            self.conn,
            self.row(self.db.jobs),
            '抱歉，未找到相关结果。<div class="card-wrap" mid="9988776655"></div><a class="next" href="?page=2">推荐下一页</a>',
        )
        self.assertEqual(
            self.conn.scalar(
                select(func.count())
                .select_from(self.db.jobs)
                .where(self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'post')
            ),
            0,
        )
        self.assertEqual(self.row(self.db.windows)['status'], 'done')
        search_jobs = (
            self.conn.execute(
                select(self.db.jobs.c.payload).where(
                    self.db.jobs.c.task_id == self.tid, self.db.jobs.c.kind == 'search'
                )
            )
            .scalars()
            .all()
        )
        self.assertTrue(all(row['page'] == 1 for row in search_jobs))

    def test_monthly_display_still_uses_full_scope(self):
        with patch.object(self.s.engine, 'connect', lambda: nullcontext(self.conn)):
            cells = self.s.coverage(self.tid)
        self.assertEqual(cells[0]['total'], 24 * 3600)
        self.assertEqual(cells[0]['progress'], 0)


if __name__ == '__main__':
    unittest.main()
