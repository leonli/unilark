"""Private gRPC-web/JSON transport for an explicitly selected local AGY process.

No remote endpoints, environment proxies, global first-port discovery, or retries.
The CSRF value stays in memory and is excluded from representations and errors.
"""

from __future__ import annotations

import json
import os
import re
import struct
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


class ProtocolError(Exception):
    """A reply cannot prove that the requested operation succeeded."""


class RpcError(Exception):
    """RPC failure does not establish whether a write executed before the error."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"AGY RPC failed (grpc-status={status})")


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    started: str
    args: tuple[str, ...] = field(repr=False)


def processes() -> dict[int, Process]:
    result = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            args = tuple((directory / "cmdline").read_bytes().decode().strip("\0").split("\0"))
            pid = int(directory.name)
            result[pid] = Process(pid, int(stat[1]), stat[19], args)
        except (OSError, ValueError, UnicodeError):
            continue
    return result


def select_process(table: dict[int, Process], executable: Path, user_data: Path) -> Process:
    expected = f"--user-data-dir={user_data.resolve()}"
    matches = [
        p
        for p in table.values()
        if p.args
        and p.args[0] == str(executable.resolve())
        and expected in p.args
        and not any(a.startswith("--type=") for a in p.args)
    ]
    if len(matches) != 1:
        raise ProtocolError(
            "Expected exactly one AGY process for the configured executable/profile"
        )
    return matches[0]


def family(table: dict[int, Process], root: int) -> set[int]:
    owned = {root}
    while True:
        expanded = owned | {p.pid for p in table.values() if p.parent in owned}
        if expanded == owned:
            return owned
        owned = expanded


def listening_ports(pids: set[int]) -> list[int]:
    inodes = set()
    for pid in pids:
        try:
            for fd in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    link = fd.readlink().as_posix()
                    if link.startswith("socket:["):
                        inodes.add(link[8:-1])
                except OSError:
                    continue
        except OSError:
            continue
    # The verified Linux profile uses IPv4 loopback. IPv6/other hosts fail closed.
    ports = set()
    for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        columns = line.split()
        address, port = columns[1].split(":")
        if columns[3] == "0A" and address == "0100007F" and columns[9] in inodes:
            ports.add(int(port, 16))
    return sorted(ports)


@dataclass(frozen=True)
class Endpoint:
    port: int
    csrf: str = field(repr=False)
    process: Process


def encode(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    return struct.pack(">BI", 0, len(data)) + data


class Frames:
    """Incremental framing, including truncation, flags and length validation."""

    LIMIT = 32 * 1024 * 1024

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 5:
            flag, length = struct.unpack(">BI", self.buffer[:5])
            if flag not in (0, 128) or length > self.LIMIT:
                raise ProtocolError("Unsupported gRPC-web frame flags or excessive frame size")
            if len(self.buffer) < length + 5:
                break
            result.append((flag, bytes(self.buffer[5 : 5 + length])))
            del self.buffer[: 5 + length]
        return result

    def finish(self) -> None:
        if self.buffer:
            raise ProtocolError("Truncated gRPC-web response")


def trailers(data: bytes) -> dict[str, str]:
    try:
        return {
            k.strip().lower(): v.strip()
            for k, v in (line.split(":", 1) for line in data.decode().splitlines() if line)
        }
    except (ValueError, UnicodeError):
        raise ProtocolError("Malformed gRPC-web trailers") from None


class Transport:
    SERVICE = "exa.language_server_pb.LanguageServerService"

    def __init__(self, executable: Path, user_data: Path) -> None:
        self.executable = executable
        self.user_data = user_data
        self.endpoint: Endpoint | None = None
        # AGY's own loopback HTTPS service uses a self-signed certificate.
        # Endpoints are constrained by same-user process ownership above.
        self.http = httpx.AsyncClient(verify=False, trust_env=False, timeout=15)  # noqa: S501

    async def discover(self) -> Endpoint:
        table = processes()
        root = select_process(table, self.executable, self.user_data)
        for port in listening_ports(family(table, root.pid)):
            try:
                response = await self.http.get(f"https://127.0.0.1:{port}/", timeout=2)
                if response.status_code != 200:
                    continue
                found = re.search(r'"csrfToken"\s*:\s*"([^"\r\n]+)"', response.text)
                if found:
                    current = processes().get(root.pid)
                    if current is None or current.started != root.started:
                        raise ProtocolError("AGY restarted during discovery")
                    self.endpoint = Endpoint(port, found[1], root)
                    return self.endpoint
            except httpx.HTTPError:
                continue
        raise ProtocolError("Configured AGY profile has no available local UI endpoint")

    async def current(self) -> Endpoint:
        endpoint = self.endpoint
        if endpoint:
            table = processes()
            root = select_process(table, self.executable, self.user_data)
            if root.pid == endpoint.process.pid and root.started == endpoint.process.started:
                if endpoint.port in listening_ports(family(table, root.pid)):
                    return endpoint
        return await self.discover()

    async def stream(self, method: str, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]+", method):
            raise ValueError("Invalid RPC method")
        endpoint = await self.current()
        url = f"https://127.0.0.1:{endpoint.port}/{self.SERVICE}/{method}"
        status: str | None = None
        ended = False
        parser = Frames()
        async with self.http.stream(
            "POST",
            url,
            content=encode(payload),
            headers={
                "content-type": "application/grpc-web+json",
                "x-grpc-web": "1",
                "x-codeium-csrf-token": endpoint.csrf,
                "x-user-agent": "CONNECT_ES_USER_AGENT",
            },
            timeout=httpx.Timeout(30, read=90),
        ) as response:
            if response.status_code != 200:
                raise ProtocolError(f"AGY HTTP status {response.status_code}")
            status = response.headers.get("grpc-status")
            if status is not None and status != "0":
                raise RpcError(status)
            async for data in response.aiter_bytes():
                for flag, body in parser.feed(data):
                    if ended:
                        raise ProtocolError("Data received after gRPC-web trailers")
                    if flag == 128:
                        status = trailers(body).get("grpc-status")
                        ended = True
                        if status is None:
                            raise ProtocolError("Missing grpc-status in trailers")
                        if status != "0":
                            raise RpcError(status)
                    else:
                        try:
                            value = json.loads(body)
                        except (ValueError, UnicodeError):
                            raise ProtocolError("Invalid JSON response") from None
                        if not isinstance(value, dict):
                            raise ProtocolError("Expected a JSON object")
                        yield value
            parser.finish()
            if status != "0":
                raise ProtocolError("Response ended without a successful grpc-status")

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        values = [value async for value in self.stream(method, payload)]
        if len(values) != 1:
            raise ProtocolError("Unary RPC must contain exactly one message")
        return values[0]

    async def close(self) -> None:
        await self.http.aclose()
