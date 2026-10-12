"""Per-Cookie workers backed by fenced MySQL leases and atomic checkpoints."""

import threading
import uuid
import time
from datetime import timedelta
from sqlalchemy import select, func, or_, and_, case
from server.db import (
    engine,
    tasks,
    cookies,
    windows,
    jobs,
    tables,
    audits,
    threads,
    attempts,
    utcnow,
    upsert,
    event,
    keyword_checks,
)
from server.service import stop_task, update_audit, window_seconds
from server.collector.pacing import request_delay
from server.collector.maintenance import compact_once
from server.security import decrypt
from server.collector.transport import fetch, RemoteError
from server.collector.records import endpoint_for, process_result
from research_models import now


class WorkerPool:
    def __init__(self, task_ids=None, verbose_logs=False):
        self.stop = threading.Event()
        self.workers = {}
        self.owner = uuid.uuid4().hex
        self.task_ids = frozenset(task_ids) if task_ids is not None else None
        self.verbose_logs = verbose_logs
        self.last_compaction = time.monotonic()

    def start(self):
        self.manager = threading.Thread(target=self.manage, daemon=True, name='weibo-scheduler')
        self.manager.start()

    def shutdown(self):
        self.stop.set()
        if hasattr(self, 'manager'):
            self.manager.join(timeout=5)
        for worker in list(self.workers.values()):
            worker.join(timeout=35)

    def recover_expired(self):
        with engine.connect() as conn:
            expired = (
                conn.execute(
                    select(jobs).where(jobs.c.status == 'running', jobs.c.lease_until < utcnow())
                )
                .mappings()
                .all()
            )
        for job in expired:
            if self.task_ids is not None and job['task_id'] not in self.task_ids:
                continue
            self.fail(dict(job), job['cookie_id'], RemoteError('lease_expired'))

    def manage(self):
        while not self.stop.wait(2):
            try:
                self.recover_expired()
                with engine.begin() as conn:
                    active = (
                        conn.execute(select(cookies.c.id).where(cookies.c.status == 'active'))
                        .scalars()
                        .all()
                    )
                    for task in (
                        conn.execute(select(tasks).where(tasks.c.status == 'running'))
                        .mappings()
                        .all()
                    ):
                        if self.task_ids is not None and task['id'] not in self.task_ids:
                            continue
                        counts = dict(
                            conn.execute(
                                select(jobs.c.status, func.count())
                                .where(jobs.c.task_id == task['id'])
                                .group_by(jobs.c.status)
                            ).all()
                        )
                        if (
                            counts.get('pending')
                            and not counts.get('running')
                            and not set(task['cookie_ids']).intersection(active)
                        ):
                            locked = (
                                conn.execute(
                                    select(tasks).where(tasks.c.id == task['id']).with_for_update()
                                )
                                .mappings()
                                .one()
                            )
                            if locked['status'] == 'running':
                                stop_task(conn, locked, 'needs_attention', 'no_active_cookie')
                            continue
                        if not counts.get('pending') and not counts.get('running'):
                            gaps = conn.scalar(
                                select(func.count())
                                .select_from(windows)
                                .where(
                                    windows.c.task_id == task['id'],
                                    windows.c.status.in_(['limited', 'error']),
                                )
                            )
                            incomplete = conn.scalar(
                                select(func.count())
                                .select_from(audits)
                                .where(
                                    audits.c.task_id == task['id'],
                                    audits.c.comments_complete.is_(False),
                                )
                            )
                            planned = int(
                                (task['end_at'] - task['start_at']).total_seconds() / 3600
                            ) * len(task['query_ids'])
                            incomplete += conn.scalar(
                                select(func.count())
                                .select_from(keyword_checks)
                                .where(
                                    keyword_checks.c.task_id == task['id'],
                                    keyword_checks.c.status.in_(['pending', 'outside_time_range']),
                                )
                            )
                            done = window_seconds(conn, task['id']).get('done', 0) / 3600
                            status = (
                                'partial'
                                if counts.get('failed')
                                or counts.get('blocked')
                                or gaps
                                or incomplete
                                or done != planned
                                else 'completed'
                            )
                            locked = (
                                conn.execute(
                                    select(tasks).where(tasks.c.id == task['id']).with_for_update()
                                )
                                .mappings()
                                .one()
                            )
                            if locked['status'] == 'running':
                                stop_task(conn, locked, status)
                                event(
                                    conn,
                                    'task_finished',
                                    '任务执行结束，存在数据缺口'
                                    if status == 'partial'
                                    else '任务执行完成',
                                    task['id'],
                                )
                for cid in active:
                    if cid not in self.workers or not self.workers[cid].is_alive():
                        t = threading.Thread(target=self.run_cookie, args=(cid,), daemon=True)
                        self.workers[cid] = t
                        t.start()
                if time.monotonic() - self.last_compaction >= 60:
                    compact_once(self.task_ids)
                    self.last_compaction = time.monotonic()
            except Exception:
                # A transient database outage must not kill the scheduler or reveal credentials.
                self.stop.wait(3)

    def claim(self, cid):
        with engine.begin() as conn:
            cookie = (
                conn.execute(
                    select(cookies).where(cookies.c.id == cid).with_for_update(skip_locked=True)
                )
                .mappings()
                .first()
            )
            if not cookie or cookie['status'] != 'active':
                return None
            if cookie['lease_until'] and cookie['lease_until'] > utcnow():
                return None
            if cookie['next_request_at'] and cookie['next_request_at'] > utcnow():
                return None
            for task in (
                conn.execute(
                    select(tasks).where(tasks.c.status == 'running').order_by(tasks.c.created_at)
                )
                .mappings()
                .all()
            ):
                if self.task_ids is not None and task['id'] not in self.task_ids:
                    continue
                if cid not in task['cookie_ids']:
                    continue
                task = (
                    conn.execute(select(tasks).where(tasks.c.id == task['id']).with_for_update())
                    .mappings()
                    .one()
                )
                if task['status'] != 'running':
                    continue
                if task['request_limit'] and task['request_count'] >= task['request_limit']:
                    stop_task(conn, task, 'quota_paused', 'request_limit')
                    event(
                        conn,
                        'request_limit',
                        '达到任务请求额度，自动暂停',
                        task['id'],
                        cid,
                        level='warning',
                    )
                    continue
                job = (
                    conn.execute(
                        select(jobs)
                        .where(
                            jobs.c.task_id == task['id'],
                            jobs.c.status == 'pending',
                            or_(jobs.c.lease_until.is_(None), jobs.c.lease_until <= utcnow()),
                        )
                        .order_by(
                            case(
                                (
                                    and_(
                                        jobs.c.kind == 'search',
                                        jobs.c.created_at <= utcnow() - timedelta(seconds=30),
                                    ),
                                    1,
                                ),
                                else_=0,
                            ).desc()
                            if cookie['request_count'] % 4 == 0
                            else jobs.c.priority.desc(),
                            jobs.c.priority.desc(),
                            jobs.c.created_at,
                            jobs.c.id,
                        )
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                    .mappings()
                    .first()
                )
                if not job:
                    continue
                until = utcnow() + timedelta(seconds=120)
                token = str(uuid.uuid4())
                conn.execute(
                    cookies.update()
                    .where(cookies.c.id == cid)
                    .values(
                        lease_owner=token,
                        lease_until=until,
                        next_request_at=utcnow() + timedelta(seconds=request_delay(cookie)),
                        request_count=cookies.c.request_count + 1,
                    )
                )
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == job['id'])
                    .values(
                        status='running',
                        cookie_id=cid,
                        lease_owner=token,
                        lease_until=until,
                        attempts=jobs.c.attempts + 1,
                    )
                )
                conn.execute(
                    attempts.insert().values(
                        id=token,
                        job_id=job['id'],
                        task_id=task['id'],
                        cookie_id=cid,
                        checkpoint=job['payload'],
                        kind=job['kind'],
                        started_at=utcnow(),
                        outcome='running',
                    )
                )
                conn.execute(
                    tasks.update()
                    .where(tasks.c.id == task['id'])
                    .values(request_count=tasks.c.request_count + 1)
                )
                if job['kind'] == 'search':
                    conn.execute(
                        windows.update()
                        .where(windows.c.id == job['payload']['window_id'])
                        .values(status='running')
                    )
                return dict(
                    job, cookie_id=cid, attempts=job['attempts'] + 1, lease_owner=token
                ), dict(cookie)
        return None

    def execute(self, job, cookie, fetcher=None):
        """Network outside transaction; records, next cursor and ACK commit together."""
        cid = cookie['id']
        try:
            with engine.connect() as conn:
                endpoint, params = endpoint_for(job, conn)
            network_started = utcnow()
            result = (fetcher or fetch)(decrypt(cookie['encrypted_value']), endpoint, params)
            network_finished = utcnow()
            with engine.begin() as conn:
                conn.execute(
                    select(cookies.c.id).where(cookies.c.id == cid).with_for_update()
                ).one()
                conn.execute(
                    select(tasks.c.id).where(tasks.c.id == job['task_id']).with_for_update()
                ).one()
                current = (
                    conn.execute(select(jobs).where(jobs.c.id == job['id']).with_for_update())
                    .mappings()
                    .one()
                )
                if (
                    current['status'] != 'running'
                    or current['lease_owner'] != job['lease_owner']
                    or current['lease_until'] < utcnow()
                ):
                    return False
                process_result(conn, job, result)
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == job['id'])
                    .values(
                        status='done',
                        finished_at=utcnow(),
                        lease_until=None,
                        lease_owner=None,
                        last_error=None,
                    )
                )
                conn.execute(
                    cookies.update()
                    .where(cookies.c.id == cid, cookies.c.lease_owner == job['lease_owner'])
                    .values(
                        lease_owner=None, lease_until=None, last_error=None, consecutive_failures=0
                    )
                )
                if self.verbose_logs:
                    conn.execute(
                        attempts.update()
                        .where(attempts.c.id == job['lease_owner'])
                        .values(outcome='done', finished_at=utcnow(), http_status=200)
                    )
                    event(
                        conn,
                        'request_done',
                        '请求完成且数据已提交',
                        job['task_id'],
                        cid,
                        kind=job['kind'],
                        job_id=job['id'],
                        network_started_at=network_started.isoformat(),
                        network_finished_at=network_finished.isoformat(),
                    )
                else:
                    # Committed jobs/windows already hold the durable resume checkpoint.
                    conn.execute(attempts.delete().where(attempts.c.id == job['lease_owner']))
            return True
        except Exception as exc:
            self.fail(job, cid, exc)
            return False

    def run_cookie(self, cid):
        from server.collector.transport import close_transport

        try:
            self._run_cookie(cid)
        finally:
            close_transport()

    def _run_cookie(self, cid):
        while not self.stop.wait(0.5):
            try:
                with engine.connect() as conn:
                    status = conn.scalar(select(cookies.c.status).where(cookies.c.id == cid))
                if status != 'active':
                    return
                claimed = self.claim(cid)
                if not claimed:
                    continue
                job, cookie = claimed
                self.execute(job, cookie)
            except Exception:
                self.stop.wait(3)

    def fail(self, job, cid, exc):
        code = exc.code if isinstance(exc, RemoteError) else 'processing_error'
        auth = code in (
            'login_expired',
            'access_restricted',
            'rate_limited',
            'unrecognized_search_page',
        )
        with engine.begin() as conn:
            channel = (
                conn.execute(select(cookies).where(cookies.c.id == cid).with_for_update())
                .mappings()
                .one()
            )
            task = (
                conn.execute(select(tasks).where(tasks.c.id == job['task_id']).with_for_update())
                .mappings()
                .one()
            )
            current = (
                conn.execute(select(jobs).where(jobs.c.id == job['id']).with_for_update())
                .mappings()
                .one()
            )
            if current['status'] != 'running' or current['lease_owner'] != job['lease_owner']:
                return
            if code == 'lease_expired' and current['lease_until'] >= utcnow():
                return
            invisible = code in ('deleted', 'not_visible') and job['kind'] == 'post'
            # Content/schema/database errors are not evidence of an unhealthy Cookie.
            channel_failure = (
                auth or code in ('network_error', 'non_json_response') or code.startswith('http_5')
            )
            failures = (
                channel['consecutive_failures'] + 1
                if channel_failure
                else (0 if invisible else channel['consecutive_failures'])
            )
            suspend = auth or (channel_failure and failures >= channel['failure_limit'])
            other_active = conn.execute(
                select(cookies.c.id)
                .where(
                    cookies.c.id.in_(task['cookie_ids']),
                    cookies.c.id != cid,
                    cookies.c.status == 'active',
                )
                .limit(1)
            ).first()
            status = (
                'done'
                if invisible
                else 'pending'
                if suspend and other_active
                else 'blocked'
                if suspend
                else 'pending'
                if job['attempts'] < 3 and code != 'longtext_unconfirmed'
                else 'failed'
            )
            delay = min(300, 10 * 2 ** min(job['attempts'] - 1, 5))
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job['id'])
                .values(
                    status=status,
                    last_error=code,
                    attempts=0 if suspend else current['attempts'],
                    lease_owner=None,
                    lease_until=utcnow() + timedelta(seconds=delay)
                    if status == 'pending'
                    else None,
                )
            )
            conn.execute(
                attempts.update()
                .where(attempts.c.id == job['lease_owner'])
                .values(
                    outcome=code, finished_at=utcnow(), http_status=getattr(exc, 'status', None)
                )
            )
            values = dict(
                lease_owner=None,
                lease_until=None,
                last_error=code,
                next_request_at=utcnow() + timedelta(seconds=10)
                if channel_failure
                else channel['next_request_at'],
                consecutive_failures=failures,
            )
            if suspend:
                values['status'] = (
                    'expired'
                    if code == 'login_expired'
                    else 'restricted'
                    if auth
                    else 'error_paused'
                )
                if not other_active and task['status'] == 'running':
                    stop_task(conn, task, 'needs_attention', code)
            conn.execute(
                cookies.update()
                .where(cookies.c.id == cid, cookies.c.lease_owner == job['lease_owner'])
                .values(**values)
            )
            if invisible:
                upsert(
                    conn,
                    tables['posts'],
                    dict(
                        post_id=job['payload']['post_id'], retrieved_at=now(), retrieval_status=code
                    ),
                )
            if status in ('failed', 'blocked') and job['kind'] == 'search':
                conn.execute(
                    windows.update()
                    .where(windows.c.id == job['payload']['window_id'])
                    .values(status='error')
                )
            if status in ('failed', 'blocked') and job['kind'] == 'replies':
                conn.execute(
                    threads.update()
                    .where(
                        threads.c.task_id == job['task_id'],
                        threads.c.root_id == job['payload']['root_id'],
                    )
                    .values(last_error=code)
                )
            if job['kind'] in ('comments', 'replies'):
                update_audit(conn, job['task_id'], job['payload']['post_id'])
            event(
                conn,
                'request_failed',
                '请求未完成：' + code,
                job['task_id'],
                cid,
                level='warning',
                reason=code,
                error_type=type(exc).__name__,
                http_status=getattr(exc, 'status', None),
                job_id=job['id'],
                checkpoint=job['payload'],
                attempt=job['attempts'],
                retry_after_seconds=delay if status == 'pending' else None,
                consecutive_failures=failures,
                cookie_suspended=suspend,
            )


pool = WorkerPool()
