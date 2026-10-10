"""Drain subprocess pipes with bounded retained output and explicit cancellation cleanup."""

from __future__ import annotations

import asyncio
import os
import signal


async def bounded_communicate(proc, *, limit: int = 100_000, tail: bool = False):
    async def drain(stream):
        if stream is None:
            return b""
        kept = b""
        while chunk := await stream.read(8192):
            if tail:
                kept = (kept + chunk)[-limit:]
            elif len(kept) < limit:
                kept += chunk[: limit - len(kept)]
        return kept

    stdout, stderr, _ = await asyncio.gather(drain(proc.stdout), drain(proc.stderr), proc.wait())
    return stdout, stderr


async def kill_and_reap(proc, *, group: bool = False):
    try:
        if group:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    await proc.wait()
