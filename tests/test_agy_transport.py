from __future__ import annotations

import struct
from pathlib import Path

import httpx
import pytest

from unilark.adapters.sidecars.agy.transport import (
    Endpoint,
    Frames,
    Process,
    ProtocolError,
    RpcError,
    Transport,
    encode,
    family,
    select_process,
)


def trailer(status: str = "0") -> bytes:
    body = f"grpc-status: {status}\r\n".encode()
    return struct.pack(">BI", 128, len(body)) + body


def test_fragments_are_reassembled_and_truncation_is_rejected() -> None:
    wire = encode({"value": "你好"}) + trailer()
    parser = Frames()
    frames = []
    for byte in wire:
        frames.extend(parser.feed(bytes([byte])))
    parser.finish()
    assert len(frames) == 2
    parser.feed(b"\x00\x00")
    with pytest.raises(ProtocolError, match="Truncated"):
        parser.finish()


@pytest.mark.parametrize("flag,length", [(1, 2), (129, 2), (0, 33 * 1024 * 1024)])
def test_rejects_unsupported_or_oversized_frames(flag: int, length: int) -> None:
    with pytest.raises(ProtocolError):
        Frames().feed(struct.pack(">BI", flag, length))


@pytest.mark.parametrize(
    ("body", "headers", "error"),
    [
        (b"", {"grpc-status": "2"}, RpcError),
        (encode({}), {}, ProtocolError),
        (encode({})[:-1], {"grpc-status": "0"}, ProtocolError),
        (encode({}) + trailer("2"), {}, RpcError),
        (encode({}) + trailer() + encode({}), {}, ProtocolError),
    ],
)
async def test_unary_rejects_false_success(
    body: bytes,
    headers: dict[str, str],
    error: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = Transport(Path("/unused"), Path("/unused-profile"))

    async def endpoint() -> Endpoint:
        return Endpoint(1234, "secret-fixture", Process(1, 0, "123", ("agy",)))

    monkeypatch.setattr(transport, "current", endpoint)
    await transport.http.aclose()
    transport.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body, headers=headers),
        )
    )
    try:
        with pytest.raises(error):
            await transport.call("GetCapabilities", {})
    finally:
        await transport.close()


def test_instances_with_identical_native_ids_cannot_cross_profiles(tmp_path: Path) -> None:
    executable = tmp_path / "agy"
    selected = Process(10, 1, "1", (str(executable), f"--user-data-dir={tmp_path}/a"))
    other = Process(20, 1, "2", (str(executable), f"--user-data-dir={tmp_path}/b"))
    child = Process(11, 10, "3", ("language_server",))
    table = {p.pid: p for p in (selected, other, child)}
    assert select_process(table, executable, tmp_path / "a") == selected
    assert family(table, selected.pid) == {10, 11}
    assert "secret" not in repr(Endpoint(1234, "secret", selected))
    table[30] = Process(30, 1, "4", selected.args)
    with pytest.raises(ProtocolError, match="exactly one"):
        select_process(table, executable, tmp_path / "a")
