"""Bounded LIVE integration test. Requires local Cookie; never prints credentials."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'weibospider'))
from research_client import Client
from research_crawler import API, Crawler
from research_models import comment_record, now, user_record
from research_store import Store


class ObservedClient(Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pages = []

    def get(self, endpoint, params=None, as_json=True):
        data = super().get(endpoint, params, as_json)
        if params and params.get('fetch_level') == 1:
            ids = [str(r.get('idstr') or r['id']) for r in data.get('data', [])]
            page = dict(
                page=len(self.pages) + 1,
                cursor=str(params.get('max_id', 0)),
                next_cursor=str(data.get('max_id')),
                max_id_type=data.get('max_id_type'),
                count=len(ids),
                ids=ids,
                returned_root_ids=sorted(
                    {str(r.get('rootidstr') or r.get('rootid')) for r in data.get('data', [])}
                ),
            )
            self.pages.append(page)
            print(
                json.dumps({k: v for k, v in page.items() if k != 'ids'}, ensure_ascii=False),
                flush=True,
            )
        return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--post-id', required=True)
    p.add_argument('--root-comment-id', required=True)
    p.add_argument('--max-pages', type=int, default=6)
    p.add_argument('--output', default='output/live_reply_pagination')
    args = p.parse_args()
    if not 2 <= args.max_pages <= 10:
        p.error('--max-pages must be between 2 and 10')
    store = Store(Path(args.output) / 'research.sqlite3')
    client = ObservedClient(max_requests=args.max_pages + 4)
    crawler = Crawler(client, store)
    report = dict(
        tested_at=now(),
        run_id=store.run_id,
        post_id=args.post_id,
        root_comment_id=args.root_comment_id,
        live=True,
    )
    try:
        post = crawler.post(args.post_id)
        if not post:
            raise RuntimeError('post_unavailable')
        # Popular ordering is used ONLY to locate the known test fixture root.
        # Production first-level collection remains chronological (flow=1).
        data = client.get(
            API + 'buildComments',
            dict(
                is_reload=1,
                id=post['post_id'],
                uid=post['user_id'],
                is_show_bulletin=2,
                is_mix=0,
                count=20,
                flow=0,
            ),
        )
        raw = next(r for r in data['data'] if str(r['id']) == args.root_comment_id)
        root = comment_record(raw, post['post_id'])
        store.put('comments', root)
        store.put('users', user_record(raw.get('user')))
        ids, reasons = crawler.replies(post['post_id'], root, args.max_pages)
        seen = set()
        for page in client.pages:
            page['new_ids_count'] = len(set(page['ids']) - seen)
            page['duplicate_ids_count'] = len(page['ids']) - page['new_ids_count']
            seen.update(page['ids'])
        rows = [dict(r) for r in store.db.execute('SELECT * FROM comments WHERE comment_level=2')]
        checks = dict(
            multiple_nonempty_pages=sum(page['count'] > 0 for page in client.pages) >= 2,
            cursor_chain=all(
                b['cursor'] == a['next_cursor'] and b['cursor'] != '0'
                for a, b in zip(client.pages, client.pages[1:])
            ),
            later_pages_add_new_ids=any(page['new_ids_count'] > 0 for page in client.pages[1:]),
            api_root_ids_match=all(
                page['returned_root_ids'] == [root['comment_id']]
                for page in client.pages
                if page['count']
            ),
            stored_ids_match=set(r['comment_id'] for r in rows) == ids == seen,
            stored_relationships=all(
                r['post_id'] == post['post_id']
                and r['root_comment_id'] == root['comment_id']
                and r['parent_comment_id']
                for r in rows
            ),
        )
        report.update(
            success=all(checks.values()),
            checks=checks,
            pages=client.pages,
            expected_reply_count=root.get('reply_count'),
            retrieved_reply_count=len(ids),
            exhausted=bool(client.pages and client.pages[-1]['next_cursor'] == '0'),
            incomplete_reason=reasons,
        )
    except Exception as exc:
        report.update(success=False, error_type=type(exc).__name__)
    finally:
        report['requests_made'] = client.requests
        store.log(
            'live_reply_pagination_test',
            args.root_comment_id,
            'normal' if report.get('success') else 'failed',
            **report,
        )
        report['export_counts'] = store.export(args.output)
        path = Path(args.output) / ('pagination_report_' + store.run_id + '.json')
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        store.db.close()
    print(
        json.dumps({k: v for k, v in report.items() if k != 'pages'}, ensure_ascii=False, indent=2)
    )
    return 0 if report.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
