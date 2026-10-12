"""Search by day; split capped days directly into exact one-hour children."""

from datetime import timedelta
from server.db import windows
from server.service import add_window, next_hour


def split_dense_window(conn, task, window, parsed, page):
    hours = int((window['end_at'] - window['start_at']).total_seconds() / 3600)
    if task.get('search_mode') != 'adaptive' or hours <= 1:
        return False
    # Prefer the site's explicit count, never estimate it from page count.
    dense = (
        parsed.total_results >= 500
        if parsed.total_results is not None
        else max(parsed.pagination_pages or [1]) >= 50 or page >= 50
    )
    # Even a stale sub-cap summary must not suppress splitting a capped day.
    dense = dense or (page >= 50 and parsed.has_next)
    # A next link alone is not a trustworthy total-page count. Fail conservative.
    unknown = (
        page == 1
        and parsed.has_next
        and not parsed.pagination_pages
        and parsed.total_results is None
    )
    dense = dense or unknown
    if not dense:
        return False
    conn.execute(
        windows.update()
        .where(windows.c.id == window['id'])
        .values(status='split', current_page=page)
    )
    for offset in range(hours):
        start = window['start_at'] + timedelta(hours=offset)
        end = start + timedelta(hours=1)
        add_window(
            conn, task['id'], window['query_id'], start, end, adaptive=True, parent_id=window['id']
        )
    next_hour(conn, task, window)
    return True
