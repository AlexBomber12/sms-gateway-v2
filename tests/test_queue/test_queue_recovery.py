from __future__ import annotations

from sms_gateway_v2.modem import IncomingSms
from sms_gateway_v2.queue import Queue


async def claim_enqueued(queue: Queue, sms: IncomingSms) -> str:
    item = await queue.enqueue(sms)
    assert item is not None
    claimed = await queue.claim_next()
    assert claimed == item
    return item.id


async def test_recover_processing_returns_zero_on_empty_processing(queue: Queue) -> None:
    assert await queue.recover_processing() == 0


async def test_recover_processing_moves_all_items_back_to_pending(
    queue: Queue,
    sample_sms: IncomingSms,
) -> None:
    first_id = await claim_enqueued(queue, sample_sms)
    second_id = await claim_enqueued(queue, sample_sms.model_copy(update={"text": "second"}))

    recovered = await queue.recover_processing()

    assert recovered == 2
    assert sorted(path.name for path in queue._dirs["pending"].glob("*.json")) == [
        f"{first_id}.json",
        f"{second_id}.json",
    ]
    assert list(queue._dirs["processing"].glob("*.json")) == []
