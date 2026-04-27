from __future__ import annotations

from datetime import UTC, datetime

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue


def content_hash(queue: Queue, sms: IncomingSms) -> str:
    fallback_timestamp = sms.timestamp or datetime(1970, 1, 1, tzinfo=UTC)
    return queue._content_hash(sms, fallback_timestamp=fallback_timestamp)
