import unittest
from unittest.mock import patch
from server.collector.pacing import request_delay


class PacingTests(unittest.TestCase):
    def test_millisecond_random_bounds(self):
        c = dict(request_interval_min=1, request_interval_max=2.5)
        with patch('server.collector.pacing.random.randint', return_value=1234) as draw:
            self.assertEqual(request_delay(c), 1.234)
            draw.assert_called_once_with(1000, 2500)
        for _ in range(100):
            value = request_delay(c)
            self.assertTrue(1 <= value <= 2.5)
            self.assertEqual(value, round(value, 3))

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            request_delay(dict(request_interval_min=2.5, request_interval_max=1))
