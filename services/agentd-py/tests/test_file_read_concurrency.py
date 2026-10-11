import asyncio
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentd.tools.batching import Call, ExecutionPool, Resource, resources_for, run_batch
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource


def gate_reads(monkeypatch, paths, before_read):
    original_open = Path.open

    @contextmanager
    def gated_open(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as stream:
            if path in paths and args == ("rb",):
                def read(size):
                    before_read(path)
                    return stream.read(size)

                yield SimpleNamespace(read=read)
            else:
                yield stream

    monkeypatch.setattr(Path, "open", gated_open)


@pytest.mark.asyncio
async def test_real_batch_file_reads_overlap(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("a.py", "b.py")]
    for path in paths:
        path.write_text(path.name)
    barrier = threading.Barrier(2)
    workers = set()
    lock = threading.Lock()

    def before_read(path):
        with lock:
            workers.add(threading.get_ident())
        # Both actual file reads must arrive before either can finish. A blocking
        # implementation on the event loop cannot pass this rendezvous.
        barrier.wait(timeout=3)

    gate_reads(monkeypatch, paths, before_read)
    registry = AggregatingToolRegistry([
        BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)
    ])
    calls = [Call(path.name, "read_file", {"path": path.name}) for path in paths]
    pool = ExecutionPool(2)

    async def prepare(call):
        return await registry.prepare(call.tool, call.args)

    async def update(outcome):
        pass

    results = await run_batch(
        calls, prepare=prepare, update=update, pool=pool,
        resources=lambda call: resources_for(call, tmp_path, tmp_path),
    )
    assert [result.state for result in results] == ["success", "success"]
    assert [result.output.output for result in results] == ["   1: a.py", "   1: b.py"]
    assert len(workers) == pool.high_water == 2
    assert threading.get_ident() not in workers


@pytest.mark.asyncio
async def test_cancelled_file_read_keeps_lease_until_worker_finishes(tmp_path, monkeypatch):
    path = tmp_path / "a.py"
    path.write_text("original")
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def before_read(path):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=5)

    gate_reads(monkeypatch, [path], before_read)
    registry = AggregatingToolRegistry([
        BuiltinToolSource(shadow_root=tmp_path, real_workspace_path=tmp_path)
    ])
    pool = ExecutionPool(2)
    call = Call("read", "read_file", {"path": path.name})

    async def prepare(call):
        return await registry.prepare(call.tool, call.args)

    async def update(outcome):
        pass

    batch = asyncio.create_task(run_batch(
        [call], prepare=prepare, update=update, pool=pool,
        resources=lambda call: resources_for(call, tmp_path, tmp_path),
    ))
    edit_started = asyncio.Event()

    async def edit():
        async with pool.lease((Resource(path, True),)):
            edit_started.set()
            path.write_text("edited")

    edit_task = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        batch.cancel()
        edit_task = asyncio.create_task(edit())
        await asyncio.sleep(0)
        assert not batch.done()
        assert not edit_started.is_set()
        assert pool.active
        # Repeated cancellation must not release the still-running read's lease.
        batch.cancel()
        await asyncio.sleep(0)
        assert not batch.done()
        assert not edit_started.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(batch, 2)
        if edit_task is not None:
            await asyncio.wait_for(edit_task, 2)
    assert edit_started.is_set()
    assert path.read_text() == "edited"
    assert not pool.active
