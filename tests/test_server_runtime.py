"""开服模式（server_runtime）规则与"唯一来源"守护测试。"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 设置文件放进临时目录，绝不读写真实 %APPDATA%/DSTCamp。
_SETTINGS_TMP = tempfile.TemporaryDirectory()
os.environ["APPDATA"] = _SETTINGS_TMP.name

from dstools.features.local_service import dedicated_server, server_runtime  # noqa: E402
from dstools.features.local_service.server_runtime import (  # noqa: E402
    RuntimeKind, RuntimeMode, resolve_runtime, set_runtime_mode,
)
from dstools.shared import app_settings  # noqa: E402


def _make_install(common: Path, name: str, *, client: bool) -> Path:
    bin64 = common / name / "bin64"
    bin64.mkdir(parents=True)
    (bin64 / "dontstarve_dedicated_server_nullrenderer_x64.exe").write_bytes(b"")
    if client:
        (bin64 / "dontstarve_steam_x64.exe").write_bytes(b"")
    return common / name


def test_mode_resolution_and_migration() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        library = Path(tmp)
        common = library / "steamapps" / "common"
        client = _make_install(common, "Don't Starve Together", client=True)
        libraries = [library]
        with patch.object(server_runtime, "find_all_steam_libraries", return_value=libraries), \
                patch.object(dedicated_server, "find_all_steam_libraries", return_value=libraries):
            # 只有客户端：自动模式用客户端；指定独立专服时如实报告未找到，不偷偷换成客户端。
            auto = resolve_runtime(RuntimeMode.AUTO)
            assert auto.runtime.kind is RuntimeKind.CLIENT and auto.runtime.install_dir == client
            assert auto.runtime.app_id == "322330" and auto.runtime.mods_dir == client / "mods"
            assert resolve_runtime(RuntimeMode.DEDICATED).runtime is None

            # 两者都在：自动优先独立专服；指定客户端时用客户端。
            dedicated = _make_install(common, "Don't Starve Together Dedicated Server", client=False)
            assert resolve_runtime(RuntimeMode.AUTO).runtime.install_dir == dedicated
            assert resolve_runtime(RuntimeMode.AUTO).runtime.app_id == "343050"
            assert resolve_runtime(RuntimeMode.CLIENT).runtime.install_dir == client

            # 旧版本把客户端目录存在专服路径里：迁移成客户端路径 + 客户端模式。
            app_settings.set_dedicated_server_path(client)
            assert resolve_runtime().runtime.install_dir == client
            assert app_settings.get_dedicated_server_path() is None
            assert app_settings.get_client_runtime_path() == client
            assert app_settings.get_server_runtime_mode() == RuntimeMode.CLIENT.value

            # 已明确选过模式时，迁移只挪路径不改模式。
            set_runtime_mode(RuntimeMode.DEDICATED)
            app_settings.set_dedicated_server_path(client)
            assert resolve_runtime().runtime.install_dir == dedicated
            assert app_settings.get_server_runtime_mode() == RuntimeMode.DEDICATED.value


# 业务代码只能经 server_runtime 取开服程序目录。白名单：
# - dedicated_server / server_runtime：探测函数自身；
# - legacy_v1.discover_legacy_runtime_roots：清理残留时要扫遍所有可能展开过 V1 的目录；
# - parser.is_dedicated_server_mods_dir：只做"是不是专服 mods"的路径比较。
_ALLOWED = {
    "find_dedicated_server_dir(": {"dedicated_server.py", "server_runtime.py", "legacy_v1.py"},
    "get_dedicated_server_path(": {"dedicated_server.py", "server_runtime.py", "parser.py", "app_settings.py"},
    "get_client_runtime_path(": {"server_runtime.py", "app_settings.py"},
}
# 旧 Tk 版不再维护，不受约束。
_LEGACY_TK = ("dstools/gui/", "dstools/features/mod/tab.py", "dstools/features/local_service/tab.py")


def test_runtime_single_source() -> None:
    offenders = []
    for path in (PROJECT_ROOT / "dstools").rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel.startswith(_LEGACY_TK):
            continue
        text = path.read_text(encoding="utf-8")
        for call, allowed in _ALLOWED.items():
            if path.name in allowed:
                continue
            if re.search(r"(?<![\w.])" + re.escape(call) + r"|\." + re.escape(call), text):
                offenders.append(f"{rel}: {call}")
    assert not offenders, "绕过 server_runtime 直接探测开服目录：\n" + "\n".join(offenders)


def main() -> None:
    for test in (test_mode_resolution_and_migration, test_runtime_single_source):
        test()
        print(f"PASS: {test.__name__}")


if __name__ == "__main__":
    main()
