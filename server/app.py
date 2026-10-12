import json
import os
import uuid
from datetime import timedelta
from typing import List, Optional, Literal
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from server.collector.pacing import request_delay
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from server.db import (
    ROOT,
    engine,
    cookies,
    tasks,
    jobs,
    windows,
    attempts,
    logs,
    tables,
    task_posts,
    task_comments,
    audits,
    utcnow,
    event,
    keyword_checks,
)
from server.security import same_origin, normalize_cookie, encrypt, decrypt
from server.service import create_task, task_action, list_tasks, coverage, task_metrics
from server.collector.transport import fetch, API, RemoteError
from research_models import load_queries


app = FastAPI(
    title='微博研究采集平台',
    dependencies=[Depends(same_origin)],
    docs_url='/api/docs',
    openapi_url='/api/openapi.json',
)


class CookieInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: Optional[str] = Field(default=None, max_length=64000)
    request_interval: float = Field(default=3, ge=3, le=120)
    request_interval_min: float = Field(default=1, ge=1, le=120, multiple_of=0.001)
    request_interval_max: float = Field(default=2.5, ge=1, le=120, multiple_of=0.001)
    failure_limit: int = Field(default=5, ge=1, le=100)

    @model_validator(mode='after')
    def valid_interval(self):
        if self.request_interval_min > self.request_interval_max:
            raise ValueError('最短间隔不能大于最长间隔')
        return self


class TaskInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    start_at: str
    end_at: str
    query_ids: List[str] = Field(default_factory=list, max_length=1000)
    keywords_text: str = Field(default='', max_length=250000)
    keyword_mode: str = 'words'
    combination_left: str = Field(default='', max_length=250000)
    combination_right: str = Field(default='', max_length=250000)
    cookie_ids: List[str] = Field(default_factory=list, max_length=50)
    include_comments: bool = True
    search_mode: Literal['hourly', 'adaptive'] = 'adaptive'


class TaskEdit(BaseModel):
    cookie_ids: List[str] = Field(max_length=50)
    search_mode: Optional[Literal['hourly', 'adaptive']] = None


@app.get('/api/health')
def health():
    try:
        with engine.connect() as conn:
            conn.execute(select(1))
        return {'status': 'ok', 'database': 'connected'}
    except Exception:
        raise HTTPException(503, '数据库暂不可用')


@app.get('/api/overview')
def overview(task_id: Optional[str] = None):
    ts = list_tasks()
    selected = next((t for t in ts if t['id'] == task_id), None) if task_id else None
    if task_id and not selected:
        raise HTTPException(404, '任务不存在，请重新选择')
    selected = selected or next((t for t in ts if t['status'] == 'running'), None)
    selected = selected or (ts[0] if ts else None)
    if not selected:
        return dict(
            task_id=None,
            counts={},
            tasks=[],
            active_cookies=0,
            running_workers=0,
            coverage=[],
            recent_logs=[],
            query_count=0,
        )
    tid = selected['id']
    with engine.connect() as conn:
        counts = {
            'posts': selected['posts_count'],
            'comments': selected['comments_count'],
            'replies': selected['replies_count'],
            'users': conn.scalar(
                select(func.count()).select_from(record_query('users', tid).subquery())
            ),
        }
        active = conn.scalar(
            select(func.count())
            .select_from(cookies)
            .where(cookies.c.status == 'active', cookies.c.id.in_(selected['cookie_ids']))
        )
        recent = [
            dict(r)
            for r in conn.execute(
                select(logs).where(logs.c.task_id == tid).order_by(logs.c.id.desc()).limit(12)
            ).mappings()
        ]
    return dict(
        counts=counts,
        task_id=tid,
        tasks=ts,
        active_cookies=active,
        running_workers=selected['running_jobs'],
        coverage=coverage(tid),
        recent_logs=recent,
        study_start=selected['start_at'],
        study_end=selected['end_at'],
        query_count=len(selected['query_ids']),
    )


@app.get('/api/totals')
def data_totals():
    """Actual global rows, independent of task membership or content status."""
    with engine.connect() as conn:
        counts = {
            name: conn.scalar(select(func.count()).select_from(tables[name]))
            for name in ('posts', 'comments', 'users')
        }
        counts['replies'] = conn.scalar(
            select(func.count())
            .select_from(tables['comments'])
            .where(tables['comments'].c.comment_level == 2)
        )
    return counts


