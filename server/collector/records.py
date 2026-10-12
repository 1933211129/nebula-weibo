"""Transactional parsing and scheduling of follow-up pages."""

from datetime import datetime, timedelta
from sqlalchemy import select
from server.db import (
    tasks,
    windows,
    jobs,
    tables,
    task_posts,
    task_comments,
    audits,
    threads,
    utcnow,
    upsert,
    event,
    keyword_checks,
)
from server.collector.verification import verify_combinations
from server.service import enqueue, next_hour, update_audit
from server.collector.transport import API, RemoteError
from research_crawler import SearchParser
from research_models import post_record, comment_record, user_record, text_fields, now


def endpoint_for(job, conn):
    p = job['payload']
    if job['kind'] == 'search':
        w = conn.execute(select(windows).where(windows.c.id == p['window_id'])).mappings().one()
        mode = conn.scalar(select(tasks.c.search_mode).where(tasks.c.id == job['task_id']))
        duration = w['end_at'] - w['start_at']
        if duration <= timedelta(0) or (mode != 'adaptive' and duration != timedelta(hours=1)):
            raise RemoteError('invalid_hourly_window')
        q = (
            conn.execute(
                select(tables['queries']).where(tables['queries'].c.query_id == w['query_id'])
            )
            .mappings()
            .one()
        )
        return 'https://s.weibo.com/weibo', dict(
            q=q['query'],
            timescope='custom:{}:{}'.format(
                w['start_at'].strftime('%Y-%m-%d-%H'), w['end_at'].strftime('%Y-%m-%d-%H')
            ),
            page=p['page'],
        )
    if job['kind'] == 'post':
        return API + 'show', {'id': p['post_id']}
    if job['kind'] == 'longtext':
        return API + 'longtext', {'id': p.get('mblogid') or p['post_id']}
    if job['kind'] == 'longtext_check':
        return API + 'show', {'id': p['post_id']}
    params = dict(
        is_reload=1,
        id=p.get('root_id') or p['post_id'],
        uid=p['user_id'],
        is_show_bulletin=2,
        is_mix=1 if job['kind'] == 'replies' else 0,
        count=20,
        flow=1,
        max_id=p.get('cursor', 0),
    )
    if job['kind'] == 'replies':
        params['fetch_level'] = 1
    if p.get('cursor_type') is not None:
        params['max_id_type'] = p['cursor_type']
    return API + 'buildComments', params


def save_comment(conn, tid, raw, pid, root=None, root_user=None):
    record = comment_record(raw, pid, root, root_user)
    upsert(conn, tables['comments'], record)
    upsert(conn, tables['users'], user_record(raw.get('user')))
    upsert(conn, task_comments, dict(task_id=tid, comment_id=record['comment_id']))
    return record


