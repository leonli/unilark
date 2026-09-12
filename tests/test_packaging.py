"""打包与入口点的自检。

这组用例的由来：``pyproject.toml`` 早先声明了 ``unilark = "unilark.cli:main"``，
但 ``cli.py`` 根本不存在——装出来的包会给一个一跑就 ImportError 的命令，而单元测试
全绿。声明和实现脱节是这类骨架最容易出的错，所以把它钉死在测试里。
"""

from __future__ import annotations

import importlib
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def _declared_entry_points() -> dict[str, str]:
    """从 pyproject 里读 ``[project.scripts]``。

    用 tomllib 而不是正则：3.11+ 标准库自带，没有新增依赖的理由去装 toml 解析器。
    """
    import tomllib

    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data.get("project", {}).get("scripts", {})


def test_declared_console_scripts_resolve() -> None:
    scripts = _declared_entry_points()
    assert scripts, "pyproject 里没有声明任何 console script"
    for name, target in scripts.items():
        module_path, _, attr = target.partition(":")
        module = importlib.import_module(module_path)
        assert callable(getattr(module, attr, None)), (
            f"入口点 {name} 指向 {target}，但该对象不存在或不可调用"
        )


def test_cli_version_matches_package() -> None:
    from unilark import __version__
    from unilark.cli import main

    assert main([]) == 0
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), "版本号格式不对"


def test_cli_runs_as_module() -> None:
    """真的起一个进程跑一遍。

    直接 import 调用会漏掉 ``__main__`` 路径和 argparse 的 exit 行为，而用户敲的是
    命令不是函数。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "unilark.cli", "doctor"],
        capture_output=True,
        text=True,
        cwd=REPO / "src",
        check=False,
    )
    assert proc.returncode == 2, proc.stderr
    assert "M1" in proc.stdout
    assert "闭环未验收" in proc.stdout


def test_no_placeholder_implementations() -> None:
    """PRD 8.4：不预先写空实现。

    骨架期最大的风险是「摆一堆 pass 充数」，后面没人记得哪些是真的。这里不允许出现
    裸 ``pass`` 的函数体——契约用 Protocol + ``...`` 表达，那是类型声明，不是实现。
    """
    offenders = []
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"^([ \t]+)pass\s*$", text, re.M):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO)}:{line}")
    assert not offenders, f"发现占位实现: {offenders}"