@app.get('/api/requirements')
def requirements():
    sections = [
        {'title': '采集范围', 'lines': [
            '按关键词与北京时间范围检索公开微博，保存帖子、评论、回复及关联用户公开元数据。',
            '仅用于学习研究，禁止大规模爬取和商用；使用前须遵守微博 robots 协议及适用法律法规。',
        ]},
        {'title': '检索词示例', 'lines': [
            '内置词库仅为示例；可在创建任务时输入自定义关键词或两组交叉关键词。',
            '组合词须在补全后的帖子正文中同时命中；日期覆盖不等于完整召回。',
        ]},
        {'title': '字段说明', 'lines': [
            '帖子包含正文、时间、互动计数、话题、链接、媒体计数及关键词关联。',
            '评论包含父级与根评论关系；用户包含昵称、认证信息、关注和粉丝计数。',
            '字段缺失保留为空，完整字段定义见仓库 README 与数据库文档。',
        ]},
    ]
    cfg, queries = load_queries()
    return dict(
        sections=sections,
        config=cfg,
        queries=queries,
        schema={k: [c.name for c in t.c] for k, t in tables.items()},
    )


@app.get('/api/queries')
def queries():
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(tables['queries'])).mappings()]


def safe_cookie(row):
    return {
        k: v
        for k, v in dict(row).items()
        if k not in ('encrypted_value', 'fingerprint', 'lease_owner')
    }


@app.get('/api/cookies')
def cookie_list():
    with engine.connect() as conn:
        result = []
        for r in conn.execute(select(cookies).order_by(cookies.c.created_at)).mappings():
            item = safe_cookie(r)
            item['busy'] = bool(r['lease_until'] and r['lease_until'] > utcnow())
            item['waiting_interval'] = bool(
                not item['busy'] and r['next_request_at'] and r['next_request_at'] > utcnow()
            )
            job = (
                conn.execute(
                    select(jobs.c.task_id, jobs.c.kind)
                    .where(jobs.c.cookie_id == r['id'], jobs.c.status == 'running')
                    .limit(1)
                )
                .mappings()
                .first()
            )
            item['current_job'] = dict(job) if job else None
            result.append(item)
    return result


@app.post('/api/cookies')
def add_cookie(data: CookieInput):
    if not data.value:
        raise HTTPException(400, '请输入 Cookie')
    try:
        value, fingerprint = normalize_cookie(data.value)
        cid = str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(
                cookies.insert().values(
                    id=cid,
                    name=data.name,
                    encrypted_value=encrypt(value),
                    fingerprint=fingerprint,
                    request_interval=data.request_interval,
                    request_interval_min=data.request_interval_min,
                    request_interval_max=data.request_interval_max,
                    failure_limit=data.failure_limit,
                    status='unverified',
                    request_count=0,
                )
            )
            event(conn, 'cookie_added', '已添加 Cookie，等待验证', cookie_id=cid)
        return {'id': cid}
    except IntegrityError:
        raise HTTPException(409, '该登录凭据已存在，请更新原配置；同一凭据不能重复作为并发通道')
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, 'Cookie 格式无效或缺少 SUB，请粘贴完整请求头或浏览器导出的 JSON')


@app.put('/api/cookies/{cid}')
def edit_cookie(cid: str, data: CookieInput):
    try:
        with engine.begin() as conn:
            row = (
                conn.execute(select(cookies).where(cookies.c.id == cid).with_for_update())
                .mappings()
                .first()
            )
            if not row:
                raise HTTPException(404, 'Cookie 不存在')
            if row['lease_until'] and row['lease_until'] > utcnow():
                raise HTTPException(409, '该通道正在请求，请暂停任务后稍候更新')
            values = dict(
                name=data.name,
                request_interval=data.request_interval,
                failure_limit=data.failure_limit,
                request_interval_min=data.request_interval_min,
                request_interval_max=data.request_interval_max,
            )
            if data.value:
                value, fp = normalize_cookie(data.value)
                values.update(
                    encrypted_value=encrypt(value),
                    fingerprint=fp,
                    status='unverified',
                    last_error=None,
                    consecutive_failures=0,
                )
            conn.execute(cookies.update().where(cookies.c.id == cid).values(**values))
        return {'ok': True}
    except IntegrityError:
        raise HTTPException(409, '凭据与另一通道重复')
    except (ValueError, TypeError):
        raise HTTPException(400, 'Cookie 格式无效')


