"""Resumable local setup. Existing pairing and sessions are always preserved."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

from unilark.lifecycle.diagnostics import collect
from unilark.lifecycle.files import private_directory, write_json
from unilark.lifecycle.service import handoff
from unilark.onboarding.credentials import load_credentials
from unilark.onboarding.lark_check import check_application
from unilark.store.gateway import GatewayStore


def save_credentials(path: Path) -> None:
    private_directory(path.parent)
    if path.exists():
        load_credentials(path)
        return
    edition = input("应用区域（lark / feishu）：").strip()
    app = input("App ID：").strip()
    secret = getpass.getpass("App Secret（隐藏输入）：").strip()
    if (
        edition not in ("lark", "feishu")
        or not app.startswith("cli_")
        or not secret
        or any("\n" in v or "\r" in v for v in (edition, app, secret))
    ):
        raise ValueError("Invalid application credentials")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(
                f"UNILARK_LARK_EDITION={edition}\nUNILARK_LARK_APP_ID={app}\nUNILARK_LARK_APP_SECRET={secret}\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Never replace an existing secret file, even in a race.
    finally:
        Path(temporary).unlink(missing_ok=True)


async def run(args: argparse.Namespace) -> int:
    private_directory(args.state.parent)
    interactive = sys.stdin.isatty() and not args.non_interactive
    if not args.credentials.exists() and interactive:
        print(
            "创建专用机器人并启用私聊消息/卡片回调长连接：\nhttps://open.larksuite.com/app\nhttps://open.feishu.cn/app"
        )
        save_credentials(args.credentials)
    if args.credentials.exists():
        credentials = load_credentials(args.credentials)
        check = await check_application(credentials)
        if check["state"] == "verified":
            store = GatewayStore(args.state, readonly=args.state.exists())
            owner = store.owner(credentials.account)
            store.close()
            if owner is None and interactive:
                from unilark.lifecycle.runner import pair

                await pair(args.credentials, args.state)
    report, status = await collect(args.config, args.state, args.credentials, check_api=True)
    report["environment"] = {
        "os": sys.platform,
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "verified_platform": sys.platform == "linux" and platform.machine() == "x86_64",
    }
    passed = report["acceptance"]
    report["service_handoff"] = handoff(args, report["service"])
    accepted = all(
        passed.get(k, {}).get("status") == "passed"
        for k in ("text_roundtrip", "desktop_ui_relay", "lark_permission_and_stop")
    )
    report["setup_state"] = (
        "ready"
        if status == 0 and accepted and report["service_handoff"]["status"] == "passed"
        else "external_steps_or_acceptance_pending"
    )
    if not accepted:
        report["repairs"].append(
            "在专用会话完成手机与桌面接力、允许/停止，然后运行 acceptance verify 保存核验；"
            "已有 owner 不需要重配。"
        )
    if report["service_handoff"]["status"] != "passed":
        report["repairs"].append("先 run 前台验收；随后 service install / start 交接唯一后台进程。")
    report["conditions"] = [
        "AGY 必须独立运行并登录。",
        "工具权限由 AGY 审批决定，不提供独立工作区沙箱。",
        "主机睡眠/离线时不保证执行或推送；恢复后核对缺口。",
    ]
    write_json(args.state.parent / "setup-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["setup_state"] == "ready" else 2
