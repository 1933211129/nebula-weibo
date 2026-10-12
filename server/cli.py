"""Headless collection operations; credentials are read from files, never arguments."""

import argparse
import json
import uuid
from pathlib import Path
from sqlalchemy import select
from server.db import (
    engine,
    cookies,
    tasks,
    jobs,
    windows,
    attempts,
    audits,
    tables,
    utcnow,
    init_db,
)
from server.security import normalize_cookie, encrypt
from server.service import create_task, task_action, task_metrics
from server.collector.transport import fetch, API, RemoteError
from server.executor import run_worker


def install_cookies(path):
    results = []
    for item in json.loads(Path(path).read_text()):
        value, fingerprint = normalize_cookie(item['value'])
        with engine.begin() as conn:
            lookup = (
                cookies.c.id == item['id']
                if item.get('id')
                else cookies.c.fingerprint == fingerprint
            )
            row = conn.execute(select(cookies).where(lookup).with_for_update()).mappings().first()
            if item.get('id') and not row:
                raise ValueError('指定通道不存在')
            if row and row['lease_until'] and row['lease_until'] > utcnow():
                raise ValueError('通道正在使用，暂停采集后再更新')
            cid = row['id'] if row else str(uuid.uuid4())
            values = dict(
                name=item['name'],
                fingerprint=fingerprint,
                encrypted_value=encrypt(value),
                status='unverified',
                last_error=None,
            )
            if row:
                conn.execute(cookies.update().where(cookies.c.id == cid).values(**values))
            else:
                conn.execute(cookies.insert().values(id=cid, **values))
        try:
            fetch(value, API + 'show', {'id': '5326118891815802'})
            status, error = 'active', None
        except RemoteError as exc:
            status = (
                'expired'
                if exc.code == 'login_expired'
                else 'restricted'
                if exc.code in ('access_restricted', 'rate_limited')
                else 'unverified'
            )
            error = exc.code
        with engine.begin() as conn:
            conn.execute(
                cookies.update()
                .where(cookies.c.id == cid)
                .values(
                    status=status,
                    last_error=error,
                    last_checked_at=utcnow(),
                    request_count=cookies.c.request_count + 1,
                )
            )
        results.append(dict(id=cid, name=item['name'], status=status, error=error))
    return results


def snapshot(tid):
    with engine.connect() as conn:
        task = dict(conn.execute(select(tasks).where(tasks.c.id == tid)).mappings().one())
        task.update(task_metrics(conn, task))
        return dict(
            task=task,
            windows=[
                dict(x)
                for x in conn.execute(select(windows).where(windows.c.task_id == tid)).mappings()
            ],
            checkpoints=[
                dict(x)
                for x in conn.execute(
                    select(jobs).where(jobs.c.task_id == tid, jobs.c.status != 'done')
                ).mappings()
            ],
            attempts=[
                dict(x)
                for x in conn.execute(
                    select(attempts)
                    .where(attempts.c.task_id == tid)
                    .order_by(attempts.c.started_at)
                ).mappings()
            ],
            comment_audits=[
                dict(x)
                for x in conn.execute(select(audits).where(audits.c.task_id == tid)).mappings()
            ],
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    c = sub.add_parser('cookies')
    c.add_argument('--file', required=True)
    c = sub.add_parser('create')
    c.add_argument('--name', required=True)
    c.add_argument('--query', action='append', required=True)
    c.add_argument('--start', required=True)
    c.add_argument('--end', required=True)
    c.add_argument('--cookie', action='append', required=True)
    c.add_argument('--limit', type=int, default=30)
    for action in ('start', 'pause', 'retry', 'resume', 'cancel', 'status'):
        c = sub.add_parser(action)
        c.add_argument('task_id')
    c = sub.add_parser('worker')
    c.add_argument('--seconds', type=int, help='运行秒数；省略则持续运行至收到退出信号')
    c.add_argument('--task', action='append', help='仅处理指定任务；可重复传入')
    a = p.parse_args()
    if a.command == 'init':
        init_db()
        return
    if a.command == 'cookies':
        result = install_cookies(a.file)
    elif a.command == 'create':
        with engine.connect() as conn:
            qs = (
                conn.execute(
                    select(tables['queries']).where(tables['queries'].c.query.in_(a.query))
                )
                .mappings()
                .all()
            )
        if {r['query'] for r in qs} != set(a.query):
            p.error('检索词不在需求清单中')
        result = {
            'task_id': create_task(
                dict(
                    name=a.name,
                    start_at=a.start,
                    end_at=a.end,
                    query_ids=[r['query_id'] for r in qs],
                    cookie_ids=a.cookie,
                    request_limit=a.limit,
                    include_comments=True,
                )
            )
        }
    elif a.command == 'status':
        result = snapshot(a.task_id)
    elif a.command == 'worker':
        if a.seconds is not None and a.seconds <= 0:
            p.error('--seconds 必须大于 0')
        run_worker(a.task, a.seconds)
        result = {'worker_stopped': True, 'unfinished_checkpoints': 'preserved'}
    else:
        task_action(a.task_id, a.command)
        result = {'task_id': a.task_id, 'action': a.command}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
