"""Workspace projects verified on the desktop's ReadProject/CreateProject RPCs."""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from unilark.adapters.sidecars.agy.transport import ProtocolError, RpcError, Transport


def directory(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("工作目录必须是已存在的绝对目录。")
    return path.resolve()


async def read_workspace(transport: Transport, project_id: str) -> str:
    response = await transport.call("ReadProject", {"id": project_id})
    project = response.get("project", {})
    resources = project.get("projectResources", {}).get("resources", [])
    if response.get("notFoundOnDisk") or project.get("id") != project_id or len(resources) != 1:
        raise ProtocolError("Expected an existing single-directory project")
    uri = urlparse(resources[0].get("folderUri", ""))
    if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
        raise ProtocolError("Project is not a verified local workspace")
    return str(directory(unquote(uri.path)))


async def project_for(transport: Transport, path: str) -> str:
    workspace = directory(path)
    project_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, str(transport.user_data.resolve()) + "\n" + str(workspace))
    )
    response = await transport.call("ReadProject", {"id": project_id})
    if not response.get("project"):
        try:
            await transport.call(
                "CreateProject",
                {
                    "project": {
                        "id": project_id,
                        # AGY enforces globally unique project names, including other folders.
                        "name": f"{workspace.name[:64] or 'workspace'}-Unilark-{project_id}",
                        "projectResources": {"resources": [{"folderUri": workspace.as_uri()}]},
                        "isWorkspaceOnly": True,
                    }
                },
            )
        except RpcError as error:
            if error.status != "6":
                raise
            # A concurrent creator may have succeeded. Read the exact ID and path;
            # this never retries the write or guesses from an error alone.
    if await read_workspace(transport, project_id) != str(workspace):
        raise ProtocolError("Existing project workspace differs; refusing to rebind")
    return project_id
