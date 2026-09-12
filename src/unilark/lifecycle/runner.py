"""Foreground gateway and local-only pairing. AGY is externally owned."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from unilark.adapters.lark.channel import LarkChannel
from unilark.conversation.hub import Hub
from unilark.lifecycle.lock import InstanceLock
from unilark.lifecycle.logging import configure
from unilark.lifecycle.status import process_start
from unilark.onboarding.config import load_agy
from unilark.onboarding.credentials import load_credentials
from unilark.onboarding.pairing import Pairing
from unilark.policy.redact import Redactor
from unilark.store.gateway import GatewayStore


async def terminal_line(prompt: str, timeout: float) -> str:
    """No input executor thread left hanging when the pairing window expires."""
    print(prompt, end="", flush=True)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    def ready() -> None:
        if not future.done():
            future.set_result(sys.stdin.readline().strip())

    loop.add_reader(sys.stdin.fileno(), ready)
    try:
        return await asyncio.wait_for(future, max(0, timeout))
    finally:
        loop.remove_reader(sys.stdin.fileno())


async def pair(credentials_path: Path, state: Path) -> int:
    if not sys.stdin.isatty():
        raise ValueError("Pairing requires a local interactive terminal")
    credentials = load_credentials(credentials_path)
    with InstanceLock(state.with_suffix(".lock")):
        store = GatewayStore(state)
        channel = LarkChannel(credentials)
        configure(Redactor((credentials.app_secret,)))
        try:
            if store.owner(credentials.account):
                print("此应用已配对；保留原 owner，可直接运行 unilark run。")
                return 0
            window = Pairing(credentials.account)
            channel.on_message = window.receive
            print("正在连接 Lark；可在控制台将事件与卡片回调设为长连接。", flush=True)
            await channel.connect()
            print(f"5 分钟内在机器人私聊发送：/pair {window.code}", flush=True)
            while True:
                candidate = await asyncio.wait_for(
                    window.candidates.get(), max(0, window.expires - time.time())
                )
                # Use wall-clock expiry; the nonce is never persisted or logged.
                print(
                    "候选身份："
                    + json.dumps(
                        [candidate.tenant, candidate.user, candidate.chat], ensure_ascii=False
                    )
                )
                typed = await terminal_line(
                    "核对这是本人后，粘贴上方完整 open_id 确认（回车忽略）：",
                    window.expires - time.time(),
                )
                if not typed:
                    continue
                owner = window.confirm(candidate, typed)
                store.set_owner(owner)
                print("身份已在本机确认并保存。真实跨端闭环仍待测试。")
                return 0
        finally:
            await channel.disconnect()
            store.close()


async def run(config: Path, credentials_path: Path, state: Path) -> int:
    credentials = load_credentials(credentials_path)
    with InstanceLock(state.with_suffix(".lock")):
        store = GatewayStore(state)
        client = load_agy(config)
        channel = LarkChannel(credentials, store.owner(credentials.account))
        redactor = Redactor((credentials.app_secret,))
        configure(redactor)
        loop = asyncio.get_running_loop()
        owner = store.owner(credentials.account)
        profile = str(client.transport.user_data.resolve())

        def report(state: str = "running") -> None:
            if owner is None:
                return
            sessions = [
                s
                for s in store.sessions(owner)
                if s["profile"] == profile and s["state"] == "ACTIVE"
            ]
            store.heartbeat(
                owner,
                profile,
                {
                    "state": state,
                    "pid": os.getpid(),
                    "process_start": process_start(os.getpid()),
                    "fresh_for": max(30, 20 * len(sessions) + 10),
                    "lark_connected": channel.connected,
                    "agy_observed": all(hub.views.get(s["id"]) is not None for s in sessions),
                    "session_count": len(sessions),
                    "lark_errors": channel.errors,
                    "lark_rejections": channel.rejections,
                },
            )

        try:
            if owner is None:
                raise ValueError("Run unilark pair locally before starting the gateway")
            await client.check()
            hub = Hub(
                store, client, channel, owner, str(client.transport.user_data.resolve()), redactor
            )
            hub.report_health = report
            channel.on_message, channel.on_action = hub.accept, hub.action
            # Recover before inbound callbacks can arrive.
            store.recover_gateway()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, hub.stopping.set)
            await channel.connect()
            report("starting")
            print("AGY 已核验；Lark 长连接已建立。端到端收发仍需实测。", flush=True)
            hub.notify(
                "gateway:welcome",
                None,
                "Unilark 已连接",
                "身份配对成功。\n\n先发送 /new 创建会话，再发送你的任务。"
                "\n\n运行时发送 /stop 可停止并暂停队列，/help 查看命令。",
            )
            await hub.run()
            return 0
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            try:
                await channel.disconnect()
            finally:
                try:
                    await client.close()
                finally:
                    if owner is not None:
                        store.heartbeat(
                            owner,
                            profile,
                            {
                                "state": "stopped",
                                "pid": os.getpid(),
                                "process_start": process_start(os.getpid()),
                                "lark_connected": False,
                            },
                        )
                    store.close()
