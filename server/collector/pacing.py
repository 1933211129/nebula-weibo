"""Per-channel millisecond pacing, independent of failure backoff."""

import random


def request_delay(cookie):
    low = round(cookie['request_interval_min'] * 1000)
    high = round(cookie['request_interval_max'] * 1000)
    if not 1000 <= low <= high <= 120000:
        raise ValueError('Invalid Cookie interval range')
    return random.randint(low, high) / 1000
