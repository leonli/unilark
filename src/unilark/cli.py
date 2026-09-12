"""Local configuration, lifecycle and diagnostics for the experimental gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

from . import __version__
from .adapters.sidecars.agy.client import public_step
from .adapters.sidecars.agy.transport import ProtocolError, RpcError
from .lifecycle import diagnostics, maintenance, releases, service
from .onboarding.config import load_agy
from .onboarding.credentials import load_credentials
from .policy.redact import Redactor
from .store.gateway import GatewayStore


async def _run(args: argparse.Namespace) -> int:
    if args.command in ("upgrade", "rollback", "uninstall"):
        return await releases.run(args)
    if args.command in ("backup", "diagnostics", "audit", "recovery", "retry-delivery"):
        return maintenance.run(args)
    if args.command == "service":
        return service.run(args)
    if args.command == "setup":
        from .onboarding.setup import run as setup

        return await setup(args)
    if args.command == "pair":
        from .lifecycle.runner import pair

        return await pair(args.credentials, args.state)
    if args.command == "run":
        from .lifecycle.runner import run

        if args.config is None:
            raise ValueError("run requires --config")
        return await run(args.config, args.credentials, args.state)
    if args.command == "sessions":
        credentials = load_credentials(args.credentials)
        store = GatewayStore(args.state)
        try:
            owner = store.owner(credentials.account)
            if owner is None:
                raise ValueError("Owner not paired")
            sessions = [
                {k: s[k] for k in ("id", "native_id", "state", "title", "queue_state")}
                | {
                    "context": store.journal.context(s["id"]),
                    "observation": store.journal.observation(s["id"]),
                }
                for s in store.sessions(owner)
            ]
            print(json.dumps(sessions, ensure_ascii=False, indent=2))
        finally:
            store.close()
        return 0
    if args.command == "doctor":
        report, status = await diagnostics.collect(
            args.config, args.state, args.credentials, load_agy, check_api=args.check_api
        )
        if args.connect_lark and args.credentials.exists():
            from .adapters.lark.channel import LarkChannel
            from .lifecycle.lock import InstanceLock
            from .lifecycle.logging import configure

            credentials = load_credentials(args.credentials)
            with InstanceLock(args.state.with_suffix(".lock")):
                store = GatewayStore(args.state)
                try:
                    channel = LarkChannel(credentials, store.owner(credentials.account))
                    configure(Redactor((credentials.app_secret,)))
                    try:
                        await channel.connect()
                        report["lark"]["connection_probe"] = "connected"
                    finally:
                        await channel.disconnect()
                finally:
                    store.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return status
    if args.config is None:
        print("请用 --config 指定 AGY 实例；setup 可继续未完成的配置。")
        return 2
    client = load_agy(args.config)
    try:
        check = await client.check()
        if args.command == "sidecar":
            print(
                json.dumps(
                    {
                        "sidecar_id": "agy",
                        "check": check,
                        "capabilities": asdict(client.capabilities),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        steps = await client.steps(args.session)
        if not steps:
            raise ProtocolError("Empty history cannot establish an existing session binding")
        history_credentials = (
            load_credentials(args.credentials) if args.credentials.exists() else None
        )
        if args.command == "history":
            redactor = Redactor((history_credentials.app_secret,) if history_credentials else ())
            print(
                redactor.text(
                    json.dumps(
                        [public_step(s, i) for i, s in enumerate(steps)],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            )
            return 0
        if history_credentials is None:
            raise ValueError("Pair the local owner before attaching a gateway session")
        store = GatewayStore(args.state)
        try:
            owner = store.owner(history_credentials.account)
            if owner is None:
                raise ValueError("Owner not paired")
            binding = store.add_session(
                owner,
                str(client.transport.user_data.resolve()),
                args.session,
                "ACTIVE",
                "已连接原生会话",
            )
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
    parser = argparse.ArgumentParser(
        prog="unilark", description="Unilark 本地共享会话网关（实验版）"
    )
    parser.add_argument("--version", action="version", version=f"unilark {__version__}")
    default_config = Path.home() / ".unilark/agy.toml"
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config if default_config.exists() else None,
        help="AGY 实例 TOML（不含密钥）",
    )
    parser.add_argument("--state", type=Path, default=Path.home() / ".unilark/state.db")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path.home() / ".unilark/lark.env",
        help="0600 凭据数据文件，不作为 shell 执行",
    )
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="分项检查运行健康与历史验收")
    doctor.add_argument("--connect-lark", action="store_true", help="网关停止后独占长连接探测")
    doctor.add_argument("--check-api", action="store_true", help="核验应用认证与机器人启用状态")
    commands.add_parser("setup", help="首次配置或继续未完成步骤；保留 owner 与会话").add_argument(
        "--non-interactive", action="store_true"
    )
    commands.add_parser("pair", help="在本机确认一次性私聊配对")
    commands.add_parser("run", help="前台运行；退出保留外部 AGY")
    commands.add_parser("sessions", help="列出本人的持久化会话")
    for name in ("attach", "history"):
        commands.add_parser(name).add_argument("session", help="原生会话 UUID")
    sidecar = commands.add_parser("sidecar", help="查看已验证的实例与能力")
    sidecar.add_argument("sidecar_action", choices=("list", "check"))
    sidecar.add_argument("sidecar_id", nargs="?", default="agy", choices=("agy",))
    maintenance.parsers(commands)
    service.parsers(commands)
    releases.parsers(commands)
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
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        print(
            f"未就绪：{type(exc).__name__}；使用 doctor 分项检查配置、配对、实例和服务状态。",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
