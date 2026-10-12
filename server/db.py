"""MySQL schema and transactional persistence for the research service."""

import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    String,
    Text,
    Integer,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    JSON,
    Index,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import insert as mysql_insert, LONGTEXT, DATETIME

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env.local')
sys.path.insert(0, str(ROOT / 'weibospider'))
from research_store import FIELDS, NUMERIC

engine = create_engine(
    os.environ['DATABASE_URL'],
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=12,
    max_overflow=12,
    connect_args={'connect_timeout': 10},
)
meta = MetaData()


def utcnow():
    return datetime.utcnow()


cookies = Table(
    'cookie_accounts',
    meta,
    Column('id', String(36), primary_key=True),
    Column('name', String(100), nullable=False),
    Column('encrypted_value', Text, nullable=False),
    Column('fingerprint', String(64), unique=True, nullable=False),
    Column('status', String(24), nullable=False, default='unverified'),
    Column('request_interval', Float, nullable=False, default=3),
    Column('request_interval_min', Float, nullable=False, server_default='1'),
    Column('request_interval_max', Float, nullable=False, server_default='2.5'),
    Column('failure_limit', Integer, nullable=False, default=5, server_default='5'),
    Column('consecutive_failures', Integer, nullable=False, default=0, server_default='0'),
    Column('request_count', BigInteger, nullable=False, default=0),
    Column('last_checked_at', DateTime),
    Column('last_error', String(200)),
    Column('lease_owner', String(64)),
    Column('lease_until', DateTime),
    Column('next_request_at', DATETIME(fsp=3)),
    Column('created_at', DateTime, default=utcnow, nullable=False),
)

tasks = Table(
    'tasks',
    meta,
    Column('id', String(36), primary_key=True),
    Column('name', String(160), nullable=False),
    Column('status', String(24), default='draft', nullable=False),
    Column('start_at', DateTime, nullable=False),
    Column('end_at', DateTime, nullable=False),
    Column('query_ids', JSON, nullable=False),
    Column('cookie_ids', JSON, nullable=False),
    Column('include_comments', Boolean, default=True, nullable=False),
    Column('request_limit', BigInteger),
    Column('search_mode', String(24), nullable=False, server_default='hourly'),
    Column('compacted_jobs', BigInteger, nullable=False, server_default='0'),
    Column('request_count', BigInteger, default=0, nullable=False),
    Column('active_seconds', Float, default=0, nullable=False),
    Column('active_since', DateTime),
    Column('created_at', DateTime, default=utcnow, nullable=False),
    Column('finished_at', DateTime),
    Column('last_error', String(200)),
)

windows = Table(
    'search_windows',
    meta,
    Column('id', String(36), primary_key=True),
    Column('task_id', String(36), ForeignKey('tasks.id'), nullable=False),
    Column('query_id', String(64), nullable=False),
    Column('parent_id', String(36)),
    Column('range_end', DateTime),
    Column('start_at', DateTime, nullable=False),
    Column('end_at', DateTime, nullable=False),
    Column('status', String(24), default='pending', nullable=False),
    Column('current_page', Integer, default=0),
    Column('updated_at', DateTime, default=utcnow),
    Index('ix_windows_task', 'task_id', 'status'),
)

