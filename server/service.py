import uuid
import hashlib
from datetime import datetime, timedelta
from sqlalchemy import select, func, literal_column
from server.keywords import parse_keywords, cross_keywords
from server.db import (
    engine,
    tasks,
    cookies,
    windows,
    jobs,
    tables,
    task_posts,
    task_comments,
    audits,
    threads,
    utcnow,
    event,
    receipts,
)

HOUR_LANES = 24


def uid():
    return str(uuid.uuid4())


def enqueue(conn, task_id, kind, key, payload, priority=0):
    from sqlalchemy.dialects.mysql import insert

    if kind == 'search':
        saved = conn.execute(
            select(windows.c.current_page, windows.c.status).where(
                windows.c.id == payload['window_id']
            )
        ).first()
        if saved and (saved.current_page >= payload['page'] or saved.status == 'split'):
            return
    elif conn.execute(
        select(receipts.c.key_hash).where(
            receipts.c.task_id == task_id,
            receipts.c.key_hash == hashlib.sha256(key.encode()).hexdigest(),
        )
    ).first():
        return

    conn.execute(
        insert(jobs)
        .values(
            id=uid(),
            task_id=task_id,
            kind=kind,
            dedupe_key=key,
            payload=payload,
            status='pending',
            priority=priority,
            attempts=0,
            created_at=utcnow(),
        )
        .prefix_with('IGNORE')
    )


def day_end(start, end):
    return min(start.replace(hour=0) + timedelta(days=1), end)


def add_window(
    conn, task_id, query_id, start, end, *, adaptive=False, parent_id=None, range_end=None
):
    from sqlalchemy.dialects.mysql import insert

    if (
        (not adaptive and end - start != timedelta(hours=1))
        or end <= start
        or any(t.minute or t.second or t.microsecond for t in (start, end))
    ):
        raise ValueError('搜索窗口必须是北京时间整点起止的一个小时')
    # Stable identity makes retry/recovery unable to duplicate an hourly window.
    identity = f'{task_id}:{query_id}:{start.isoformat()}'
    if adaptive:
        identity += ':' + end.isoformat()
    wid = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
    conn.execute(
        insert(windows)
        .values(
            id=wid,
            task_id=task_id,
            query_id=query_id,
            parent_id=parent_id,
            range_end=range_end,
            start_at=start,
            end_at=end,
            status='pending',
            updated_at=utcnow(),
        )
        .prefix_with('IGNORE')
    )
    enqueue(conn, task_id, 'search', 'search:' + wid + ':1', dict(window_id=wid, page=1), 0)
    return wid


def next_hour(conn, task, window):
    """Refill this independent lane; the other 23 hours need not have finished."""
    if task.get('search_mode') == 'adaptive':
        if window.get('parent_id'):
            return
        start = window['end_at']
        boundary = window.get('range_end') or task['end_at']
        if start < boundary:
            add_window(
                conn,
                task['id'],
                window['query_id'],
                start,
                day_end(start, boundary),
                adaptive=True,
                range_end=boundary,
            )
        return
    start = window['start_at'] + timedelta(hours=HOUR_LANES)
    if start < task['end_at']:
        add_window(conn, task['id'], window['query_id'], start, start + timedelta(hours=1))


