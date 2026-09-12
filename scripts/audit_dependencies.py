"""Emit installed third-party pins; Unilark itself is local, not a PyPI dependency."""

from importlib.metadata import distributions

for name, version in sorted(
    (d.metadata["Name"], d.version)
    for d in distributions()
    if d.metadata["Name"].lower().replace("_", "-") != "unilark"
):
    print(f"{name}=={version}")