@app.post('/api/cookies/{cid}/validate')
def validate_cookie(cid: str):
    owner = 'validate:' + str(uuid.uuid4())
    with engine.begin() as conn:
        row = (
            conn.execute(select(cookies).where(cookies.c.id == cid).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, 'Cookie 不存在')
        if row['lease_until'] and row['lease_until'] > utcnow():
            raise HTTPException(409, '通道正在采集，请稍后再验证')
        if row['next_request_at'] and row['next_request_at'] > utcnow():
            raise HTTPException(429, '请求间隔内，请稍后验证')
        conn.execute(
            cookies.update()
            .where(cookies.c.id == cid)
            .values(
                lease_owner=owner,
                lease_until=utcnow() + timedelta(seconds=60),
                request_count=cookies.c.request_count + 1,
            )
        )
    status, error = 'active', None
    try:
        d = fetch(decrypt(row['encrypted_value']), API + 'show', {'id': '5321271801808779'})
        if not d.get('id') or not d.get('user'):
            raise RemoteError('invalid_response')
    except RemoteError as e:
        status = (
            'expired'
            if e.code == 'login_expired'
            else 'restricted'
            if e.code in ('access_restricted', 'rate_limited')
            else 'unverified'
        )
        error = e.code
    except Exception:
        status, error = 'unverified', 'validation_error'
    with engine.begin() as conn:
        conn.execute(
            cookies.update()
            .where(cookies.c.id == cid, cookies.c.lease_owner == owner)
            .values(
                status=status,
                last_error=error,
                last_checked_at=utcnow(),
                consecutive_failures=0 if status == 'active' else row['consecutive_failures'],
                lease_owner=None,
                lease_until=None,
                next_request_at=utcnow() + timedelta(seconds=request_delay(row)),
            )
        )
        event(
            conn,
            'cookie_checked',
            'Cookie 验证通过' if status == 'active' else 'Cookie 验证未通过：' + str(error),
            cookie_id=cid,
            level='info' if status == 'active' else 'warning',
        )
    return {'status': status, 'error': error}


@app.post('/api/cookies/{cid}/disable')
def disable_cookie(cid: str):
    with engine.begin() as conn:
        conn.execute(cookies.update().where(cookies.c.id == cid).values(status='disabled'))
        event(conn, 'cookie_disabled', 'Cookie 通道已停用', cookie_id=cid)
    return {'ok': True}


@app.get('/api/tasks')
def get_tasks():
    return list_tasks()


@app.post('/api/tasks')
def add_task(data: TaskInput):
    try:
        return {'id': create_task(data.model_dump())}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post('/api/tasks/{tid}/{action}')
def act_task(tid: str, action: str):
    try:
        task_action(tid, action)
        return {'ok': True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch('/api/tasks/{tid}')
def edit_task(tid: str, data: TaskEdit):
    with engine.begin() as conn:
        row = (
            conn.execute(select(tasks).where(tasks.c.id == tid).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, '任务不存在')
        if row['status'] == 'running':
            raise HTTPException(409, '请暂停任务后再调整配置')
        ids = set(
            conn.execute(select(cookies.c.id).where(cookies.c.id.in_(data.cookie_ids))).scalars()
        )
        if ids != set(data.cookie_ids):
            raise HTTPException(400, 'Cookie 配置不存在')
        if data.search_mode and data.search_mode != row['search_mode']:
            if data.search_mode != 'adaptive':
                raise HTTPException(400, '已建立的自适应断点不可直接改为逐小时，请新建任务')
            from server.collector.planning import enable_adaptive

            try:
                enable_adaptive(conn, tid)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        conn.execute(
            tasks.update().where(tasks.c.id == tid).values(cookie_ids=list(ids), request_limit=None)
        )
    return {'ok': True}


@app.get('/api/tasks/{tid}')
def task_detail(tid: str):
    with engine.connect() as conn:
        row = conn.execute(select(tasks).where(tasks.c.id == tid)).mappings().first()
        if not row:
            raise HTTPException(404, '任务不存在')
        return dict(
            row,
            metrics=task_metrics(conn, row),
            coverage=coverage(tid),
            keyword_verification=dict(
                conn.execute(
                    select(keyword_checks.c.status, func.count())
                    .where(keyword_checks.c.task_id == tid)
                    .group_by(keyword_checks.c.status)
                ).all()
            ),
            keyword_checks=[
                dict(r)
                for r in conn.execute(
                    select(keyword_checks, tables['queries'].c.query)
                    .join(
                        tables['queries'], keyword_checks.c.query_id == tables['queries'].c.query_id
                    )
                    .where(
                        keyword_checks.c.task_id == tid, keyword_checks.c.status != 'search_match'
                    )
                    .order_by(keyword_checks.c.checked_at.desc())
                    .limit(100)
                ).mappings()
            ],
            audits=[
                dict(r)
                for r in conn.execute(
                    select(audits).where(audits.c.task_id == tid).limit(100)
                ).mappings()
            ],
            logs=[
                dict(r)
                for r in conn.execute(
                    select(logs).where(logs.c.task_id == tid).order_by(logs.c.id.desc()).limit(100)
                ).mappings()
            ],
        )


@app.get('/api/tasks/{tid}/checkpoints')
def checkpoints(
    tid: str,
    kind: str = 'windows',
    status: str = '',
    page: int = Query(default=1, ge=1),
    q: str = '',
):
    """Bounded, read-only inspection of durable hourly and request checkpoints."""
    if kind not in ('windows', 'jobs', 'attempts'):
        raise HTTPException(400, '请选择小时窗口、处理队列或请求记录')
    table = {'windows': windows, 'jobs': jobs, 'attempts': attempts}[kind]
    with engine.connect() as conn:
        if not conn.execute(select(tasks.c.id).where(tasks.c.id == tid)).first():
            raise HTTPException(404, '任务不存在')
        query = select(table).where(table.c.task_id == tid)
        if status:
            query = query.where(
                (table.c.outcome if kind == 'attempts' else table.c.status) == status
            )
        if kind == 'windows':
            query = query.add_columns(tables['queries'].c.query).join(
                tables['queries'], table.c.query_id == tables['queries'].c.query_id
            )
            if q:
                query = query.where(tables['queries'].c.query.contains(q, autoescape=True))
            query = query.order_by(table.c.start_at, table.c.query_id)
        else:
            query = query.order_by(
                (table.c.started_at if kind == 'attempts' else table.c.created_at).desc(),
                table.c.id,
            )
        total = conn.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        rows = [dict(r) for r in conn.execute(query.limit(30).offset((page - 1) * 30)).mappings()]
        for row in rows:
            row.pop('lease_owner', None)
        return {'rows': rows, 'total': total}


def record_query(name, task_id=None, q=None):
    if name not in ('posts', 'comments', 'users', 'post_query_matches'):
        raise HTTPException(404, '数据表不存在')
    table = tables[name]
    query = select(table)
    if task_id and name == 'posts':
        query = query.join(task_posts, task_posts.c.post_id == table.c.post_id).where(
            task_posts.c.task_id == task_id
        )
    elif task_id and name == 'comments':
        query = query.join(task_comments, task_comments.c.comment_id == table.c.comment_id).where(
            task_comments.c.task_id == task_id
        )
    elif task_id and name == 'post_query_matches':
        query = query.join(task_posts, task_posts.c.post_id == table.c.post_id).where(
            task_posts.c.task_id == task_id
        )
    elif task_id and name == 'users':
        post_users = (
            select(tables['posts'].c.user_id)
            .join(task_posts, task_posts.c.post_id == tables['posts'].c.post_id)
            .where(task_posts.c.task_id == task_id)
        )
        comment_users = (
            select(tables['comments'].c.comment_user_id)
            .join(task_comments, task_comments.c.comment_id == tables['comments'].c.comment_id)
            .where(task_comments.c.task_id == task_id)
        )
        query = query.where(
            or_(table.c.user_id.in_(post_users), table.c.user_id.in_(comment_users))
        )
    if q:
        field = {
            'posts': 'post_text',
            'comments': 'comment_text',
            'users': 'screen_name',
            'post_query_matches': 'matched_keyword',
        }[name]
        query = query.where(table.c[field].contains(q, autoescape=True))
    return query


@app.get('/api/records/{name}')
def records(name: str, task_id: Optional[str] = None, q: Optional[str] = None, page: int = 1):
    if page < 1 or page > 100000:
        raise HTTPException(400, '页码无效')
    query = record_query(name, task_id, q)
    with engine.connect() as conn:
        count = conn.scalar(select(func.count()).select_from(query.subquery()))
        key = list(tables[name].primary_key.columns)
        rows = conn.execute(query.order_by(*key).offset((page - 1) * 30).limit(30)).mappings().all()
    return dict(total=count, page=page, page_size=30, rows=[dict(r) for r in rows])


@app.get('/api/export/{name}')
def export(name: str, task_id: Optional[str] = None, q: Optional[str] = None):
    query = record_query(name, task_id, q)

    def generate():
        with engine.connect().execution_options(stream_results=True) as conn:
            for row in conn.execute(query).mappings():
                yield json.dumps(dict(row), ensure_ascii=False, default=str) + '\n'

    return StreamingResponse(
        generate(),
        media_type='application/x-ndjson',
        headers={'Content-Disposition': 'attachment; filename="' + name + '.jsonl"'},
    )


@app.get('/api/logs')
def get_logs(task_id: Optional[str] = None):
    query = select(logs)
    if task_id:
        query = query.where(logs.c.task_id == task_id)
    with engine.connect() as conn:
        return [
            dict(r) for r in conn.execute(query.order_by(logs.c.id.desc()).limit(150)).mappings()
        ]


@app.get('/{path:path}')
def frontend(path: str):
    if path.startswith('api/'):
        raise HTTPException(404, '接口不存在')
    base = (ROOT / 'frontend/dist').resolve()
    file = (base / path).resolve()
    if file.is_relative_to(base) and file.is_file():
        return FileResponse(file)
    index = base / 'index.html'
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(503, '前端尚未构建，请执行 frontend 中的 npm run build')


base_path = os.environ.get('APP_BASE_PATH', '').strip('/')
if base_path:
    application = app
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount('/' + base_path, application)
