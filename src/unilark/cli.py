"""Local diagnostics and persistent attachment for the early M1 AGY client."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from . import __version__
from .adapters.sidecars.agy.client import public_step
from .adapters.sidecars.agy.transport import ProtocolError, RpcError
from .lifecycle.status import health
from .onboarding.config import load_agy
from .onboarding.credentials import load_credentials
from .store.gateway import GatewayStore
from .store.ledger import Ledger


async def _run(args: argparse.Namespace) -> int:
    if args.command == "pair":
        from .lifecycle.runner import pair

        return await pair(args.credentials, args.state)
    if args.command == "run":
        from .lifecycle.runner import run

        if args.config is None:
            raise ValueError("run requires --config")
        return await run(args.config, args.credentials, args.state)
    if args.command == "sessions":
        store = Ledger(args.state)
        try:
            print(json.dumps(store.bindings(), ensure_ascii=False, indent=2))
        finally:
            store.close()
        return 0
    if args.config is None:
        print(f"unilark {__version__} — M1 开发中")
        print("AGY: 已有真实连接实现；请用 --config 指定实例配置。")
        print("Lark: 使用 pair 配对、run 启动；AGY → Lark → AGY 闭环未验收。")
        return 2
    client = load_agy(args.config)
    try:
        check = await client.check()
        if args.command == "doctor":
            service: dict[str, object] = {"state": "not_observed", "running": False}
            validation: dict[str, object] = {}
            unknown = 0
            lark: dict[str, object] = {
                "credentials": "missing",
                "owner": "not_paired",
                "connection": "not_tested",
                "permissions": "not_verified",
            }
            if args.credentials.exists():
                credentials = load_credentials(args.credentials)
                lark["credentials"] = "configured"
                store = GatewayStore(args.state)
                try:
                    lark["owner"] = "paired" if store.owner(credentials.account) else "not_paired"
                    owner = store.owner(credentials.account)
                    if owner:
                        lark["delivery"] = store.delivery_health(owner)
                        profile = str(client.transport.user_data.resolve())
                        service = health(store.health(owner, profile))
                        validation = store.validations(owner, profile)
                        unknown = len(store.unknown_requests(owner)) + sum(
                            op["state"] in ("UNKNOWN", "SUBMITTING")
                            for op in store.operations(owner)
                        )
                        if service.get("state") == "running":
                            lark["connection"] = (
                                "connected" if service.get("lark_connected") else "disconnected"
                            )
                finally:
                    store.close()
                if args.connect_lark:
                    from .adapters.lark.channel import LarkChannel
                    from .lifecycle.lock import InstanceLock
                    from .lifecycle.logging import configure
                    from .policy.redact import Redactor

                    with InstanceLock(args.state.with_suffix(".lock")):
                        channel = LarkChannel(credentials, owner)
                        configure(Redactor((credentials.app_secret,)))
                        try:
                            await channel.connect()
                            lark["connection"] = "connected"
                        finally:
                            await channel.disconnect()
            print(
                json.dumps(
                    {
                        "stage": "M1",
                        "agy": check,
                        "lark": lark,
                        "service": service,
                        "unresolved_operations": unknown,
                        "acceptance": validation,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            delivery = lark.get("delivery", {})
            blocked_delivery = isinstance(delivery, dict) and any(
                delivery.get(s, 0) for s in ("UNKNOWN", "BLOCKED")
            )
            ready = (
                service.get("state") == "running"
                and service.get("running")
                and service.get("lark_connected")
                and service.get("agy_observed")
                and not unknown
                and not blocked_delivery
            )
            return 0 if ready else 2
        steps = await client.steps(args.session)
        if not steps:
            raise ProtocolError("Empty history cannot establish an existing session binding")
        if args.command == "history":
            print(
                json.dumps(
                    [public_step(s, i) for i, s in enumerate(steps)], ensure_ascii=False, indent=2
                )
            )
            return 0
        store = Ledger(args.state)
        try:
            binding = store.bind(str(client.transport.user_data.resolve()), args.session)
            print(
                json.dumps(
                    {"binding_id": binding, "native_id": args.session, "steps": len(steps)},
                    ensure_ascii=False,
                )
            )
        finally:
            store.close()
        return 0
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unilark", description="Unilark（M1 开发中）")
    parser.add_argument("--version", action="version", version=f"unilark {__version__}")
    parser.add_argument("--config", type=Path, help="AGY 实例 TOML（不含密钥）")
    parser.add_argument("--state", type=Path, default=Path.home() / ".unilark/state.db")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path.home() / ".unilark/lark.env",
        help="0600 凭据数据文件；不作为 shell 执行",
    )
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="检查运行健康和独立验收记录；运行异常时返回非零")
    doctor.add_argument("--connect-lark", action="store_true", help="独占连接探测，不发送消息")
    commands.add_parser("pair", help="5 分钟私聊配对窗口，并在本机确认 owner")
    commands.add_parser("run", help="前台运行网关；退出保留外部 AGY 和会话")
    commands.add_parser("sessions", help="列出本地持久化绑定，不依赖 AGY 枚举")
    for name in ("attach", "history"):
        commands.add_parser(name).add_argument("session", help="原生 conversation UUID")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return asyncio.run(_run(args))
    except (
        ProtocolError,
        RpcError,
        ValueError,
        KeyError,
        OSError,
        httpx.HTTPError,
        RuntimeError,
        ImportError,
    ) as exc:
        print(
            f"未就绪：{type(exc).__name__}；请检查配置、凭据文件、配对和实例状态。", file=sys.stderr
        )
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
