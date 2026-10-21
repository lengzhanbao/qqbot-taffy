"""Offline regressions for interval timing and bounded message admission."""
import asyncio
import time
from unittest.mock import AsyncMock

import bridge_qq as app


def test_phase_logger_reports_individual_intervals(monkeypatch):
    ticks = iter([10.125, 10.375])
    lines = []
    monkeypatch.setattr(app, "_PHASE_CLOCK", lambda: next(ticks))
    monkeypatch.setattr(app, "log", lines.append)

    boundary = app._log_phase(10.0, "上下文", "turn-a")
    boundary = app._log_phase(boundary, "搜索", "turn-a")

    assert boundary == 10.375
    assert "turn=turn-a 上下文: 125ms" in lines[0]
    assert "turn=turn-a 搜索: 250ms" in lines[1]


def test_model_request_logs_http_duration_and_outcome(monkeypatch):
    bridge = object.__new__(app.Bridge)
    lines = []
    monkeypatch.setattr(app, "log", lines.append)
    bridge._api_call = lambda body, *_: ("输出", "ok", [], "stop", [])

    result = asyncio.run(bridge._run_api_call({
        "model": "test-model", "_trace_id": "turn-test", "_stage": "final",
        "_attempt": 2,
    }, "https://example.invalid", "offline"))

    assert result[1] == "ok"
    assert any("[模型请求] turn=turn-test stage=final model=test-model "
               "attempt=2 elapsed=" in line and "state=ok finish=stop chars=2" in line
               for line in lines)


def test_queue_is_bounded_and_overload_notice_is_rate_limited(monkeypatch):
    monkeypatch.setattr(app, "MESSAGE_QUEUE_MAXSIZE", 1)
    monkeypatch.setattr(app, "TRIGGER", "at")
    bridge = object.__new__(app.Bridge)
    bridge.workers = {}
    bridge._queue_notice_at = {}
    bridge._queue_notice_tasks = set()
    bridge.send_group_msg = AsyncMock()

    async def hold_worker(key, queue):
        await asyncio.Event().wait()

    bridge._worker_loop = hold_worker

    async def run():
        event = {"message_type": "group", "group_id": "g", "user_id": "u"}
        assert bridge.enqueue_message("g", event, at_bot=True)
        assert not bridge.enqueue_message("g", event, at_bot=True)
        assert not bridge.enqueue_message("g", event, at_bot=True)
        assert bridge.workers["g"].qsize() == 1
        assert not bridge.enqueue_message("g", event, at_bot=False)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert bridge.send_group_msg.await_count == 1
        assert "排队满啦" in bridge.send_group_msg.await_args.args[2]
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_worker_records_wait_time_and_finishes_queue_item(monkeypatch):
    bridge = object.__new__(app.Bridge)
    lines = []
    monkeypatch.setattr(app, "log", lines.append)
    done = asyncio.Event()

    async def process(ev):
        done.set()

    bridge._process_private = process
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({
        "message_type": "private",
        "_qqbot_enqueued_at": time.monotonic() - 0.05,
        "_qqbot_trace_id": "private_u:trace",
    })

    async def run():
        worker = asyncio.create_task(bridge._worker_loop("private_u", queue))
        try:
            await asyncio.wait_for(done.wait(), timeout=1)
            assert queue.qsize() == 0
            assert queue._unfinished_tasks == 0
            assert any("[队列出队] turn=private_u:trace wait=" in line for line in lines)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
