#!/usr/bin/env python3
"""Research CLI. Bounded samples only; no command starts the full 2020–2026 crawl."""
import argparse
import json
from pathlib import Path
from research_client import Client
from research_crawler import Crawler
from research_models import CONFIG, load_queries
from research_store import Store

ROOT = Path(__file__).resolve().parent.parent


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['queries', 'post', 'comments', 'replies', 'search', 'smoke', 'export'])
    parser.add_argument('--post-id', help='numeric mid or public mblogid; required for post/comments/replies')
    parser.add_argument('--root-comment-id', help='root comment ID; replies mode only')
    parser.add_argument('--root-user-id', help='root author ID; replies mode only')
    parser.add_argument('--keyword', default='AI心理咨询', help='exact configured query; combination uses a space')
    parser.add_argument('--start', default='2026-07-01T00:00:00+08:00')
    parser.add_argument('--end', default='2026-08-01T00:00:00+08:00', help='exclusive upper bound')
    parser.add_argument('--config', default=str(CONFIG))
    parser.add_argument('--cookie', help='local Cookie header file or browser-export JSON array')
    parser.add_argument('--output', default=str(ROOT / 'output' / 'research_sample'))
    parser.add_argument('--max-requests', type=positive, default=16)
    parser.add_argument('--max-posts', type=positive, default=3)
    parser.add_argument('--max-pages', type=positive, default=2)
    parser.add_argument('--max-reply-pages', type=positive, default=2)
    parser.add_argument('--max-threads', type=positive, default=3)
    parser.add_argument('--delay', type=float, default=3.0)
    args = parser.parse_args(argv)
    if args.delay < 1:
        parser.error('--delay must be at least 1 second')
    if args.mode in ('post', 'comments', 'replies') and not args.post_id:
        parser.error('--post-id is required')
    if args.mode == 'replies' and not (args.root_comment_id and args.root_user_id):
        parser.error('--root-comment-id and --root-user-id are required')
    cfg, queries = load_queries(args.config)
    if args.mode == 'queries':
        print(json.dumps(dict(total=len(queries), scope={k:v for k,v in cfg.items() if k != 'groups'}, queries=queries), ensure_ascii=False, indent=2))
        return 0
    selected = next((q for q in queries if q['query'] == args.keyword), None)
    if args.mode in ('search', 'smoke') and not selected:
        parser.error('--keyword must match a configured query (use queries to list)')
    if args.mode in ('search', 'smoke'):
        from datetime import datetime
        from research_models import timestamp
        start, end = datetime.fromisoformat(timestamp(args.start)), datetime.fromisoformat(timestamp(args.end))
        if not datetime.fromisoformat(cfg['start_date']) <= start < end <= datetime.fromisoformat(cfg['end_date_exclusive']):
            parser.error('search dates must be within the configured study period')
    output = Path(args.output).resolve()
    store = Store(output / 'research.sqlite3')
    for query in queries:
        store.put('queries', query)
    summary = dict(mode=args.mode, run_id=store.run_id, scope='bounded_sample', tests={})
    code = 0
    client = None
    try:
        if args.mode != 'export':
            client = Client(args.cookie, max_requests=args.max_requests, delay=args.delay)
            crawler = Crawler(client, store)
            store.log('run_start', args.mode, 'normal', max_requests=args.max_requests,
                      max_pages=args.max_pages, max_posts=args.max_posts, max_reply_pages=args.max_reply_pages,
                      max_threads=args.max_threads)
            if args.mode == 'post':
                post = crawler.post(args.post_id)
                summary['tests']['post'] = dict(success=bool(post and post['retrieval_status'] == 'normal'), post_id=post['post_id'] if post else None)
            elif args.mode == 'comments':
                post = crawler.post(args.post_id)
                summary['tests']['post'] = dict(success=bool(post))
                if post:
                    audit = crawler.comments(post, args.max_pages, args.max_reply_pages, args.max_threads)
                    summary['tests']['comments'] = dict(success=audit['retrieved_first_level_count'] > 0, **audit)
            elif args.mode == 'replies':
                post = crawler.post(args.post_id)
                summary['tests']['post'] = dict(success=bool(post))
                if post:
                    root = store.get('comments', args.root_comment_id)
                    if not root:
                        # A known root can be probed independently. Its missing body is explicit.
                        root = dict(comment_id=args.root_comment_id, post_id=post['post_id'],
                                    root_comment_id=args.root_comment_id, comment_level=1,
                                    comment_user_id=args.root_user_id, retrieval_status='not_fetched')
                        store.put('comments', root)
                    found, reasons = crawler.replies(post['post_id'], root, args.max_reply_pages)
                    summary['tests']['replies'] = dict(success=bool(found), retrieved_reply_count=len(found), incomplete_reason=reasons)
            elif args.mode in ('search', 'smoke'):
                posts = crawler.search(selected, args.start, args.end, args.max_pages, args.max_posts)
                summary['tests']['search'] = dict(success=bool(posts), posts_returned=len(posts), query_id=selected['query_id'])
                summary['tests']['post'] = dict(success=any(p['retrieval_status'] == 'normal' for p in posts))
                if args.mode == 'smoke':
                    # Test a post with comments first; no user history/profile requests.
                    candidate = next((p for p in posts if p.get('comments_count', 0) > 0), posts[0] if posts else None)
                    if candidate:
                        audit = crawler.comments(candidate, args.max_pages, args.max_reply_pages, args.max_threads)
                        summary['tests']['comments'] = dict(success=audit['retrieved_first_level_count'] > 0, **audit)
                        summary['tests']['replies'] = dict(success=audit['retrieved_reply_count'] > 0,
                                                           note='zero replies does not establish reply-endpoint failure')
            if any(not t['success'] for t in summary['tests'].values()):
                code = 2
    except KeyboardInterrupt:
        summary['interrupted'] = True
        store.log('run_end', args.mode, 'incomplete', reason='interrupted')
        code = 130
    except Exception as exc:
        # Exception text can contain headers or response bodies; report only its type.
        summary['error_type'] = type(exc).__name__
        store.log('run_error', args.mode, 'failed', error_type=type(exc).__name__)
        code = 1
    finally:
        summary['requests_made'] = client.requests if client else 0
        summary['post_status_counts'] = dict(store.db.execute('SELECT retrieval_status, count(*) FROM posts GROUP BY retrieval_status').fetchall())
        store.log('run_end', args.mode, 'normal' if code == 0 else 'incomplete',
                  requests_made=summary['requests_made'], tests=summary['tests'], scope='bounded_sample')
        summary['counts'] = store.export(output)
        with (output / ('report_' + store.run_id + '.json')).open('w', encoding='utf-8') as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2)
        store.db.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
