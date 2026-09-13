"""Explicit installation acceptance backed by native observations and Lark readback.

This command never sends a task or approves a tool. Desktop-origin proof requires
the local operator's explicit confirmation because an untagged input alone is not proof.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

from unilark.adapters.sidecars.views import SessionView
from unilark.conversation.channel import Owner
from unilark.onboarding.config import load_agy
from unilark.onboarding.credentials import load_credentials
from unilark.policy.redact import Redactor
from unilark.store.gateway import GatewayStore


def contains_text(value: Any, text: str) -> bool:
    if isinstance(value, str):
        return text in value
    if isinstance(value, dict):
        return any(contains_text(v, text) for v in value.values())
    if isinstance(value, list):
        return any(contains_text(v, text) for v in value)
    return False


def evidence(
    store: GatewayStore, owner: Owner, binding: str, view: SessionView, capability: str
) -> dict[str, Any]:
    if not view.idle:
        raise ValueError("Wait for the acceptance task to finish")
    if capability == "lark_permission_and_stop":
        controls = [
            dict(r)
            for r in store.db.execute(
                "SELECT action,body FROM inbox WHERE owner=? AND binding=? AND status='DONE'",
                (owner.key, binding),
            )
        ]
        permits = [json.loads(c["body"]) for c in controls if c["action"] == "resolve"]
        stops = [json.loads(c["body"]) for c in controls if c["action"] == "stop" and c["body"]]
        if not any(
            p.get("decision") == "allow"
            and p.get("applied_at")
            and any(
                stop.get("stop_was_busy")
                and stop.get("stop_idle_confirmed")
                and stop.get("stop_observed_at", 0) >= p["applied_at"]
                for stop in stops
            )
            and any(
                s.index == p["step"] and s.kind == "tool" and s.status != "waiting"
                for s in view.steps
            )
            for p in permits
        ):
            raise ValueError("No completed Allow callback and confirmed Stop in this binding")
        basis = (
            "Allow callback applied; later Stop observed busy then confirmed idle; "
            "operator confirmed tool effect"
        )
    else:
        inputs = [s for s in view.steps if s.kind == "user"]
        if capability == "text_roundtrip":
            operations = [
                op
                for op in store.operations(owner)
                if op["binding_id"] == binding and op["state"] == "ACCEPTED"
            ]
            inputs = [
                s
                for s in inputs
                if any(
                    op["request_id"] in s.operation_ids and s.text == op["content"]
                    for op in operations
                )
            ]
        else:
            inputs = [s for s in inputs if not s.operation_ids]
        if not inputs:
            raise ValueError("No matching native input and gateway receipt")
        basis = "native input and later assistant response; local operator confirmed desktop view"
    candidates = [
        s
        for s in view.steps
        if s.kind == "assistant"
        and s.text.strip()
        and (capability == "lark_permission_and_stop" or any(s.index > u.index for u in inputs))
    ]
    for step in reversed(candidates):
        card_id = store.card_id(owner, f"step:{binding}:{step.index}:0")
        row = store.db.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if row and row["message_id"] and row["delivered"] == row["revision"]:
            return {
                "basis": basis,
                "message_id": row["message_id"],
                "step": step.index,
                "text": step.text,
            }
    raise ValueError("No completed assistant card delivery for this binding")


async def run(args: argparse.Namespace) -> int:
    if args.config is None or not args.confirm_native_view:
        raise ValueError("Use --config and confirm the same task is visible in the native desktop")
    if args.capability == "desktop_ui_relay" and not args.confirm_desktop_ui:
        raise ValueError("Desktop origin needs explicit --confirm-desktop-ui")
    if args.capability == "lark_permission_and_stop" and not args.confirm_tool_effect:
        raise ValueError("Inspect the controlled tool result and use --confirm-tool-effect")
    credentials = load_credentials(args.credentials)
    client = load_agy(args.config)
    store = GatewayStore(args.state, readonly=True)
    try:
        if store.db.execute("PRAGMA user_version").fetchone()[0] not in (2, 3):
            raise ValueError("Upgrade the gateway before recording acceptance with this version")
        owner = store.owner(credentials.account)
        if owner is None:
            raise ValueError("Owner not paired")
        session = store.session(owner, args.binding)
        profile = str(client.transport.user_data.resolve())
        if session["profile"] != profile:
            raise ValueError("Binding belongs to a different runtime")
        proof = evidence(
            store, owner, args.binding, await client.view(session["native_id"]), args.capability
        )
        text = Redactor((credentials.app_secret,)).text(proof.pop("text"))[:80]
        async with httpx.AsyncClient(base_url=credentials.domain, timeout=20) as http:
            auth = (
                await http.post(
                    "/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": credentials.app_id, "app_secret": credentials.app_secret},
                )
            ).json()
            if auth.get("code") != 0:
                raise ValueError("Lark authentication failed")
            result = (
                await http.get(
                    "/open-apis/im/v1/messages/" + proof["message_id"],
                    headers={"Authorization": "Bearer " + auth["tenant_access_token"]},
                )
            ).json()
            if result.get("code") != 0 or not result.get("data", {}).get("items"):
                raise ValueError("Lark message readback failed")
            item = result["data"]["items"][0]
            body = item.get("body", {}).get("content", "")
            if item.get("chat_id") != owner.chat or not contains_text(json.loads(body), text):
                raise ValueError("Readback does not match this owner's native response")
        proof.update(status="passed", lark_readback=True, desktop_confirmed=True)
    finally:
        store.close()
        await client.close()
    writer = GatewayStore(args.state)
    try:
        writer.record_validation(owner, profile, args.capability, proof)
    finally:
        writer.close()
    print(
        json.dumps(
            {"capability": args.capability, "status": "passed", "message_bodies_saved": False}
        )
    )
    return 0
