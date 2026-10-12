"""Opt-in, capped real requests plus explicitly labelled Cookie-expiry fault injection."""

import argparse
import json
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select
from server.db import engine, cookies, tables, tasks, logs, utcnow, event
from server.service import create_task, task_action
from server.worker import WorkerPool, RemoteError, fetch, API
from server.security import decrypt
from server.cli import snapshot


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cookie', action='append', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--seconds', type=int, default=120)
    a = p.parse_args()
    with engine.connect() as c:
        accounts = [
            dict(x) for x in c.execute(select(cookies).where(cookies.c.id.in_(a.cookie))).mappings()
        ]
        qid = c.scalar(
            select(tables['queries'].c.query_id).where(
                tables['queries'].c.query == '互联网医院 精神科'
            )
        )

    # Probe both credentials concurrently; report this separately from successful crawl concurrency.
    def probe(account):
        started = utcnow()
        try:
            fetch(decrypt(account['encrypted_value']), API + 'show', {'id': '5326118891815802'})
            outcome, status = 'ok', 200
        except RemoteError as exc:
            outcome, status = exc.code, exc.status
        return dict(
            cookie_id=account['id'],
            name=account['name'],
            outcome=outcome,
            http_status=status,
            started_at=started,
            finished_at=utcnow(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        probes = list(executor.map(probe, accounts))
    valid = [x['cookie_id'] for x in probes if x['outcome'] == 'ok']
    report = {'probes': probes, 'real_dual_valid_cookie_test': len(valid) >= 2}
    if not valid:
        raise RuntimeError('没有可用 Cookie；停止，不执行采集')
    tid = create_task(
        dict(
            name='限量实测：小时队列与失效恢复',
            start_at='2026-07-29T18:00:00',
            end_at='2026-07-29T23:00:00',
            query_ids=[qid],
            cookie_ids=valid,
            include_comments=True,
            request_limit=30,
        )
    )
    task_action(tid, 'start')
    pool = WorkerPool(task_ids=[tid], verbose_logs=True)
    deadline = time.monotonic() + 20
    claimed = None
    while claimed is None and time.monotonic() < deadline:
        claimed = pool.claim(valid[0])
        if claimed is None:
            time.sleep(0.25)
    assert claimed

    def simulate_expiry(*_):
        raise RemoteError('login_expired', 200)

    with engine.begin() as c:
        event(c, 'fault_injection', '模拟 HTTP 200 / ok=-100；不是实际 Cookie 失效', tid, valid[0])
    pool.execute(*claimed, fetcher=simulate_expiry)
    report['simulated_interruption'] = snapshot(tid)
    # Revalidate the actual credential before resuming the exact stored checkpoint.
    account = next(x for x in accounts if x['id'] == valid[0])
    fetch(decrypt(account['encrypted_value']), API + 'show', {'id': '5326118891815802'})
    with engine.begin() as c:
        c.execute(
            cookies.update()
            .where(cookies.c.id == valid[0])
            .values(status='active', last_error=None, last_checked_at=utcnow())
        )
    task_action(tid, 'retry')
    report['after_retry'] = snapshot(tid)
    if len(valid) >= 2:
        # Align a pair of real requests after cooldown, using the production claim/ACK path.
        with engine.connect() as c:
            deadlines = (
                c.execute(select(cookies.c.next_request_at).where(cookies.c.id.in_(valid)))
                .scalars()
                .all()
            )
        delay = max([(d - utcnow()).total_seconds() for d in deadlines if d] + [0])
        if delay > 0:
            time.sleep(delay + 0.1)
        pair = [pool.claim(cid) for cid in valid[:2]]
        assert all(pair)
        barrier = threading.Barrier(2)
        http_spans = []

        def synchronized_fetch(*args):
            barrier.wait(timeout=30)
            started = utcnow()
            result = fetch(*args)
            http_spans.append(
                {
                    'thread': threading.current_thread().name,
                    'started_at': started,
                    'finished_at': utcnow(),
                }
            )
            return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(lambda claim: pool.execute(*claim, fetcher=synchronized_fetch), pair)
            )
        report['concurrent_batch'] = {
            'jobs': [x[0]['id'] for x in pair],
            'windows': [x[0]['payload'].get('window_id') for x in pair],
            'success': outcomes,
            'http_spans': http_spans,
            'http_overlap': len(http_spans) == 2
            and max(x['started_at'] for x in http_spans)
            < min(x['finished_at'] for x in http_spans),
        }
    pool.start()
    try:
        deadline = time.monotonic() + a.seconds
        while time.monotonic() < deadline:
            time.sleep(1)
            with engine.connect() as c:
                status = c.scalar(select(tasks.c.status).where(tasks.c.id == tid))
            if status != 'running':
                break
        else:
            task_action(tid, 'pause')
    finally:
        pool.shutdown()
    report['final'] = snapshot(tid)
    with engine.connect() as c:
        report['failures'] = [
            dict(x)
            for x in c.execute(
                select(logs.c.details).where(
                    logs.c.task_id == tid, logs.c.event == 'request_failed'
                )
            ).mappings()
        ]
        report['request_timings'] = [
            dict(x)
            for x in c.execute(
                select(logs.c.cookie_id, logs.c.details).where(
                    logs.c.task_id == tid, logs.c.event == 'request_done'
                )
            ).mappings()
        ]
    report['same_checkpoint_retried'] = any(
        x['job_id'] == claimed[0]['id'] and x['outcome'] == 'done'
        for x in report['final']['attempts']
    )
    path = Path(a.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(
        json.dumps(
            dict(
                task_id=tid,
                status=report['final']['task']['status'],
                posts=report['final']['task']['posts_count'],
                comments=report['final']['task']['comments_count'],
                same_checkpoint_retried=report['same_checkpoint_retried'],
                probes=probes,
                report=str(path),
            ),
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == '__main__':
    main()