jobs = Table(
    'jobs',
    meta,
    Column('id', String(36), primary_key=True),
    Column('task_id', String(36), ForeignKey('tasks.id'), nullable=False),
    Column('kind', String(24), nullable=False),
    Column('dedupe_key', String(240), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('status', String(24), default='pending', nullable=False),
    Column('priority', Integer, default=0, nullable=False),
    Column('attempts', Integer, default=0, nullable=False),
    Column('cookie_id', String(36)),
    Column('lease_owner', String(64)),
    Column('lease_until', DateTime),
    Column('last_error', String(200)),
    Column('created_at', DateTime, default=utcnow, nullable=False),
    Column('finished_at', DateTime),
    UniqueConstraint('task_id', 'dedupe_key', name='uq_task_job'),
    Index('ix_jobs_queue', 'task_id', 'status', 'priority'),
    Index('ix_jobs_lease', 'lease_until'),
)

logs = Table(
    'crawl_logs',
    meta,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    Column('task_id', String(36)),
    Column('cookie_id', String(36)),
    Column('level', String(16), nullable=False, default='info'),
    Column('event', String(64), nullable=False),
    Column('message', String(240), nullable=False),
    Column('details', JSON),
    Column('created_at', DateTime, default=utcnow, nullable=False),
    Index('ix_logs_task_time', 'task_id', 'created_at'),
)

receipts = Table(
    'job_receipts',
    meta,
    Column('task_id', String(36), ForeignKey('tasks.id'), primary_key=True),
    Column('key_hash', String(64), primary_key=True),
)

attempts = Table(
    'job_attempts',
    meta,
    Column('id', String(36), primary_key=True),
    Column('job_id', String(36), ForeignKey('jobs.id'), nullable=False),
    Column('task_id', String(36), nullable=False),
    Column('cookie_id', String(36), nullable=False),
    Column('checkpoint', JSON, nullable=False),
    Column('kind', String(24), nullable=False),
    Column('started_at', DATETIME(fsp=6), nullable=False),
    Column('finished_at', DATETIME(fsp=6)),
    Column('outcome', String(40), nullable=False, default='running'),
    Column('http_status', Integer),
    Index('ix_attempts_task', 'task_id', 'started_at'),
)

tables = {}
for name in ('posts', 'comments', 'users', 'post_query_matches', 'queries'):
    names = FIELDS[name].split()
    columns = []
    for field in names:
        primary = field == names[0] or (name == 'post_query_matches' and field == 'query_id')
        typ = String(64) if primary or field.endswith('_id') else Text
        if field in NUMERIC:
            typ = BigInteger
        if field in ('post_text', 'comment_text', 'repost_text', 'user_description'):
            typ = LONGTEXT
        if field in ('topics', 'mentions', 'urls', 'terms') or (
            name == 'posts' and field in ('query_id', 'matched_keyword')
        ):
            typ = JSON
        if field in (
            'created_at',
            'retrieved_at',
            'profile_retrieved_at',
            'retrieval_status',
            'year_month',
            'media_type',
        ):
            typ = String(48)
        columns.append(Column(field, typ, primary_key=primary, nullable=not primary))
    tables[name] = Table(name, meta, *columns)
Index('ix_posts_date', tables['posts'].c.created_at)
Index('ix_posts_month', tables['posts'].c.year_month)
Index('ix_comments_post', tables['comments'].c.post_id)
Index('ix_comments_root', tables['comments'].c.root_comment_id)

task_posts = Table(
    'task_posts',
    meta,
    Column('task_id', String(36), ForeignKey('tasks.id'), primary_key=True),
    Column('post_id', String(64), primary_key=True),
    Column('created_at', DateTime, default=utcnow),
)
task_comments = Table(
    'task_comments',
    meta,
    Column('task_id', String(36), ForeignKey('tasks.id'), primary_key=True),
    Column('comment_id', String(64), primary_key=True),
    Column('created_at', DateTime, default=utcnow),
)

audits = Table(
    'comment_audits',
    meta,
    Column('task_id', String(36), ForeignKey('tasks.id'), primary_key=True),
    Column('post_id', String(64), primary_key=True),
    Column('displayed_comments_count', BigInteger),
    Column('retrieved_comments_count', BigInteger, default=0),
    Column('retrieved_first_level_count', BigInteger, default=0),
    Column('retrieved_reply_count', BigInteger, default=0),
    Column('first_level_exhausted', Boolean, default=False),
    Column('comments_complete', Boolean, default=False),
    Column('incomplete_reason', JSON),
    Column('updated_at', DateTime, default=utcnow),
)
threads = Table(
    'comment_threads',
    meta,
    Column('task_id', String(36), ForeignKey('tasks.id'), primary_key=True),
    Column('root_id', String(64), primary_key=True),
    Column('post_id', String(64), nullable=False),
    Column('expected_count', BigInteger),
    Column('exhausted', Boolean, default=False),
    Column('last_error', String(200)),
)


keyword_checks = Table(
    'keyword_checks',
    meta,
    Column('task_id', String(36), ForeignKey('tasks.id'), primary_key=True),
    Column('post_id', String(64), primary_key=True),
    Column('query_id', String(64), primary_key=True),
    Column('status', String(32), nullable=False, default='pending'),
    Column('missing_terms', JSON),
    Column('checked_at', DateTime),
)


def upsert_many(conn, table, rows):
    """Batch compatible non-null updates without erasing known values."""
    from collections import defaultdict

    groups = defaultdict(list)
    for row in rows:
        if row:
            values = {k: v for k, v in row.items() if k in table.c and v is not None}
            groups[tuple(sorted(values))].append(values)
    for keys, values in groups.items():
        for offset in range(0, len(values), 200):
            stmt = mysql_insert(table).values(values[offset : offset + 200])
            updates = {k: stmt.inserted[k] for k in keys if not table.c[k].primary_key}
            conn.execute(
                stmt.on_duplicate_key_update(**updates) if updates else stmt.prefix_with('IGNORE')
            )


def upsert(conn, table, data):
    if not data:
        return
    allowed = {k: v for k, v in data.items() if k in table.c}
    stmt = mysql_insert(table).values(**allowed)
    updates = {
        k: stmt.inserted[k]
        for k, v in allowed.items()
        if not table.c[k].primary_key and v is not None
    }
    if updates:
        conn.execute(stmt.on_duplicate_key_update(**updates))
    else:
        conn.execute(stmt.prefix_with('IGNORE'))


def event(conn, name, message, task_id=None, cookie_id=None, level='info', **details):
    conn.execute(
        logs.insert().values(
            event=name,
            message=message,
            task_id=task_id,
            cookie_id=cookie_id,
            level=level,
            details=details,
            created_at=utcnow(),
        )
    )


def init_db():
    from research_models import load_queries

    meta.create_all(engine)
    from sqlalchemy import inspect, text

    existing = {col['name'] for col in inspect(engine).get_columns('cookie_accounts')}
    with engine.begin() as conn:
        additions = {
            'cookie_accounts': {
                'request_interval_min': 'FLOAT NOT NULL DEFAULT 1',
                'request_interval_max': 'FLOAT NOT NULL DEFAULT 2.5',
            },
            'tasks': {
                'search_mode': "VARCHAR(24) NOT NULL DEFAULT 'hourly'",
                'compacted_jobs': 'BIGINT NOT NULL DEFAULT 0',
            },
            'search_windows': {'parent_id': 'VARCHAR(36) NULL', 'range_end': 'DATETIME NULL'},
        }
        for table, columns in additions.items():
            present = {col['name'] for col in inspect(conn).get_columns(table)}
            for name, definition in columns.items():
                if name not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
        next_type = next(
            col['type']
            for col in inspect(conn).get_columns('cookie_accounts')
            if col['name'] == 'next_request_at'
        )
        if getattr(next_type, 'fsp', None) != 3:
            conn.execute(
                text('ALTER TABLE cookie_accounts MODIFY next_request_at DATETIME(3) NULL')
            )
        for name, default in (('failure_limit', 5), ('consecutive_failures', 0)):
            if name not in existing:
                conn.execute(
                    text(
                        f'ALTER TABLE cookie_accounts ADD COLUMN {name} INT NOT NULL DEFAULT {default}'
                    )
                )
        if 'failure_limit' not in existing:
            # Remove legacy product quotas without automatically resuming paused tasks.
            conn.execute(tasks.update().values(request_limit=None))
            conn.execute(
                tasks.update()
                .where(tasks.c.status == 'quota_paused')
                .values(status='paused', last_error=None)
            )
    with engine.begin() as conn:
        for query in load_queries()[1]:
            upsert(conn, tables['queries'], query)


if __name__ == '__main__':
    init_db()
    print('MySQL schema ready:', ', '.join(meta.tables))