def create_task(data):
    mode = data.get('search_mode', 'hourly')
    if mode not in ('hourly', 'adaptive'):
        raise ValueError('请选择逐小时或自适应检索')
    start = (
        datetime.fromisoformat(data['start_at'])
        if data.get('start_at')
        else datetime.fromisoformat(data['start_date'])
    )
    end = (
        datetime.fromisoformat(data['end_at'])
        if data.get('end_at')
        else datetime.fromisoformat(data['end_date']) + timedelta(days=1)
    )
    if any(t.tzinfo or t.minute or t.second or t.microsecond for t in (start, end)):
        raise ValueError('请使用北京时间整点（不带时区），结束时刻不包含在范围内')
    if not datetime(1000, 1, 1) <= start < end < datetime(9999, 1, 1):
        raise ValueError('截止时间必须晚于开始时间，年份需在 1000 至 9998 之间')
    keywords = parse_keywords(data.get('keywords_text', ''), data.get('keyword_mode', 'words'))
    keywords = list(
        dict.fromkeys(
            keywords
            + cross_keywords(data.get('combination_left', ''), data.get('combination_right', ''))
        )
    )
    if len(keywords) > 1000 or any(len(q) > 200 for q in keywords):
        raise ValueError('每任务最多 1000 个检索项，单项最多 200 字符')
    with engine.begin() as conn:
        known = {r[0] for r in conn.execute(select(tables['queries'].c.query_id))}
        query_ids = list(dict.fromkeys(data.get('query_ids', [])))
        if not set(query_ids) <= known:
            raise ValueError('请选择有效检索词')
        from server.db import upsert

        by_text = {row.query: row.query_id for row in conn.execute(select(tables['queries']))}
        for keyword in keywords:
            qid = by_text.get(keyword)
            if qid is None:
                qid = 'custom_' + hashlib.sha256(keyword.encode()).hexdigest()[:48]
                upsert(
                    conn,
                    tables['queries'],
                    dict(query_id=qid, query=keyword, category='custom', terms=keyword.split()),
                )
            query_ids.append(qid)
        query_ids = list(dict.fromkeys(query_ids))
        if not query_ids or len(query_ids) > 1000:
            raise ValueError('请提供 1 至 1000 个检索词')
        cookie_ids = list(dict.fromkeys(data['cookie_ids']))
        if cookie_ids:
            valid = set(
                conn.execute(select(cookies.c.id).where(cookies.c.id.in_(cookie_ids))).scalars()
            )
            if valid != set(cookie_ids):
                raise ValueError('Cookie 配置不存在')
        tid = uid()
        conn.execute(
            tasks.insert().values(
                id=tid,
                name=data['name'],
                query_ids=query_ids,
                cookie_ids=cookie_ids,
                start_at=start,
                end_at=end,
                include_comments=data.get('include_comments', True),
                request_limit=data.get('request_limit'),
                search_mode=mode,
                request_count=0,
                active_seconds=0,
                status='draft',
            )
        )
        for qid in query_ids:
            if mode == 'adaptive':
                add_window(conn, tid, qid, start, day_end(start, end), adaptive=True, range_end=end)
                continue
            for offset in range(min(HOUR_LANES, int((end - start).total_seconds() / 3600))):
                hour = start + timedelta(hours=offset)
                add_window(conn, tid, qid, hour, hour + timedelta(hours=1))
        event(
            conn,
            'task_created',
            '任务已创建，等待启动',
            tid,
            query_count=len(query_ids),
            start_at=start.isoformat(),
            end_at=end.isoformat(),
            search_window_hours=24 if mode == 'adaptive' else 1,
            planned_query_hours=int((end - start).total_seconds() / 3600) * len(query_ids),
        )
    return tid


def stop_task(conn, task, status, reason=None):
    elapsed = task['active_seconds'] or 0
    if task['active_since']:
        elapsed += max(0, (utcnow() - task['active_since']).total_seconds())
    conn.execute(
        tasks.update()
        .where(tasks.c.id == task['id'])
        .values(
            status=status,
            active_since=None,
            active_seconds=elapsed,
            last_error=reason,
            finished_at=utcnow() if status in ('completed', 'partial', 'cancelled') else None,
        )
    )


def task_action(task_id, action):
    with engine.begin() as conn:
        task = (
            conn.execute(select(tasks).where(tasks.c.id == task_id).with_for_update())
            .mappings()
            .first()
        )
        if not task:
            raise ValueError('任务不存在')
        if action in ('start', 'resume', 'retry'):
            if task['status'] == 'running':
                return
            if task['status'] == 'completed':
                raise ValueError('任务已完成，请创建新的任务以重新采集')
            if task['status'] == 'cancelled':
                raise ValueError('已取消的任务不可恢复，请创建新任务')
            active = conn.execute(
                select(cookies.c.id).where(
                    cookies.c.id.in_(task['cookie_ids']), cookies.c.status == 'active'
                )
            ).first()
            if not active:
                raise ValueError('请先添加并验证至少一个分配给本任务的 Cookie')
            if task['request_limit'] and task['request_count'] >= task['request_limit']:
                raise ValueError('已达到请求额度，请先提高任务额度再继续')
            # Resume restores interrupted work only; content/parser failures stay failed.
            if action == 'resume':
                conn.execute(
                    jobs.update()
                    .where(jobs.c.task_id == task_id, jobs.c.status == 'blocked')
                    .values(
                        status='pending',
                        attempts=0,
                        last_error=None,
                        lease_owner=None,
                        lease_until=None,
                    )
                )
            if action == 'retry':
                from server.collector.repair import repair_daily_gaps

                repair_daily_gaps(conn, task)
                conn.execute(
                    jobs.update()
                    .where(jobs.c.task_id == task_id, jobs.c.status.in_(['blocked', 'failed']))
                    .values(
                        status='pending',
                        last_error=None,
                        attempts=0,
                        lease_owner=None,
                        lease_until=None,
                    )
                )
                conn.execute(
                    windows.update()
                    .where(windows.c.task_id == task_id, windows.c.status == 'error')
                    .values(status='pending')
                )
            conn.execute(
                tasks.update()
                .where(tasks.c.id == task_id)
                .values(status='running', active_since=utcnow(), last_error=None, finished_at=None)
            )
        elif action == 'pause':
            if task['status'] == 'running':
                stop_task(conn, task, 'paused')
        elif action == 'cancel':
            stop_task(conn, task, 'cancelled')
        else:
            raise ValueError('不支持的任务操作')
        event(
            conn,
            'task_' + action,
            {
                'start': '任务开始采集',
                'resume': '任务继续采集',
                'retry': '任务仅补抓异常作业与日期缺口，已完成数据保留',
                'pause': '任务已暂停，正在完成当前请求',
                'cancel': '任务已取消，已取得数据保留',
            }[action],
            task_id,
        )


