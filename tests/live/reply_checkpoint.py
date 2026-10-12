"""Bounded real reply pagination with a simulated expiry between the two pages."""

import argparse
import json
import time
from pathlib import Path
from sqlalchemy import select, func
from server.db import engine, cookies, tables, threads, audits, task_posts, event, upsert
from server.service import create_task, task_action, enqueue
from server.worker import WorkerPool, RemoteError, fetch, API
from server.security import decrypt
from server.cli import snapshot


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cookie', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    with engine.connect() as c:
        qid = c.scalar(select(tables['queries'].c.query_id).limit(1))
    tid = create_task(
        dict(
            name='定点回复分页与游标恢复实测（非关键词样本）',
            start_at='2025-01-30T19:00:00',
            end_at='2025-01-30T20:00:00',
            query_ids=[qid],
            cookie_ids=[a.cookie],
            include_comments=False,
            request_limit=8,
        )
    )
    pid, root, user = '5128599886106312', '5128602473727532', '1454352573'
    with engine.begin() as c:
        before_count = c.scalar(
            select(func.count())
            .select_from(tables['comments'])
            .where(tables['comments'].c.root_comment_id == root)
        )
        enqueue(c, tid, 'post', 'post:' + pid, dict(post_id=pid), 200)
        upsert(c, task_posts, dict(task_id=tid, post_id=pid))
        enqueue(
            c,
            tid,
            'replies',
            'replies:' + root + ':0',
            dict(post_id=pid, root_id=root, user_id=user, cursor=0),
            100,
        )
        upsert(
            c,
            threads,
            dict(task_id=tid, root_id=root, post_id=pid, expected_count=None, exhausted=False),
        )
    task_action(tid, 'start')
    pool = WorkerPool(task_ids=[tid])

    def claim():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            item = pool.claim(a.cookie)
            if item:
                return item
            time.sleep(0.25)
        raise RuntimeError('未取得请求，请检查任务状态')

    try:
        detail = claim()
        assert detail[0]['kind'] == 'post' and pool.execute(*detail)
        with engine.begin() as c:
            displayed = c.scalar(
                select(tables['posts'].c.comments_count).where(tables['posts'].c.post_id == pid)
            )
            upsert(
                c,
                audits,
                dict(
                    task_id=tid,
                    post_id=pid,
                    displayed_comments_count=displayed,
                    first_level_exhausted=False,
                    comments_complete=False,
                    incomplete_reason=['targeted_reply_test_only'],
                ),
            )
        first = claim()
        assert first[0]['kind'] == 'replies'
        assert pool.execute(*first)
        second = claim()
        assert second[0]['kind'] == 'replies', '当前样本没有第二页，请检查报告'
        checkpoint = second[0]['payload']
        with engine.begin() as c:
            event(
                c, 'fault_injection', '模拟回复第二页登录失效', tid, a.cookie, checkpoint=checkpoint
            )
        pool.fail(second[0], a.cookie, RemoteError('login_expired', 200))
        interrupted = snapshot(tid)
        fetch(decrypt(second[1]['encrypted_value']), API + 'show', {'id': pid})
        with engine.begin() as c:
            c.execute(
                cookies.update()
                .where(cookies.c.id == a.cookie)
                .values(status='active', last_error=None)
            )
        task_action(tid, 'retry')
        restored = claim()
        assert restored[0]['id'] == second[0]['id'] and restored[0]['payload'] == checkpoint
        assert pool.execute(*restored)
    finally:
        task_action(tid, 'pause')
    report = dict(
        task_id=tid,
        simulated_interruption=interrupted,
        restored_job_id=restored[0]['id'],
        restored_checkpoint=checkpoint,
        final=snapshot(tid),
    )
    with engine.connect() as c:
        after_count = c.scalar(
            select(func.count())
            .select_from(tables['comments'])
            .where(tables['comments'].c.root_comment_id == root)
        )
    report['global_reply_rows_before'] = before_count
    report['global_reply_rows_after'] = after_count
    path = Path(a.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(
        json.dumps(
            dict(
                task_id=tid,
                replies=report['final']['task']['replies_count'],
                restored_checkpoint=checkpoint,
                report=str(path),
            ),
            ensure_ascii=False,
        )
    )


if __name__ == '__main__':
    main()
