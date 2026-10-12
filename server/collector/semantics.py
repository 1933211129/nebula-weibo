"""Conservative endpoint termination and full-text evidence rules."""


def comment_cursor(result):
    cursor = result.get('max_id')
    # Some legitimate empty/end pages omit max_id or retain a stale cursor.
    # Never infer exhaustion from an empty list alone.
    terminal = result.get('trendsText') == '已加载全部评论'
    if result.get('ok') == 1 and result.get('data') == [] and terminal:
        return 0
    return cursor


def confirmed_full_text(detail):
    from weibospider.research_models import body

    embedded = (detail.get('longText') or {}).get('longTextContent')
    if isinstance(embedded, str) and embedded.strip():
        return embedded
    # A short, plausible-looking body or textLength alone cannot prove completeness.
    if detail.get('isLongText') is False and detail.get('id') and body(detail).strip():
        return body(detail)
    return None
