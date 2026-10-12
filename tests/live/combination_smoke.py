"""Explicitly opt-in, capped mixed keyword/AND search through real worker leases."""

import argparse
import json
import time
from pathlib import Path
from sqlalchemy import select, func
from server.db import engine, cookies, tasks, jobs, keyword_checks, tables
from server.service import create_task, task_action
from server.collector.worker import WorkerPool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--left', default='互联网医院')
    parser.add_argument('--right', default='精神科、心理')
    parser.add_argument('--ordinary', default='互联网医院')
    parser.add_argument('--limit', type=int, default=18)
    parser.add_argument('--output', required=True)
    a = parser.parse_args()
    if not 1 <= a.limit <= 24:
        raise ValueError('Small live test limited to 24 requests')
    with engine.connect() as conn:
        ids = (
            conn.execute(select(cookies.c.id).where(cookies.c.status == 'active').limit(2))
            .scalars()
            .all()
        )
    if not ids:
        raise RuntimeError('No active Cookie; no test started')
    tid = create_task(
        dict(
            name='限量实测：普通词与双词组合',
            start_at=a.start,
            end_at=a.end,
            keywords_text=a.ordinary,
            combination_left=a.left,
            combination_right=a.right,
            cookie_ids=ids,
            include_comments=False,
            request_limit=a.limit,
        )
    )
    with engine.begin() as conn:
        # Cover all query types before following detail jobs within this small request budget.
        conn.execute(jobs.update().where(jobs.c.task_id == tid).values(priority=100))
    pool = WorkerPool(task_ids=[tid])
    task_action(tid, 'start')
    print(json.dumps({'task_id': tid, 'limit': a.limit}, ensure_ascii=False), flush=True)
    try:
        pool.start()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            time.sleep(2)
            with engine.connect() as conn:
                status = conn.scalar(select(tasks.c.status).where(tasks.c.id == tid))
            if status != 'running':
                break
    finally:
        task_action(tid, 'pause')
        pool.shutdown()
    with engine.connect() as conn:
        task = dict(conn.execute(select(tasks).where(tasks.c.id == tid)).mappings().one())
        counts = dict(
            conn.execute(
                select(keyword_checks.c.status, func.count())
                .where(keyword_checks.c.task_id == tid)
                .group_by(keyword_checks.c.status)
            ).all()
        )
        rows = [
            dict(r)
            for r in conn.execute(
                select(keyword_checks, tables['queries'].c.query)
                .join(tables['queries'], keyword_checks.c.query_id == tables['queries'].c.query_id)
                .where(keyword_checks.c.task_id == tid)
            ).mappings()
        ]
        used = (
            conn.execute(
                select(jobs.c.cookie_id)
                .where(jobs.c.task_id == tid, jobs.c.cookie_id.is_not(None))
                .distinct()
            )
            .scalars()
            .all()
        )
        searches = [
            dict(r)
            for r in conn.execute(
                select(jobs.c.status, jobs.c.last_error, jobs.c.payload).where(
                    jobs.c.task_id == tid, jobs.c.kind == 'search'
                )
            ).mappings()
        ]
    report = dict(
        task_id=tid,
        requests=task['request_count'],
        cookies_used=len(used),
        verification=counts,
        searches=searches,
        checks=rows,
        scope='Limited sample, not full recall; no comments requested',
    )
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8'
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ('searches', 'checks')}, ensure_ascii=False
        ),
        flush=True,
    )


if __name__ == '__main__':
    main()
