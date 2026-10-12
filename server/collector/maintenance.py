"""Bounded transactional queue compaction; failures and live checkpoints remain."""

import hashlib
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from server.db import engine, tasks, jobs, attempts, receipts


def compact_task(conn, tid, keep=100, batch=500):
    # Same task lock as enqueue/ACK prevents replay racing deletion and receipts.
    conn.execute(select(tasks.c.id).where(tasks.c.id == tid).with_for_update()).one()
    rows = (
        conn.execute(
            select(jobs)
            .where(
                jobs.c.task_id == tid,
                jobs.c.status == 'done',
                ~select(attempts.c.id).where(attempts.c.job_id == jobs.c.id).exists(),
            )
            .order_by(jobs.c.finished_at.desc(), jobs.c.id)
            .offset(keep)
            .limit(batch)
        )
        .mappings()
        .all()
    )
    if rows:
        keys = [
            dict(task_id=tid, key_hash=hashlib.sha256(row['dedupe_key'].encode()).hexdigest())
            for row in rows
            if row['kind'] != 'search'
        ]
        if keys:
            conn.execute(insert(receipts).values(keys).prefix_with('IGNORE'))
        # Successful search pages are represented by search_windows.current_page.
        conn.execute(
            jobs.delete().where(jobs.c.id.in_([row['id'] for row in rows]), jobs.c.status == 'done')
        )
        conn.execute(
            tasks.update()
            .where(tasks.c.id == tid)
            .values(compacted_jobs=tasks.c.compacted_jobs + len(rows))
        )
    return len(rows)


def compact_once(task_ids=None):
    with engine.connect() as conn:
        query = select(tasks.c.id)
        if task_ids is not None:
            query = query.where(tasks.c.id.in_(task_ids))
        ids = conn.execute(query).scalars().all()
    total = 0
    for tid in ids:
        with engine.begin() as conn:
            total += compact_task(conn, tid)
    return total