def window_seconds(conn, tid):
    return dict(
        conn.execute(
            select(
                windows.c.status,
                func.sum(
                    func.timestampdiff(
                        literal_column('SECOND'), windows.c.start_at, windows.c.end_at
                    )
                ),
            )
            .where(windows.c.task_id == tid, windows.c.status != 'split')
            .group_by(windows.c.status)
        ).all()
    )


def task_metrics(conn, task):
    tid = task['id']
    job_counts = dict(
        conn.execute(
            select(jobs.c.status, func.count()).where(jobs.c.task_id == tid).group_by(jobs.c.status)
        ).all()
    )
    counts = dict(
        conn.execute(
            select(windows.c.status, func.count())
            .where(windows.c.task_id == tid)
            .group_by(windows.c.status)
        ).all()
    )
    total = (task['end_at'] - task['start_at']).total_seconds() * len(task['query_ids'])
    seconds = window_seconds(conn, tid)
    done = int(seconds.get('done', 0))
    executed = done + int(seconds.get('limited', 0))
    compacted = task.get('compacted_jobs', 0) or 0
    job_counts['done'] = job_counts.get('done', 0) + compacted
    posts_count = conn.scalar(
        select(func.count())
        .select_from(
            task_posts.join(tables['posts'], task_posts.c.post_id == tables['posts'].c.post_id)
        )
        .where(
            task_posts.c.task_id == tid,
            tables['posts'].c.retrieval_status.in_(['normal', 'incomplete']),
        )
    )
    comments_count = conn.scalar(
        select(func.count()).select_from(task_comments).where(task_comments.c.task_id == tid)
    )
    replies = conn.scalar(
        select(func.count())
        .select_from(
            task_comments.join(
                tables['comments'], task_comments.c.comment_id == tables['comments'].c.comment_id
            )
        )
        .where(task_comments.c.task_id == tid, tables['comments'].c.comment_level == 2)
    )
    elapsed = (task['active_seconds'] or 0) + (
        (utcnow() - task['active_since']).total_seconds() if task['active_since'] else 0
    )
    eta = None
    if task['status'] == 'running' and counts.get('done', 0) >= 3 and done > 0 and elapsed > 10:
        eta = int(max(0, total - done) * elapsed / done)
        # Date coverage alone cannot estimate unfinished comment/detail backlog at 100%.
        if done >= total and job_counts.get('pending', 0) + job_counts.get('running', 0):
            eta = None
    return dict(
        posts_count=posts_count or 0,
        comments_count=comments_count or 0,
        replies_count=replies or 0,
        pending_jobs=job_counts.get('pending', 0),
        running_jobs=job_counts.get('running', 0),
        failed_jobs=job_counts.get('failed', 0) + job_counts.get('blocked', 0),
        limited_windows=counts.get('limited', 0),
        repairable_windows=conn.scalar(
            select(func.count())
            .select_from(windows)
            .where(
                windows.c.task_id == tid,
                windows.c.status == 'limited',
                func.timestampdiff(literal_column('HOUR'), windows.c.start_at, windows.c.end_at)
                > 1,
            )
        ),
        jobs=job_counts,
        date_progress=min(99.99 if done < total else 100, round(done / total * 100, 2))
        if total
        else 0,
        processed_progress=round(executed / total * 100, 2) if total else 0,
        covered_query_days=round(done / 86400, 2),
        total_query_days=round(total / 86400, 2),
        covered_query_hours=int(done / 3600),
        total_query_hours=int(total / 3600),
        unqueued_query_hours=max(0, int((total - int(sum(seconds.values()))) / 3600)),
        search_window_hours=24 if task.get('search_mode') == 'adaptive' else 1,
        compacted_jobs=compacted,
        active_seconds=int(elapsed),
        eta_seconds=eta,
        requests_per_hour=round(task['request_count'] / elapsed * 3600, 1)
        if elapsed > 60
        else None,
    )


