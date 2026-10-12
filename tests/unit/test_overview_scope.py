"""Read-only overview selection and task-isolated query contracts, no database access."""

import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


class OverviewScopeTests(unittest.TestCase):
    def setUp(self):
        from server import app

        self.app = app
        self.tasks = [
            dict(
                id=tid,
                name=tid,
                status=status,
                posts_count=n,
                comments_count=n * 10,
                replies_count=n * 2,
                running_jobs=n,
                cookie_ids=[tid + '-cookie'],
                query_ids=[tid + '-query'],
                start_at='2020-01-01',
                end_at='2020-02-01',
            )
            for tid, status, n in [
                ('new-paused', 'paused', 3),
                ('running-b', 'running', 2),
                ('running-a', 'running', 1),
            ]
        ]

    def scoped(self, choice, expected):
        conn = MagicMock()
        conn.scalar.side_effect = [9, 1]
        conn.execute.return_value.mappings.return_value = []
        with (
            patch.object(self.app, 'list_tasks', return_value=self.tasks),
            patch.object(self.app.engine, 'connect') as connect,
            patch.object(self.app.engine, 'begin', side_effect=AssertionError('write')),
            patch.object(self.app, 'coverage', return_value=[{'month': '2020-01'}]) as coverage,
        ):
            connect.return_value.__enter__.return_value = conn
            result = self.app.overview(choice)
            self.assertEqual(result['task_id'], expected['id'])
            self.assertEqual(result['counts']['posts'], expected['posts_count'])
            self.assertEqual(result['counts']['comments'], expected['comments_count'])
            self.assertEqual(result['counts']['users'], 9)
            coverage.assert_called_once_with(expected['id'])
            # User count deduplicates actual users after scoping both authors and commenters.
            users_query = conn.scalar.call_args_list[0].args[0].compile()
            self.assertIn('task_posts', str(users_query))
            self.assertIn('task_comments', str(users_query))
            self.assertEqual(list(users_query.params.values()).count(expected['id']), 2)
            channel_query = conn.scalar.call_args_list[1].args[0].compile()
            self.assertIn(expected['cookie_ids'], channel_query.params.values())
            log_query = conn.execute.call_args.args[0].compile()
            self.assertIn(expected['id'], log_query.params.values())
            return result

    def test_default_prefers_running_not_newest_paused_or_combined(self):
        self.scoped(None, self.tasks[1])

    def test_global_totals_count_table_rows_without_task_or_status_filters(self):
        conn = MagicMock()
        conn.scalar.side_effect = [100, 250, 80, 50]
        with patch.object(self.app.engine, 'connect') as connect:
            connect.return_value.__enter__.return_value = conn
            result = self.app.data_totals()
        self.assertEqual(result, dict(posts=100, comments=250, users=80, replies=50))
        for call in conn.scalar.call_args_list[:3]:
            sql = str(call.args[0].compile())
            self.assertNotIn('WHERE', sql)
            self.assertNotIn('JOIN', sql)
        self.assertIn('comment_level', str(conn.scalar.call_args_list[3].args[0].compile()))

    def test_explicit_task_stays_selected_even_when_others_run(self):
        self.scoped('new-paused', self.tasks[0])

    def test_no_running_uses_latest_task(self):
        for t in self.tasks:
            t['status'] = 'completed'
        self.scoped(None, self.tasks[0])

    def test_empty_has_no_coverage_or_global_counts(self):
        with (
            patch.object(self.app, 'list_tasks', return_value=[]),
            patch.object(self.app.engine, 'connect', side_effect=AssertionError('unexpected DB')),
        ):
            result = self.app.overview()
        self.assertIsNone(result['task_id'])
        self.assertEqual(result['coverage'], [])
        self.assertEqual(result['counts'], {})

    def test_missing_explicit_task_does_not_silently_show_another(self):
        with patch.object(self.app, 'list_tasks', return_value=self.tasks):
            with self.assertRaises(HTTPException) as caught:
                self.app.overview('missing')
        self.assertEqual(caught.exception.status_code, 404)
