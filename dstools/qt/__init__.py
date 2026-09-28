"""DSTCamp 的 Qt (PySide6) 界面层，逐页替换 Tk 版（dstools/gui + features/*/tab.py）。

业务代码（解析、Lua 沙箱、SSH、frp 等）不动，两套界面共用；Qt 层只负责显示与交互。
入口：``python -m dstools.qt.app``。
"""
