import unittest
from server.keywords import parse_keywords, cross_keywords, missing_terms


class KeywordTests(unittest.TestCase):
    def test_cross_and_literal_verification(self):
        self.assertEqual(
            cross_keywords('· 医院、问诊 医院', '精神科；心理'),
            ['医院 精神科', '医院 心理', '问诊 精神科', '问诊 心理'],
        )
        self.assertEqual(cross_keywords('医院', '医院'), ['医院'])
        self.assertEqual(missing_terms('<b>ＡＩ</b>心理咨询', ['ai', '心理咨询']), [])
        self.assertEqual(missing_terms('互联网医院', ['互联网医院', '精神科']), ['精神科'])
        with self.assertRaises(ValueError):
            cross_keywords('医院', '')
        with self.assertRaises(ValueError):
            cross_keywords(' '.join(map(str, range(40))), ' '.join(map(str, range(40))))

    def test_pasted_bullets_and_all_separators(self):
        self.assertEqual(
            parse_keywords(
                '· 远程心理健康、远程心理服务，数字心理健康;网络心理服务；线上心理服务,在线心理服务\n·\u00a0远程心理健康\t电话心理咨询'
            ),
            [
                '远程心理健康',
                '远程心理服务',
                '数字心理健康',
                '网络心理服务',
                '线上心理服务',
                '在线心理服务',
                '电话心理咨询',
            ],
        )

    def test_phrase_mode(self):
        self.assertEqual(
            parse_keywords('人工智能 心理咨询、AI 服务', 'phrases'),
            ['人工智能 心理咨询', 'AI 服务'],
        )

    def test_empty_and_limits(self):
        self.assertEqual(parse_keywords('· ， 、 ; \n'), [])
        for text in ['词' * 201, ','.join(str(i) for i in range(1001))]:
            with self.assertRaises(ValueError):
                parse_keywords(text)
