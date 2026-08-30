"""DSTCamp GUI、冻结版 Worker 与发布冒烟测试入口。"""

import sys
import os
import subprocess
import time

# 允许从 scripts 目录直接执行。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _wait_for_process_exit(pid: int) -> None:
    """等待旧 GUI 退出并释放单实例锁；辅助进程不参与 GUI 初始化。"""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        infinite = 0xFFFFFFFF
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            try:
                kernel32.WaitForSingleObject(handle, infinite)
            finally:
                kernel32.CloseHandle(handle)
        return
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def _run_restart_helper(parent_pid: int, original_args: list[str]) -> None:
    _wait_for_process_exit(parent_pid)
    restart_env = os.environ.copy()
    if getattr(sys, "frozen", False):
        command = [sys.executable, *original_args]
        # 最终 GUI 需要比本辅助进程活得更久，同样不能复用辅助进程的
        # onefile 解压目录。两层启动都设置该变量，分别得到独立 _MEI。
        restart_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        command = [sys.executable, os.path.abspath(__file__), *original_args]
    subprocess.Popen(command, env=restart_env)

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--restart-helper":
        _run_restart_helper(int(sys.argv[2]), sys.argv[3:])
        raise SystemExit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--smoke-test":
        from dstools.features.mod._sandbox_worker import run_worker_main
        from dstools.features.mod.workshop_worker import main as workshop_worker_main
        from dstools.gui.app import DSToolsApp
        from dstools.shared.resource_paths import bundled_resource_dir, tool_binary_dir

        required = (
            bundled_resource_dir() / "icons" / "app" / "icon.png",
            bundled_resource_dir() / "icons" / "ui" / "mod_icon_default.png",
            tool_binary_dir() / "ktools" / "ktech.exe",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise SystemExit(f"缺少发布资源：{missing}")
        assert DSToolsApp and run_worker_main and workshop_worker_main
        raise SystemExit(0)

    # onefile 子进程通过参数复用当前 EXE，必须在导入 GUI 前分流。
    if len(sys.argv) > 1 and sys.argv[1] == "--lua-sandbox-worker":
        # 包装入口会把预期的 Mod 沙箱失败转成安静的非零退出。
        from dstools.features.mod._sandbox_worker import run_worker_main

        run_worker_main()
        sys.exit(0)

    # 普通 SteamAPI_Init 会把宿主进程登记成《饥荒：联机版》(322330)。
    # Workshop 查询/更新必须在短生命周期 Worker 中运行，完成即退出，
    # 否则长期存活的 GUI 会让 Steam 一直显示游戏正在运行。
    if len(sys.argv) > 1 and sys.argv[1] == "--dstcamp-workshop-worker":
        from dstools.features.mod.workshop_worker import main as workshop_worker_main

        raise SystemExit(workshop_worker_main(sys.argv[2:]))

    # 只有普通 GUI 入口启用单实例；上面的 Worker 分支必须允许 onefile EXE
    # 自己启动短生命周期子进程，不应被 GUI 的 Mutex 拦截。
    from dstools.shared.single_instance import acquire_gui_instance

    gui_instance = acquire_gui_instance()
    if gui_instance is None:
        raise SystemExit(0)

    from dstools.gui.app import main

    try:
        main()
    finally:
        gui_instance.close()
