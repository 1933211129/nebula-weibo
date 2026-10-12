import unittest
from server.collector.semantics import comment_cursor, confirmed_full_text


class EndpointSemanticsTests(unittest.TestCase):
    def test_explicit_empty_end_without_cursor(self):
        for cursor in (None, 123):
            self.assertEqual(
                comment_cursor(
                    dict(ok=1, data=[], max_id=cursor, total_number=0, trendsText='已加载全部评论')
                ),
                0,
            )

    def test_empty_alone_is_not_exhaustion(self):
        self.assertIsNone(comment_cursor(dict(ok=1, data=[], total_number=0)))
        self.assertEqual(comment_cursor(dict(ok=1, data=[], max_id=99)), 99)
        self.assertEqual(
            comment_cursor(dict(ok=1, data=[{}], max_id=99, trendsText='已加载全部评论')), 99
        )

    def test_short_looking_longtext_is_not_assumed_complete(self):
        self.assertIsNone(
            confirmed_full_text(dict(id=1, isLongText=True, text_raw='看起来完整。', textLength=12))
        )
        self.assertEqual(
            confirmed_full_text(dict(id=1, isLongText=False, text_raw='完整正文')), '完整正文'
        )
        self.assertEqual(
            confirmed_full_text(dict(longText=dict(longTextContent='明确全文'))), '明确全文'
        )

    def test_pool_reuses_client_and_clears_cookie_jar(self):
        from unittest.mock import patch, Mock
        from server.collector import transport

        transport.close_transport()
        client = Mock(is_closed=False)
        with patch.object(transport.httpx, 'Client', return_value=client) as factory:
            transport.pooled_get('https://weibo.com/a')
            transport.pooled_get('https://weibo.com/b')
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(client.cookies.clear.call_count, 2)
            transport.close_transport()
