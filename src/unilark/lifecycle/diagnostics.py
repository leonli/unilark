"""Independent diagnostics: one failed subsystem must not hide the others."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from unilark import __version__
from unilark.adapters.sidecars.agy.transport import ProtocolError, RpcError
from unilark.lifecycle.status import health
from unilark.onboarding.config import load_agy
from unilark.onboarding.credentials import load_credentials
from unilark.onboarding.lark_check import check_application
from unilark.store.gateway import GatewayStore


async def collect(
    config: Path | None,
    state: Path,
    credentials_path: Path,
    loader: Callable[[Path], Any] = load_agy,
    *,
    check_api: bool = False,
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {
        "version": __version__,
        "stage": "experimental",
        "agy": {"status": "not_configured"},
        "lark": {"credentials": "missing", "owner": "not_paired", "connection": "not_observed"},
        "service": {"state": "not_observed", "running": False},
        "unresolved_operations": 0,
        "acceptance": {},
        "repairs": [],
    }
    profile = ""
    client = None
    if config:
        try:
            client = loader(config)
            profile = str(client.transport.user_data.resolve())
            result["agy"] = await client.check()
            if hasattr(client, "capabilities"):
                result["capabilities"] = asdict(client.capabilities)
        except RpcError as error:
            result["agy"] = {
                "status": "authentication_required" if error.status == "16" else "runtime_rejected"
            }
            result["repairs"].append("在所选 AGY 实例检查登录和项目；不要更换模型或绑定。")
        except ProtocolError as error:
            result["agy"] = {
                "status": "incompatible_bundle" if "bundle" in str(error) else "runtime_unavailable"
            }
            result["repairs"].append("恢复指定 AGY 实例；若 bundle 变化须重新验证兼容性。")
        except httpx.HTTPError:
            result["agy"] = {"status": "local_connection_error"}
            result["repairs"].append("检查指定 AGY 的回环服务和进程。")
        except (OSError, KeyError, ValueError):
            result["agy"] = {"status": "invalid_configuration"}
            result["repairs"].append("用 --config 指定有效实例配置。")
        finally:
            if client is not None:
                await client.close()
    try:
        credentials = load_credentials(credentials_path)
    except (OSError, ValueError):
        result["repairs"].append("运行 setup 保存本人可读的应用凭据。")
        return result, 2
    result["lark"]["credentials"] = "configured"
    if check_api:
        result["lark"]["application"] = await check_application(credentials)
    if state.exists():
        try:
            store = GatewayStore(state, readonly=True)
            try:
                owner = store.owner(credentials.account)
                if owner:
                    result["lark"]["owner"] = "paired"
                    result["lark"]["delivery"] = store.delivery_health(owner)
                    if profile:
                        result["service"] = health(store.health(owner, profile))
                        result["acceptance"] = store.validations(owner, profile)
                    result["unresolved_operations"] = len(store.unknown_requests(owner)) + sum(
                        op["state"] in ("UNKNOWN", "SUBMITTING") for op in store.operations(owner)
                    )
                    result["database"] = {
                        "schema": store.db.execute("PRAGMA user_version").fetchone()[0],
                        "sessions": len(store.sessions(owner)),
                    }
                    if result["service"].get("state") == "running":
                        result["lark"]["connection"] = (
                            "connected"
                            if result["service"].get("lark_connected")
                            else "disconnected"
                        )
            finally:
                store.close()
        except (OSError, ValueError, sqlite3.Error):
            result["database"] = {"state": "unavailable_or_incompatible"}
            result["repairs"].append("保留数据库；使用兼容程序版本或一致性备份修复。")
    service = result["service"]
    blocked = any(result["lark"].get("delivery", {}).get(s, 0) for s in ("UNKNOWN", "BLOCKED"))
    ready = (
        result["agy"].get("status") == "verified"
        and service.get("state") == "running"
        and service.get("running")
        and service.get("lark_connected")
        and service.get("agy_observed")
        and not result["unresolved_operations"]
        and not blocked
        and (not check_api or result["lark"]["application"]["state"] == "verified")
    )
    if not ready:
        result["repairs"].append(
            "检查 service status；未知结果使用 recovery list 本机核对，勿删除账本重发。"
        )
    return result, 0 if ready else 2
