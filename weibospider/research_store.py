"""Idempotent SQLite storage and JSONL exports with no raw API/user-profile dumps."""
import json
import sqlite3
import uuid
from pathlib import Path
from research_models import now

FIELDS = {
    'posts': 'post_id post_url post_text user_id created_at year_month is_original is_repost reposted_post_id reposted_user_id repost_text topics mentions urls media_type image_count video_count post_source likes_count comments_count reposts_count favorites_count ip_location query_id matched_keyword retrieved_at retrieval_status',
    'comments': 'comment_id post_id parent_comment_id root_comment_id comment_level comment_text comment_user_id reply_to_user_id created_at likes_count reply_count comment_source ip_location retrieved_at retrieval_status',
    'users': 'user_id screen_name verified verified_type verified_reason followers_count follow_count statuses_count user_description profile_retrieved_at',
    'post_query_matches': 'post_id query_id matched_keyword retrieved_at',
    'crawl_logs': 'log_id run_id event target retrieved_at retrieval_status details',
    'queries': 'query_id category query terms',
}
JSON_FIELDS = {'topics', 'mentions', 'urls', 'query_id', 'matched_keyword', 'details', 'terms'}
NUMERIC = {'is_original', 'is_repost', 'verified', 'verified_type', 'comment_level', 'image_count', 'video_count', 'likes_count', 'comments_count', 'reposts_count', 'favorites_count', 'reply_count', 'followers_count', 'follow_count', 'statuses_count'}


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.run_id = uuid.uuid4().hex
        for table, names in FIELDS.items():
            columns = names.split()
            specs = [c + (' INTEGER' if c in NUMERIC else ' TEXT') for c in columns]
            keys = 'post_id, query_id' if table == 'post_query_matches' else columns[0]
            self.db.execute('CREATE TABLE IF NOT EXISTS {} ({}, PRIMARY KEY ({}))'.format(table, ','.join(specs), keys))
        self.db.execute('CREATE INDEX IF NOT EXISTS comment_post ON comments(post_id)')
        self.db.commit()

    def put(self, table, item):
        if not item:
            return
        columns = FIELDS[table].split()
        values = [json.dumps(item[c], ensure_ascii=False) if isinstance(item.get(c), (dict, list)) else item.get(c) for c in columns]
        keys = ['post_id', 'query_id'] if table == 'post_query_matches' else [columns[0]]
        updates = ','.join('{0}=COALESCE(excluded.{0},{1}.{0})'.format(c, table) for c in columns if c not in keys)
        self.db.execute('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}'.format(
            table, ','.join(columns), ','.join('?' for _ in columns), ','.join(keys), updates), values)
        self.db.commit()

    def get(self, table, key):
        first = FIELDS[table].split()[0]
        row = self.db.execute('SELECT * FROM {} WHERE {}=?'.format(table, first), (str(key),)).fetchone()
        return self.decode(table, row) if row else None

    @staticmethod
    def decode(table, row):
        item = dict(row)
        for field in JSON_FIELDS:
            if field in item and item[field] is not None and not (table != 'posts' and field in ('query_id', 'matched_keyword')):
                item[field] = json.loads(item[field])
        for field in ('verified', 'is_original', 'is_repost'):
            if field in item and item[field] is not None:
                item[field] = bool(item[field])
        return item

    def sync_matches(self, post):
        rows = self.db.execute('SELECT query_id, matched_keyword FROM post_query_matches WHERE post_id=? ORDER BY query_id', (post['post_id'],)).fetchall()
        post['query_id'] = [r['query_id'] for r in rows]
        post['matched_keyword'] = sorted({r['matched_keyword'] for r in rows})
        self.put('posts', post)

    def match(self, pid, query):
        self.put('post_query_matches', dict(post_id=str(pid), query_id=query['query_id'], matched_keyword=query['query'], retrieved_at=now()))
        post = self.get('posts', pid)
        if not post:
            post = dict(post_id=str(pid), retrieval_status='not_fetched')
        self.sync_matches(post)

    def log(self, event, target, status, **details):
        self.put('crawl_logs', dict(log_id=uuid.uuid4().hex, run_id=self.run_id, event=event, target=str(target), retrieved_at=now(), retrieval_status=status, details=details))

    def export(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        counts = {}
        for table in FIELDS:
            counts[table] = 0
            with (directory / (table + '.jsonl')).open('w', encoding='utf-8') as stream:
                for row in self.db.execute('SELECT * FROM ' + table):
                    stream.write(json.dumps(self.decode(table, row), ensure_ascii=False) + '\n')
                    counts[table] += 1
        with (directory / 'comment_completeness.jsonl').open('w', encoding='utf-8') as stream:
            for row in self.db.execute("SELECT run_id, retrieved_at, details FROM crawl_logs WHERE event='comments_summary' ORDER BY retrieved_at"):
                audit = json.loads(row['details'])
                audit.update(run_id=row['run_id'], retrieved_at=row['retrieved_at'])
                stream.write(json.dumps(audit, ensure_ascii=False) + '\n')
        return counts
