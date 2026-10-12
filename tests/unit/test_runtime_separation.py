"""Offline contracts: platform startup has no database/collector side effects."""

import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch


class RuntimeSeparationTests(unittest.TestCase):
    def test_platform_start_stop_and_manual_actions(self):
        # Isolated imports also cover Docker's mounted /weibo application.
        script = """
import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import db
from server.collector.worker import WorkerPool

with patch.object(db.engine, 'connect', side_effect=AssertionError('unexpected DB connection')), \\
     patch.object(db.engine, 'begin', side_effect=AssertionError('unexpected DB write')), \\
     patch.object(db, 'init_db') as initialize, \\
     patch.object(WorkerPool, 'start') as start, \\
     patch.object(WorkerPool, 'shutdown') as stop:
    from server import app
    prefix = os.environ['APP_BASE_PATH'].rstrip('/')
    with TestClient(app.app) as client:
        assert client.get(prefix + '/api/openapi.json').status_code == 200
        # Database-backed reads remain enabled; fake only the connection for this test.
        with patch.object(db.engine, 'connect') as connection:
            assert client.get(prefix + '/api/health').json()['database'] == 'connected'
            assert client.get(prefix + '/api/queries').status_code == 200
            assert connection.call_count == 2
        with patch.object(app, 'task_action') as action:
            for name in ('start', 'resume', 'pause', 'retry', 'cancel'):
                assert client.post(prefix + '/api/tasks/test/' + name).status_code == 200
                action.assert_called_with('test', name)
        with patch.object(app, 'create_task', return_value='new-task') as create:
            result = client.post(prefix + '/api/tasks', json={
                'name': 'manual', 'start_at': '2020-01-01T00:00:00',
                'end_at': '2020-01-02T00:00:00', 'cookie_ids': []})
            assert result.status_code == 200
            assert result.json()['id'] == 'new-task'
            create.assert_called_once()
        # Existing same-origin protection still works, without adding read-only restrictions.
        assert client.post(prefix + '/api/tasks/test/pause',
                           headers={'Origin': 'https://untrusted.example'}).status_code == 403
    initialize.assert_not_called()
    start.assert_not_called()
    stop.assert_not_called()
"""
        for base in ('', '/weibo'):
            with self.subTest(base=base):
                env = dict(
                    os.environ,
                    APP_BASE_PATH=base,
                    DATABASE_URL='mysql+pymysql://offline:offline@127.0.0.1:1/offline',
                )
                env.pop('DISABLE_WORKERS', None)
                result = subprocess.run(
                    [sys.executable, '-c', script],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_executor_lifecycle(self):
        from server.executor import run_worker
        from server import db

        worker = Mock()
        with (
            patch('server.collector.worker.WorkerPool', return_value=worker) as factory,
            patch('server.executor.signal.signal') as signals,
            patch.object(db, 'init_db') as initialize,
            patch.object(db.engine, 'connect', side_effect=AssertionError('unexpected DB')),
        ):
            run_worker(['one-task'], 5)
            factory.assert_called_once_with(task_ids=['one-task'])
            worker.start.assert_called_once()
            worker.stop.wait.assert_called_once_with(5)
            worker.shutdown.assert_called_once()
            self.assertEqual(signals.call_count, 4)
            initialize.assert_not_called()

    def test_executor_cleans_up_on_error(self):
        from server.executor import run_worker

        worker = Mock()
        worker.stop.wait.side_effect = RuntimeError('test interruption')
        with (
            patch('server.collector.worker.WorkerPool', return_value=worker),
            patch('server.executor.signal.signal'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'test interruption'):
                run_worker()
        worker.shutdown.assert_called_once()
        worker.stop.wait.assert_called_once_with(None)

    def test_cli_init_is_explicit(self):
        from server import cli

        with (
            patch.object(cli, 'init_db') as initialize,
            patch.object(cli, 'run_worker') as worker,
            patch.object(sys, 'argv', ['cli', 'init']),
        ):
            cli.main()
            initialize.assert_called_once()
            worker.assert_not_called()

    def test_legacy_worker_command_uses_separate_executor(self):
        from server import cli

        with (
            patch.object(cli, 'init_db') as initialize,
            patch.object(cli, 'run_worker') as worker,
            patch.object(sys, 'argv', ['cli', 'worker', '--task', 'one-task', '--seconds', '2']),
            patch('builtins.print'),
        ):
            cli.main()
            worker.assert_called_once_with(['one-task'], 2)
            initialize.assert_not_called()
