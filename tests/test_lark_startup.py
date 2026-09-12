"""Real SDK startup inside asyncio: network-independent loop ownership regression."""

from __future__ import annotations

import subprocess
import sys

import pytest


def test_sdk_imported_inside_running_loop_can_start_and_stop():
    probe = r"""
import asyncio

async def main():
    from unilark.adapters.lark.channel import LarkChannel
    from unilark.onboarding.credentials import Credentials
    from lark_channel.ws import client as ws
    adapter = LarkChannel(Credentials('lark', 'cli_fixture', 'fixture-secret'))
    adapter.sdk._fetch_bot_identity_sync = lambda: None
    adapter.sdk._config.transport.auto_reconnect = False
    original = asyncio.get_running_loop()
    connection_loops = []

    async def connect(self):
        connection_loops.append(asyncio.get_running_loop())
        self._conn = object()

    async def disconnect(self, **kwargs):
        self._conn = None

    async def ping(self):
        await asyncio.sleep(3600)

    ws.Client._connect = connect
    ws.Client._disconnect = disconnect
    ws.Client._ping_loop = ping
    try:
        await adapter.connect()
        assert adapter.connected and adapter.sdk.is_ready
        assert connection_loops and connection_loops[0] is not original
        assert asyncio.get_running_loop() is original and original.is_running()
    finally:
        await adapter.disconnect()
    await asyncio.sleep(0)
    assert original.is_running()
    print('SDK_START_STOP_OK')

asyncio.run(main())
"""
    result = subprocess.run(  # noqa: S603 - fixed source probe in an isolated interpreter
        [sys.executable, "-c", probe], text=True, capture_output=True, timeout=15, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SDK_START_STOP_OK" in result.stdout
    assert "never awaited" not in result.stderr
    assert "Task was destroyed" not in result.stderr


@pytest.mark.parametrize("cycles", [10])
def test_real_sdk_receiver_and_reconnect_cleanup_leave_no_pending_tasks(cycles):
    probe = r"""
import asyncio
from unilark.adapters.lark.channel import LarkChannel
from unilark.onboarding.credentials import Credentials
from lark_channel.ws import client as ws

class Socket:
    def __init__(self):
        self.closed = asyncio.Event()
    async def recv(self):
        await self.closed.wait()
        raise RuntimeError('test socket closed')
    async def close(self):
        self.closed.set()
        await asyncio.sleep(0.01)
    async def send(self, value):
        return None

async def connect(self):
    async with self._lock:
        if self._conn is not None:
            return
        self._conn = Socket()
        self._service_id = '1'
        ws.loop.create_task(self._receive_message_loop(self._conn))

ws.Client._connect = connect

async def main():
    for _ in range(CYCLES):
        adapter = LarkChannel(Credentials('lark','cli_fixture','fixture-secret'))
        adapter.sdk._fetch_bot_identity_sync = lambda: None
        await adapter.connect()
        assert adapter.sdk.is_ready
        await adapter.disconnect()
    print('DRAINED')

asyncio.run(main())
""".replace("CYCLES", str(cycles))
    result = subprocess.run(  # noqa: S603 - fixed network-free probe
        [sys.executable, "-c", probe], text=True, capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRAINED" in result.stdout
    assert "never awaited" not in result.stderr
    assert "Task was destroyed" not in result.stderr
