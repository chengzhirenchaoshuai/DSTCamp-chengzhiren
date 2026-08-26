"""DSTCamp GUI、冻结版 Worker 与发布冒烟测试入口。"""

import sys
import os

# 允许从 scripts 目录直接执行。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
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

    from dstools.gui.app import main

    main()
