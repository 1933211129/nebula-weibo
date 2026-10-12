"""Persist compact per-task AND-match evidence, never trust search snippets."""

from datetime import datetime
from sqlalchemy import select
from server.db import keyword_checks, tables, task_posts, upsert, utcnow
from server.keywords import missing_terms
from research_models import now


def verify_combinations(conn, task, record):
    pid = record['post_id']
    checks = (
        conn.execute(
            select(keyword_checks).where(
                keyword_checks.c.task_id == task['id'], keyword_checks.c.post_id == pid
            )
        )
        .mappings()
        .all()
    )
    if not checks:
        return
    complete = bool(record.get('created_at')) and record.get('retrieval_status') in (
        'normal',
        'keyword_unmatched',
    )
    created = (
        datetime.fromisoformat(record['created_at']).replace(tzinfo=None)
        if record.get('created_at')
        else None
    )
    for check in checks:
        if check['status'] == 'search_match':
            continue
        query = (
            conn.execute(
                select(tables['queries']).where(tables['queries'].c.query_id == check['query_id'])
            )
            .mappings()
            .one()
        )
        missing = missing_terms(record.get('post_text'), query['terms']) if complete else None
        status = (
            'outside_time_range'
            if created and not task['start_at'] <= created < task['end_at']
            else 'pending'
            if not complete
            else 'rejected'
            if missing
            else 'verified'
        )
        conn.execute(
            keyword_checks.update()
            .where(
                keyword_checks.c.task_id == task['id'],
                keyword_checks.c.post_id == pid,
                keyword_checks.c.query_id == check['query_id'],
            )
            .values(
                status=status,
                missing_terms=missing,
                checked_at=utcnow() if status != 'pending' else None,
            )
        )
        if status == 'verified':
            upsert(conn, task_posts, dict(task_id=task['id'], post_id=pid))
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
        elif status != 'pending':
            still_verified = conn.execute(
                select(keyword_checks.c.task_id)
                .where(
                    keyword_checks.c.post_id == pid,
                    keyword_checks.c.query_id == query['query_id'],
                    keyword_checks.c.status == 'verified',
                )
                .limit(1)
            ).first()
            if not still_verified:
                conn.execute(
                    tables['post_query_matches']
                    .delete()
                    .where(
                        tables['post_query_matches'].c.post_id == pid,
                        tables['post_query_matches'].c.query_id == query['query_id'],
                    )
                )
    valid = conn.execute(
        select(keyword_checks.c.query_id).where(
            keyword_checks.c.task_id == task['id'],
            keyword_checks.c.post_id == pid,
            keyword_checks.c.status.in_(['verified', 'search_match']),
        )
    ).first()
    if not valid:
        conn.execute(
            task_posts.delete().where(
                task_posts.c.task_id == task['id'], task_posts.c.post_id == pid
            )
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
    record['query_id'] = [m['query_id'] for m in matches]
    record['matched_keyword'] = sorted({m['matched_keyword'] for m in matches})
    if complete:
        accepted = conn.execute(
            select(task_posts.c.task_id).where(task_posts.c.post_id == pid).limit(1)
        ).first()
        record['retrieval_status'] = 'normal' if accepted else 'keyword_unmatched'
