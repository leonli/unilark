"""Local operator recovery and exports; never execute an ambiguous task again."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from unilark.lifecycle.files import backup_database, write_json
from unilark.lifecycle.lock import InstanceLock
from unilark.onboarding.credentials import load_credentials
from unilark.store.gateway import GatewayStore


def overview(store: GatewayStore, account: str) -> dict[str, Any]:
    owner = store.owner(account)
    if owner is None:
        raise ValueError("Local owner is not paired")
    return {
        "schema": store.db.execute("PRAGMA user_version").fetchone()[0],
        "inputs": [
            {k: op[k] for k in ("request_id", "binding_id", "kind", "state", "native_step")}
            for op in store.operations(owner)
            if op["state"] in ("UNKNOWN", "SUBMITTING", "DISMISSED")
        ],
        "controls": [
            {k: r[k] for k in ("event", "action", "binding", "status")}
            for r in store.unknown_requests(owner)
        ],
        "cards": [
            dict(r)
            for r in store.db.execute(
                "SELECT id,binding,state,message_id,revision,delivered FROM cards WHERE owner=? "
                "AND state IN ('UNKNOWN','BLOCKED','DISMISSED')",
                (owner.key,),
            )
        ],
        "observations": [
            dict(r)
            for r in store.db.execute(
                "SELECT o.* FROM observations o JOIN session_meta s ON o.binding=s.binding "
                "WHERE s.owner=?",
                (owner.key,),
            )
        ],
    }


def run(args: argparse.Namespace) -> int:
    if args.command == "backup":
        backup_database(args.state, args.output)
        print(json.dumps({"backup": str(args.output), "consistent": True}))
        return 0
    credentials = load_credentials(args.credentials)
    if args.command == "recovery" and args.recovery_action != "list":
        if not args.acknowledge_possible_execution:
            raise ValueError("Explicit --acknowledge-possible-execution is required")
        # A running gateway may be reconciling the same request; require it to be stopped.
        with InstanceLock(args.state.with_suffix(".lock")):
            store = GatewayStore(args.state)
            try:
                owner = store.owner(credentials.account)
                if owner is None:
                    raise ValueError("Owner not paired")
                store.acknowledge_unknown(owner, args.entity, args.reference)
            finally:
                store.close()
        print("已记录本机决议并保留暂停；不表示任务未执行，也不会重发。")
        return 0
    store = GatewayStore(args.state, readonly=args.command != "retry-delivery")
    try:
        owner = store.owner(credentials.account)
        if owner is None:
            raise ValueError("Owner not paired")
        if args.command == "retry-delivery":
            store.retry_delivery(owner, args.card)
            print("已允许重试原 message ID 的更新；不会重新创建卡片。")
            return 0
        result = overview(store, credentials.account)
        if args.command == "audit":
            result = {
                "recovery": [
                    dict(r)
                    for r in store.db.execute(
                        "SELECT at,entity,reference,decision FROM recovery_audit "
                        "WHERE owner=? ORDER BY id",
                        (owner.key,),
                    )
                ],
                "security": [
                    dict(r) for r in store.db.execute("SELECT at,reason FROM audit ORDER BY id")
                ],
            }
        if args.command in ("audit", "diagnostics"):
            write_json(args.output, result)
            print(
                json.dumps(
                    {"output": str(args.output), "message_bodies": False, "credentials": False}
                )
            )
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        store.close()


def parsers(commands: Any) -> None:
    recovery = commands.add_parser("recovery", help="列出未知结果，或在本机接受其不确定性")
    choices = recovery.add_subparsers(dest="recovery_action", required=True)
    choices.add_parser("list")
    acknowledge = choices.add_parser("acknowledge")
    acknowledge.add_argument("entity", choices=("input", "control", "card"))
    acknowledge.add_argument("reference")
    acknowledge.add_argument("--acknowledge-possible-execution", action="store_true")
    commands.add_parser("retry-delivery", help="修复权限后重试已知消息的更新").add_argument("card")
    for name in ("backup", "diagnostics", "audit"):
        commands.add_parser(name).add_argument("--output", type=Path, required=True)