def process_result(conn, job, result):
    tid, kind, p = job['task_id'], job['kind'], job['payload']
    task = conn.execute(select(tasks).where(tasks.c.id == tid)).mappings().one()
    if kind == 'search':
        w = (
            conn.execute(select(windows).where(windows.c.id == p['window_id']).with_for_update())
            .mappings()
            .one()
        )
        parsed = SearchParser()
        parsed.feed(result)
        if not parsed.ids and '未找到相关结果' not in result:
            raise RemoteError('unrecognized_search_page')
        from server.collector.adaptive import split_dense_window

        if split_dense_window(conn, task, w, parsed, p['page']):
            return
        query = (
            conn.execute(
                select(tables['queries']).where(tables['queries'].c.query_id == w['query_id'])
            )
            .mappings()
            .one()
        )
        for pid in parsed.ids:
            from sqlalchemy.dialects.mysql import insert

            conn.execute(
                insert(tables['posts'])
                .values(post_id=pid, retrieval_status='not_fetched')
                .prefix_with('IGNORE')
            )
            # Serialize cross-keyword matches so the denormalized export lists stay complete.
            conn.execute(
                select(tables['posts'].c.post_id)
                .where(tables['posts'].c.post_id == pid)
                .with_for_update()
            ).one()
            if len(query.get('terms') or []) > 1:
                conn.execute(
                    insert(keyword_checks)
                    .values(task_id=tid, post_id=pid, query_id=query['query_id'], status='pending')
                    .prefix_with('IGNORE')
                )
                cached = dict(
                    conn.execute(select(tables['posts']).where(tables['posts'].c.post_id == pid))
                    .mappings()
                    .one()
                )
                verify_combinations(conn, task, cached)
                upsert(conn, tables['posts'], cached)
                if cached.get('retrieval_status') in ('normal', 'keyword_unmatched'):
                    schedule_comments(conn, task, cached)
                enqueue(conn, tid, 'post', 'post:' + pid, dict(post_id=pid), 10)
                continue
            conn.execute(
                insert(keyword_checks)
                .values(task_id=tid, post_id=pid, query_id=query['query_id'], status='search_match')
                .prefix_with('IGNORE')
            )
            upsert(conn, task_posts, dict(task_id=tid, post_id=pid))
            upsert(
                conn,
                tables['post_query_matches'],
                dict(
                    post_id=pid,
                    query_id=query['query_id'],
                    matched_keyword=query['query'],
                    retrieved_at=now(),
                ),
            )
            matches = (
                conn.execute(
                    select(tables['post_query_matches']).where(
                        tables['post_query_matches'].c.post_id == pid
                    )
                )
                .mappings()
                .all()
            )
            conn.execute(
                tables['posts']
                .update()
                .where(tables['posts'].c.post_id == pid)
                .values(
                    query_id=[m['query_id'] for m in matches],
                    matched_keyword=sorted({m['matched_keyword'] for m in matches}),
                )
            )
            enqueue(conn, tid, 'post', 'post:' + pid, dict(post_id=pid), 10)
        # A known sub-500 count and an explicit last page are not a cap.
        natural_end = (
            not parsed.has_next and parsed.total_results is not None and parsed.total_results < 500
        )
        limited = not natural_end and (
            p['page'] >= 50 or (not parsed.has_next and max(parsed.page_numbers or [1]) >= 50)
        )
        if parsed.has_next and not limited:
            enqueue(
                conn,
                tid,
                'search',
                'search:' + w['id'] + ':' + str(p['page'] + 1),
                dict(window_id=w['id'], page=p['page'] + 1),
                0,
            )
        status = 'limited' if limited else 'running' if parsed.has_next else 'done'
        conn.execute(
            windows.update()
            .where(windows.c.id == w['id'])
            .values(status=status, current_page=p['page'], updated_at=utcnow())
        )
        if limited:
            event(
                conn,
                'search_cap',
                '检索窗口触及分页上限，保留缺口',
                tid,
                job['cookie_id'],
                level='warning',
                window_id=w['id'],
            )
        if status in ('done', 'limited'):
            next_hour(conn, task, w)
        return
    if kind == 'post':
        record = post_record(result)
        conn.execute(
            select(tables['posts'].c.post_id)
            .where(tables['posts'].c.post_id == record['post_id'])
            .with_for_update()
        ).first()
        created = datetime.fromisoformat(record['created_at']).replace(tzinfo=None)
        if not task['start_at'] <= created < task['end_at']:
            conn.execute(
                keyword_checks.update()
                .where(
                    keyword_checks.c.task_id == tid, keyword_checks.c.post_id == record['post_id']
                )
                .values(status='outside_time_range', checked_at=utcnow())
            )
            # A boundary discovery belongs to the audit trail, not this task's corpus.
            conn.execute(
                task_posts.delete().where(
                    task_posts.c.task_id == tid, task_posts.c.post_id == record['post_id']
                )
            )
            existing = (
                conn.execute(
                    select(tables['posts']).where(tables['posts'].c.post_id == record['post_id'])
                )
                .mappings()
                .first()
            )
            if not existing or existing['retrieval_status'] == 'not_fetched':
                upsert(
                    conn,
                    tables['posts'],
                    dict(
                        post_id=record['post_id'],
                        created_at=record['created_at'],
                        retrieval_status='outside_time_range',
                    ),
                )
            return
        matches = (
            conn.execute(
                select(tables['post_query_matches']).where(
                    tables['post_query_matches'].c.post_id == record['post_id']
                )
            )
            .mappings()
            .all()
        )
        record['query_id'] = [r['query_id'] for r in matches]
        record['matched_keyword'] = sorted({r['matched_keyword'] for r in matches})
        if result.get('isLongText'):
            record['retrieval_status'] = 'incomplete'
            enqueue(
                conn,
                tid,
                'longtext',
                'longtext:' + record['post_id'],
                dict(post_id=record['post_id'], mblogid=result.get('mblogid')),
                30,
            )
        verify_combinations(conn, task, record)
        upsert(conn, tables['posts'], record)
        upsert(conn, tables['users'], user_record(result.get('user')))
        # Accepted posts can collect comments independently of full-text recovery.
        schedule_comments(conn, task, record)
        return
    if kind in ('longtext', 'longtext_check'):
        from server.collector.semantics import confirmed_full_text

        full = (
            (confirmed_full_text(result) if str(result.get('id')) == p['post_id'] else None)
            if kind == 'longtext_check'
            else (result.get('data') or {}).get('longTextContent')
        )
        if not isinstance(full, str) or not full:
            if kind == 'longtext_check':
                raise RemoteError('longtext_unconfirmed')
            enqueue(
                conn,
                tid,
                'longtext_check',
                'longtext_check:' + p['post_id'],
                dict(post_id=p['post_id']),
                30,
            )
            record = dict(
                conn.execute(
                    select(tables['posts']).where(tables['posts'].c.post_id == p['post_id'])
                )
                .mappings()
                .one()
            )
            schedule_comments(conn, task, record)
            return
        record = dict(
            conn.execute(select(tables['posts']).where(tables['posts'].c.post_id == p['post_id']))
            .mappings()
            .one()
        )
        record.update(post_text=full, retrieval_status='normal', retrieved_at=now())
        if record['is_repost']:
            record['repost_text'] = full
        fields = text_fields(result if kind == 'longtext_check' else result.get('data') or {}, full)
        for k, v in fields.items():
            record[k] = sorted(set(record.get(k) or []) | set(v))
        verify_combinations(conn, task, record)
        upsert(conn, tables['posts'], record)
        schedule_comments(conn, task, record)
        return
    if not isinstance(result.get('data'), list):
        raise RemoteError('invalid_comment_page')
    from server.db import upsert_many

    comments, users = [], []
    for raw in result['data']:
        record = comment_record(
            raw, p['post_id'], p.get('root_id'), p['user_id'] if kind == 'replies' else None
        )
        comments.append(record)
        users.append(user_record(raw.get('user')))
        if kind == 'comments':
            for child in raw.get('comments') or []:
                comments.append(
                    comment_record(
                        child, p['post_id'], record['comment_id'], record['comment_user_id']
                    )
                )
                users.append(user_record(child.get('user')))
    upsert_many(conn, tables['comments'], comments)
    upsert_many(conn, tables['users'], users)
    upsert_many(
        conn, task_comments, [dict(task_id=tid, comment_id=r['comment_id']) for r in comments]
    )
    for raw in result['data']:
        record = comment_record(
            raw, p['post_id'], p.get('root_id'), p['user_id'] if kind == 'replies' else None
        )
        if kind == 'comments':
            inline = raw.get('comments') or []
            if raw.get('total_number', 0) or raw.get('more_info') or inline:
                upsert(
                    conn,
                    threads,
                    dict(
                        task_id=tid,
                        root_id=record['comment_id'],
                        post_id=p['post_id'],
                        expected_count=raw.get('total_number'),
                        exhausted=False,
                    ),
                )
                enqueue(
                    conn,
                    tid,
                    'replies',
                    'replies:' + record['comment_id'] + ':0',
                    dict(
                        post_id=p['post_id'],
                        root_id=record['comment_id'],
                        user_id=record['comment_user_id'],
                        cursor=0,
                    ),
                    25,
                )
    from server.collector.semantics import comment_cursor

    cursor = comment_cursor(result)
    if cursor is None:
        raise RemoteError('missing_comment_cursor')
    key_base = kind + ':' + (p.get('root_id') or p['post_id']) + ':'
    if str(cursor) != '0':
        previous = conn.execute(
            select(jobs.c.id).where(
                jobs.c.task_id == tid, jobs.c.dedupe_key == key_base + str(cursor)
            )
        ).first()
        if previous or not result['data']:
            raise RemoteError('stalled_comment_cursor')
        enqueue(
            conn,
            tid,
            kind,
            key_base + str(cursor),
            dict(p, cursor=cursor, cursor_type=result.get('max_id_type')),
            job['priority'],
        )
    elif kind == 'comments':
        conn.execute(
            audits.update()
            .where(audits.c.task_id == tid, audits.c.post_id == p['post_id'])
            .values(first_level_exhausted=True)
        )
    else:
        conn.execute(
            threads.update()
            .where(threads.c.task_id == tid, threads.c.root_id == p['root_id'])
            .values(exhausted=True, last_error=None)
        )
    update_audit(conn, tid, p['post_id'])


def schedule_comments(conn, task, record):
    if (
        not task['include_comments']
        or not conn.execute(
            select(task_posts.c.post_id).where(
                task_posts.c.task_id == task['id'], task_posts.c.post_id == record['post_id']
            )
        ).first()
    ):
        return
    from sqlalchemy.dialects.mysql import insert

    conn.execute(
        insert(audits)
        .values(
            task_id=task['id'],
            post_id=record['post_id'],
            displayed_comments_count=record['comments_count'],
            first_level_exhausted=False,
            comments_complete=False,
            incomplete_reason=['pending'],
        )
        .prefix_with('IGNORE')
    )
    enqueue(
        conn,
        task['id'],
        'comments',
        'comments:' + record['post_id'] + ':0',
        dict(post_id=record['post_id'], user_id=record['user_id'], cursor=0),
        20,
    )
