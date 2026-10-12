"""Requeue only recorded failures and split unresolved day-sized gaps."""

from datetime import timedelta
from sqlalchemy import select
from server.db import windows
from server.service import add_window


def repair_daily_gaps(conn, task):
    count = 0
    for window in (
        conn.execute(
            select(windows).where(windows.c.task_id == task['id'], windows.c.status == 'limited')
        )
        .mappings()
        .all()
    ):
        hours = int((window['end_at'] - window['start_at']).total_seconds() / 3600)
        if hours <= 1:
            continue
        conn.execute(windows.update().where(windows.c.id == window['id']).values(status='split'))
        for offset in range(hours):
            start = window['start_at'] + timedelta(hours=offset)
            add_window(
                conn,
                task['id'],
                window['query_id'],
                start,
                start + timedelta(hours=1),
                adaptive=True,
                parent_id=window['id'],
            )
        count += hours
    return count
