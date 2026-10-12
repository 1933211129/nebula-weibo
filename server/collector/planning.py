"""Convert only unstarted hourly work; preserve committed/failed checkpoints."""

from collections import defaultdict
from sqlalchemy import select
from server.db import tasks, windows, jobs, attempts
from server.service import add_window, day_end


def enable_adaptive(conn, tid):
    task = conn.execute(select(tasks).where(tasks.c.id == tid).with_for_update()).mappings().one()
    if task['status'] not in ('draft', 'paused', 'needs_attention'):
        raise ValueError('请暂停任务后再切换检索策略')
    if task['search_mode'] == 'adaptive':
        return
    if conn.execute(
        select(jobs.c.id).where(jobs.c.task_id == tid, jobs.c.status == 'running').limit(1)
    ).first():
        raise ValueError('仍有在途请求，请稍候再切换')
    by_window = defaultdict(list)
    for row in conn.execute(
        select(jobs).where(jobs.c.task_id == tid, jobs.c.kind == 'search')
    ).mappings():
        by_window[row['payload']['window_id']].append(row)
    attempted = set(
        conn.execute(select(attempts.c.job_id).where(attempts.c.task_id == tid)).scalars()
    )
    for qid in task['query_ids']:
        protected = []
        rows = (
            conn.execute(
                select(windows)
                .where(windows.c.task_id == tid, windows.c.query_id == qid)
                .order_by(windows.c.start_at)
            )
            .mappings()
            .all()
        )
        for window in rows:
            queued = by_window[window['id']]
            untouched = (
                window['status'] == 'pending'
                and window['current_page'] == 0
                and all(
                    r['status'] == 'pending' and r['attempts'] == 0 and r['id'] not in attempted
                    for r in queued
                )
            )
            if untouched:
                if queued:
                    conn.execute(jobs.delete().where(jobs.c.id.in_([r['id'] for r in queued])))
                conn.execute(windows.delete().where(windows.c.id == window['id']))
            else:
                protected.append((window['start_at'], window['end_at']))
                conn.execute(
                    windows.update()
                    .where(windows.c.id == window['id'])
                    .values(parent_id='legacy-hourly')
                )
        cursor = task['start_at']
        gaps = []
        for start, end in protected:
            if cursor < start:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < task['end_at']:
            gaps.append((cursor, task['end_at']))
        for start, end in gaps:
            add_window(conn, tid, qid, start, day_end(start, end), adaptive=True, range_end=end)
    conn.execute(tasks.update().where(tasks.c.id == tid).values(search_mode='adaptive'))
