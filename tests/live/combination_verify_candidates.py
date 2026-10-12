"""Finish at most three existing combination candidates, with <=8 real requests."""

import argparse
import json
import time
from pathlib import Path
from sqlalchemy import select
from server.db import engine, tasks, jobs, keyword_checks, tables, utcnow
from server.service import task_action
from server.collector.worker import WorkerPool


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    with engine.begin() as c:
        task = (
            c.execute(select(tasks).where(tasks.c.id == a.task).with_for_update()).mappings().one()
        )
        if task['status'] not in ('paused', 'quota_paused') or not task['name'].startswith(
            '限量实测：'
        ):
            raise ValueError('Only paused smoke-test tasks may be continued')
        pids = (
            c.execute(
                select(keyword_checks.c.post_id)
                .where(keyword_checks.c.task_id == a.task, keyword_checks.c.status == 'pending')
                .distinct()
                .order_by(keyword_checks.c.post_id)
                .limit(3)
            )
            .scalars()
            .all()
        )
        c.execute(
            tasks.update()
            .where(tasks.c.id == a.task)
            .values(request_limit=task['request_count'] + 8)
        )
        c.execute(
            jobs.update()
            .where(jobs.c.task_id == a.task, jobs.c.status == 'pending')
            .values(priority=-100)
        )
        for row in c.execute(
            select(jobs).where(jobs.c.task_id == a.task, jobs.c.status == 'pending')
        ).mappings():
            if row['kind'] in ('post', 'longtext') and row['payload'].get('post_id') in pids:
                c.execute(jobs.update().where(jobs.c.id == row['id']).values(priority=200))
    pool = WorkerPool(task_ids=[a.task])
    task_action(a.task, 'resume')
    try:
        deadline = time.monotonic() + 150
        calls = 0
        while time.monotonic() < deadline and calls < 8:
            with engine.begin() as c:
                pending = c.execute(
                    select(keyword_checks.c.post_id).where(
                        keyword_checks.c.task_id == a.task,
                        keyword_checks.c.post_id.in_(pids),
                        keyword_checks.c.status == 'pending',
                    )
                ).first()
                ready = [
                    r
                    for r in c.execute(
                        select(jobs).where(jobs.c.task_id == a.task, jobs.c.status == 'pending')
                    ).mappings()
                    if r['kind'] in ('post', 'longtext')
                    and r['payload'].get('post_id') in pids
                    and (not r['lease_until'] or r['lease_until'] <= utcnow())
                ]
                for row in ready:
                    c.execute(jobs.update().where(jobs.c.id == row['id']).values(priority=200))
            if not pending:
                break
            if not ready:
                time.sleep(1)
                continue
            item = pool.claim(task['cookie_ids'][0])
            if not item:
                time.sleep(1)
                continue
            if (
                item[0]['kind'] not in ('post', 'longtext')
                or item[0]['payload'].get('post_id') not in pids
            ):
                raise RuntimeError('Selected sample did not have the expected queued checkpoint')
            pool.execute(*item)
            calls += 1
    finally:
        task_action(a.task, 'pause')
    with engine.connect() as c:
        rows = [
            dict(r)
            for r in c.execute(
                select(keyword_checks, tables['queries'].c.query, tables['posts'].c.post_text)
                .join(tables['queries'], keyword_checks.c.query_id == tables['queries'].c.query_id)
                .join(tables['posts'], keyword_checks.c.post_id == tables['posts'].c.post_id)
                .where(keyword_checks.c.task_id == a.task, keyword_checks.c.post_id.in_(pids))
            ).mappings()
        ]
    report = dict(task_id=a.task, requests=calls, checks=rows)
    Path(a.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8'
    )
    print(
        json.dumps(
            dict(
                requests=calls,
                results=[
                    dict(query=r['query'], status=r['status'], missing=r['missing_terms'])
                    for r in rows
                ],
            ),
            ensure_ascii=False,
        )
    )


if __name__ == '__main__':
    main()
