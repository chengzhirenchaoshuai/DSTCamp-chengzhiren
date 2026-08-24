"""Steam Workshop 双后端探针。

这个脚本验证 Steam 客户端和 DST 自带 steam_api DLL 是否能在独立进程
中初始化、读取 Workshop 状态；只有显式传入 ``--download`` 才会请求
Steam 更新指定项目，不修改 modoverrides.lua，也不会调用
SteamAPI_RestartAppIfNecessary。``--game-server`` 用专服上下文探测
SteamGameServerUGC，普通模式则使用 SteamUGC。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path


REQUIRED_EXPORTS = (
    "SteamAPI_Init",
    "SteamAPI_Shutdown",
    "SteamAPI_RunCallbacks",
    "SteamAPI_IsSteamRunning",
    "SteamAPI_SteamUGC_v015",
    "SteamAPI_ISteamUGC_GetItemState",
    "SteamAPI_ISteamUGC_DownloadItem",
    "SteamAPI_ISteamUGC_GetItemDownloadInfo",
    "SteamAPI_ISteamUGC_GetItemInstallInfo",
)

GAME_SERVER_EXPORTS = (
    "SteamInternal_GameServer_Init",
    "SteamAPI_SteamGameServer_v013",
    "SteamAPI_ISteamGameServer_LogOnAnonymous",
    "SteamAPI_ISteamGameServer_BLoggedOn",
    "SteamGameServer_Shutdown",
    "SteamGameServer_RunCallbacks",
    "SteamAPI_SteamGameServerUGC_v015",
    "SteamAPI_ISteamUGC_BInitWorkshopForGameServer",
)


def _load_dll(path: Path):
    """在 DLL 所在目录加载 Steam API，避免依赖当前工作目录。"""
    if sys.platform != "win32":
        raise RuntimeError("Steam Workshop 探针当前只支持 Windows")
    if not path.is_file():
        raise FileNotFoundError(f"找不到 Steam API DLL：{path}")
    add_dll_directory = getattr(os, "add_dll_directory", None)
    dll_dir = add_dll_directory(str(path.parent)) if add_dll_directory else None
    try:
        return ctypes.WinDLL(str(path))
    finally:
        # WinDLL 已经完成加载；句柄保持有效，目录句柄可以关闭。
        if dll_dir is not None:
            dll_dir.close()


def _init_from_dll_directory(path: Path, initializer) -> bool:
    """Steam API 从 DLL 同目录读取 steam_appid.txt，探针结束后恢复 cwd。"""
    previous = os.getcwd()
    try:
        os.chdir(str(path.parent))
        return bool(initializer())
    finally:
        os.chdir(previous)


def probe(path: Path, seconds: float, workshop_id: int | None = None,
          request_download: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "dll": str(path),
        "architecture": "64-bit" if path.name.lower().endswith("64.dll") else "unknown",
        "steam_running": None,
        "exports": {},
        "initialized": False,
        "callback_pump_seconds": seconds,
    }
    dll = _load_dll(path)

    exports: dict[str, bool] = {}
    for name in REQUIRED_EXPORTS:
        exports[name] = hasattr(dll, name)
    result["exports"] = exports
    missing = [name for name, present in exports.items() if not present]
    if missing:
        result["error"] = "缺少 Steam API 导出：" + ", ".join(missing)
        return result

    dll.SteamAPI_IsSteamRunning.restype = ctypes.c_bool
    result["steam_running"] = bool(dll.SteamAPI_IsSteamRunning())

    dll.SteamAPI_Init.restype = ctypes.c_bool
    initialized = _init_from_dll_directory(path, dll.SteamAPI_Init)
    result["initialized"] = initialized
    if not initialized:
        result["error"] = (
            "SteamAPI_Init 返回失败；通常表示当前独立进程没有获得 DST 的 Steam "
            "应用上下文，不能据此执行 ISteamUGC 下载。"
        )
        return result

    try:
        if workshop_id is not None:
            dll.SteamAPI_SteamUGC_v015.restype = ctypes.c_void_p
            ugc = dll.SteamAPI_SteamUGC_v015()
            if not ugc:
                result["error"] = "SteamAPI_SteamUGC_v015 返回空接口指针"
                return result
            dll.SteamAPI_ISteamUGC_GetItemState.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
            dll.SteamAPI_ISteamUGC_GetItemState.restype = ctypes.c_uint32
            state = int(dll.SteamAPI_ISteamUGC_GetItemState(ugc, workshop_id))
            result["workshop_id"] = str(workshop_id)
            result["item_state_flags"] = state
            result["item_state"] = {
                "subscribed": bool(state & (1 << 0)),
                "legacy_item": bool(state & (1 << 1)),
                "installed": bool(state & (1 << 2)),
                "needs_update": bool(state & (1 << 3)),
                "downloading": bool(state & (1 << 4)),
                "download_pending": bool(state & (1 << 5)),
                "downloaded": bool(state & (1 << 6)),
            }
            if request_download:
                dll.SteamAPI_ISteamUGC_DownloadItem.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint64, ctypes.c_bool,
                ]
                dll.SteamAPI_ISteamUGC_DownloadItem.restype = ctypes.c_bool
                accepted = bool(dll.SteamAPI_ISteamUGC_DownloadItem(
                    ugc, workshop_id, True))
                result["download_requested"] = accepted
                if not accepted:
                    result["download_error"] = "ISteamUGC::DownloadItem 返回 false"

        # Steamworks 回调必须持续泵送；探针额外轮询状态和进度，便于在没有
        # 原生 SDK 回调类的情况下确认下载是否已经落盘。
        dll.SteamAPI_RunCallbacks.restype = None
        deadline = time.monotonic() + max(0.0, seconds)
        last_state = None
        while time.monotonic() < deadline:
            dll.SteamAPI_RunCallbacks()
            if workshop_id is not None and request_download:
                state = int(dll.SteamAPI_ISteamUGC_GetItemState(ugc, workshop_id))
                if state != last_state:
                    result["last_item_state_flags"] = state
                    last_state = state
                dll.SteamAPI_ISteamUGC_GetItemDownloadInfo.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint64,
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
                ]
                dll.SteamAPI_ISteamUGC_GetItemDownloadInfo.restype = ctypes.c_bool
                downloaded = ctypes.c_uint64()
                total = ctypes.c_uint64()
                if dll.SteamAPI_ISteamUGC_GetItemDownloadInfo(
                        ugc, workshop_id, ctypes.byref(downloaded), ctypes.byref(total)):
                    result["downloaded_bytes"] = int(downloaded.value)
                    result["total_bytes"] = int(total.value)
            time.sleep(0.05)
        result["callback_pump"] = "ok"
    finally:
        dll.SteamAPI_Shutdown()
    return result


def probe_game_server(path: Path, seconds: float, depot_id: int | None = None,
                      workshop_folder: Path | None = None,
                      workshop_id: int | None = None,
                      request_download: bool = False) -> dict[str, object]:
    """验证专服 SteamGameServer API 和 GameServerUGC 接口。"""
    result: dict[str, object] = {
        "dll": str(path),
        "mode": "game_server",
        "initialized": False,
        "callback_pump_seconds": seconds,
    }
    dll = _load_dll(path)
    exports = {name: hasattr(dll, name) for name in GAME_SERVER_EXPORTS}
    result["exports"] = exports
    missing = [name for name, present in exports.items() if not present]
    if missing:
        result["error"] = "缺少 GameServer API 导出：" + ", ".join(missing)
        return result

    # Steamworks 文档中的 SteamGameServer_Init 参数：IP、Steam 端口、
    # 游戏端口、查询端口、ServerMode、版本字符串。探针使用 0 端口和
    # no-auth 模式，只验证接口初始化，不占用 DST 实际服务器端口。
    dll.SteamInternal_GameServer_Init.argtypes = [
        ctypes.c_uint32, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint16,
        ctypes.c_int, ctypes.c_char_p,
    ]
    dll.SteamInternal_GameServer_Init.restype = ctypes.c_bool
    initialized = _init_from_dll_directory(
        path, lambda: dll.SteamInternal_GameServer_Init(
            0, 0, 0, 0, 1, b"dstcamp-probe"))
    result["initialized"] = initialized
    if not initialized:
        result["error"] = "SteamInternal_GameServer_Init 返回失败"
        return result

    try:
        dll.SteamAPI_SteamGameServer_v013.restype = ctypes.c_void_p
        game_server = dll.SteamAPI_SteamGameServer_v013()
        result["game_server_interface"] = bool(game_server)
        if game_server:
            dll.SteamAPI_ISteamGameServer_LogOnAnonymous.argtypes = [ctypes.c_void_p]
            dll.SteamAPI_ISteamGameServer_LogOnAnonymous.restype = None
            dll.SteamAPI_ISteamGameServer_LogOnAnonymous(game_server)
            dll.SteamAPI_ISteamGameServer_BLoggedOn.argtypes = [ctypes.c_void_p]
            dll.SteamAPI_ISteamGameServer_BLoggedOn.restype = ctypes.c_bool
            login_deadline = time.monotonic() + min(8.0, max(0.0, seconds))
            while time.monotonic() < login_deadline:
                dll.SteamGameServer_RunCallbacks()
                if dll.SteamAPI_ISteamGameServer_BLoggedOn(game_server):
                    break
                time.sleep(0.05)
        dll.SteamAPI_SteamGameServerUGC_v015.restype = ctypes.c_void_p
        ugc = dll.SteamAPI_SteamGameServerUGC_v015()
        result["ugc_interface"] = bool(ugc)
        if not ugc:
            result["error"] = "SteamAPI_SteamGameServerUGC_v015 返回空接口指针"
            return result
        if depot_id is not None and workshop_folder is not None:
            workshop_folder.mkdir(parents=True, exist_ok=True)
            dll.SteamAPI_ISteamUGC_BInitWorkshopForGameServer.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ]
            dll.SteamAPI_ISteamUGC_BInitWorkshopForGameServer.restype = ctypes.c_bool
            result["workshop_folder"] = str(workshop_folder)
            result["workshop_depot_id"] = depot_id
            result["workshop_initialized"] = bool(
                dll.SteamAPI_ISteamUGC_BInitWorkshopForGameServer(
                    ugc, depot_id, str(workshop_folder).encode("utf-8")))
        if workshop_id is not None:
            dll.SteamAPI_ISteamUGC_GetItemState.argtypes = [
                ctypes.c_void_p, ctypes.c_uint64,
            ]
            dll.SteamAPI_ISteamUGC_GetItemState.restype = ctypes.c_uint32
            state = int(dll.SteamAPI_ISteamUGC_GetItemState(ugc, workshop_id))
            result["workshop_id"] = str(workshop_id)
            result["item_state_flags"] = state
            if request_download:
                dll.SteamAPI_ISteamUGC_DownloadItem.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint64, ctypes.c_bool,
                ]
                dll.SteamAPI_ISteamUGC_DownloadItem.restype = ctypes.c_bool
                result["download_requested"] = bool(
                    dll.SteamAPI_ISteamUGC_DownloadItem(ugc, workshop_id, True))
        dll.SteamGameServer_RunCallbacks.restype = None
        deadline = time.monotonic() + max(0.0, seconds)
        last_state = None
        while time.monotonic() < deadline:
            dll.SteamGameServer_RunCallbacks()
            if game_server:
                result["game_server_logged_on"] = bool(
                    dll.SteamAPI_ISteamGameServer_BLoggedOn(game_server))
            if workshop_id is not None and request_download:
                state = int(dll.SteamAPI_ISteamUGC_GetItemState(ugc, workshop_id))
                if state != last_state:
                    result["last_item_state_flags"] = state
                    last_state = state
                dll.SteamAPI_ISteamUGC_GetItemDownloadInfo.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint64,
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
                ]
                dll.SteamAPI_ISteamUGC_GetItemDownloadInfo.restype = ctypes.c_bool
                downloaded = ctypes.c_uint64()
                total = ctypes.c_uint64()
                if dll.SteamAPI_ISteamUGC_GetItemDownloadInfo(
                        ugc, workshop_id, ctypes.byref(downloaded), ctypes.byref(total)):
                    result["downloaded_bytes"] = int(downloaded.value)
                    result["total_bytes"] = int(total.value)
            time.sleep(0.05)
        result["callback_pump"] = "ok"
    finally:
        dll.SteamGameServer_Shutdown()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 DST Steam Workshop API 初始化")
    parser.add_argument("dll", type=Path, help="DST bin64\\steam_api64.dll 的路径")
    parser.add_argument("--seconds", type=float, default=1.0,
                        help="初始化成功后泵送 Steam 回调的秒数")
    parser.add_argument("--workshop-id", type=int,
                        help="可选：读取一个 Workshop 项目的本地 Steam 状态")
    parser.add_argument("--download", action="store_true",
                        help="显式请求 Steam 更新指定 Workshop 项目")
    parser.add_argument("--game-server", action="store_true",
                        help="改为探测 SteamGameServerUGC（可配合 --download 请求下载）")
    parser.add_argument("--workshop-depot-id", type=int,
                        help="配合 --game-server：调用 BInitWorkshopForGameServer")
    parser.add_argument("--workshop-folder", type=Path,
                        help="配合 --workshop-depot-id：指定专服 Workshop 目录")
    args = parser.parse_args()
    try:
        if args.game_server:
            result = probe_game_server(
                args.dll.resolve(), args.seconds,
                args.workshop_depot_id, args.workshop_folder,
                args.workshop_id, args.download,
            )
        else:
            result = probe(args.dll.resolve(), args.seconds, args.workshop_id, args.download)
    except Exception as exc:  # 探针必须把错误转成可读 JSON，而不是堆栈刷屏。
        result = {"dll": str(args.dll), "initialized": False,
                  "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("initialized") else 1


if __name__ == "__main__":
    raise SystemExit(main())