def list_tasks():
    with engine.connect() as conn:
        result = []
        for row in conn.execute(select(tasks).order_by(tasks.c.created_at.desc())).mappings():
            item = dict(row)
            item.update(task_metrics(conn, item))
            result.append(item)
        return result


def coverage(task_id=None):
    from collections import defaultdict

    months = defaultdict(lambda: dict(total=0, done=0, limited=0, running=0))
    with engine.connect() as conn:
        scope = select(tasks)
        if task_id:
            scope = scope.where(tasks.c.id == task_id)
        for task in conn.execute(scope).mappings():
            current = task['start_at']
            while current < task['end_at']:
                boundary = (current.replace(day=28, hour=0) + timedelta(days=4)).replace(day=1)
                stop = min(boundary, task['end_at'])
                months[current.strftime('%Y-%m')]['total'] += (
                    stop - current
                ).total_seconds() * len(task['query_ids'])
                current = stop
        query = select(windows)
        if task_id:
            query = query.where(windows.c.task_id == task_id)
        for w in conn.execute(query).mappings():
            if w['status'] == 'split':
                continue
            current = w['start_at']
            while current < w['end_at']:
                boundary = (
                    current.replace(day=28, hour=0, minute=0, second=0) + timedelta(days=4)
                ).replace(day=1)
                stop = min(boundary, w['end_at'])
                seconds = (stop - current).total_seconds()
                cell = months[current.strftime('%Y-%m')]
                if w['status'] in ('done', 'limited', 'running'):
                    cell[w['status']] += seconds
                current = stop
    return [
        dict(
            month=k,
            **v,
            progress=min(
                99.99 if v['done'] < v['total'] else 100, round(v['done'] / v['total'] * 100, 2)
            )
            if v['total']
            else 0,
        )
        for k, v in sorted(months.items())
    ]


def update_audit(conn, tid, pid):
    a = (
        conn.execute(select(audits).where(audits.c.task_id == tid, audits.c.post_id == pid))
        .mappings()
        .first()
    )
    if not a:
        return
    c = tables['comments']
    counts = dict(
        conn.execute(
            select(c.c.comment_level, func.count())
            .select_from(task_comments.join(c, task_comments.c.comment_id == c.c.comment_id))
            .where(task_comments.c.task_id == tid, c.c.post_id == pid)
            .group_by(c.c.comment_level)
        ).all()
    )
    total = sum(counts.values())
    reasons = []
    if not a['first_level_exhausted']:
        reasons.append('first_level_pending')
    for thread in conn.execute(
        select(threads).where(threads.c.task_id == tid, threads.c.post_id == pid)
    ).mappings():
        if not thread['exhausted']:
            reasons.append('replies_pending')
        if thread['last_error']:
            reasons.append(thread['last_error'])
        if thread['exhausted'] and thread['expected_count'] is not None:
            n = conn.scalar(
                select(func.count())
                .select_from(task_comments.join(c, task_comments.c.comment_id == c.c.comment_id))
                .where(
                    task_comments.c.task_id == tid,
                    c.c.root_comment_id == thread['root_id'],
                    c.c.comment_level == 2,
                )
            )
            if n != thread['expected_count']:
                reasons.append('reply_count_mismatch')
    if a['displayed_comments_count'] is None or a['displayed_comments_count'] != total:
        reasons.append('displayed_count_mismatch')
    errors = conn.execute(
        select(jobs.c.last_error).where(
            jobs.c.task_id == tid,
            jobs.c.kind.in_(['comments', 'replies']),
            jobs.c.status.in_(['failed', 'blocked']),
            jobs.c.payload['post_id'].as_string() == pid,
        )
    ).scalars()
    reasons.extend(error for error in errors if error)
    conn.execute(
        audits.update()
        .where(audits.c.task_id == tid, audits.c.post_id == pid)
        .values(
            retrieved_comments_count=total,
            retrieved_first_level_count=counts.get(1, 0),
            retrieved_reply_count=counts.get(2, 0),
            comments_complete=not reasons,
            incomplete_reason=sorted(set(reasons)),
            updated_at=utcnow(),
        )
    )
