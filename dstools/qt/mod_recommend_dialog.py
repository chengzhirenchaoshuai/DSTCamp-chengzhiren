""""订阅推荐模组"引导弹窗（对应 Tk 版 ModManagerTab._open_recommend_mods）。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from dstools.features.mod.parser import is_mod_subscribed
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.theme import theme
from dstools.shared.resource_paths import bundled_resource_dir

RECOMMENDED_MODS = [
    ("3444078585", "DontStarveLuaJit2", "LuaJIT 性能补丁，大幅降低卡顿"),
    ("2941527805", "Chinese++ Pro", "汉化其它模组的名称与配置项，Mod 列表和设置直接显示中文"),
    ("2998347052", "Say about your ping(Server)", "显示 Ping、网络与服务器性能及丢包率，并支持聊天播报"),
]


class RecommendModsDialog(QDialog):
    def __init__(self, page):
        super().__init__(page.window())
        self.page = page
        self.setWindowTitle(t("mod.recommend_title"))
        self.setMinimumWidth(dialogs.DIALOG_WIDTHS["lg"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*dialogs.DIALOG_MARGINS)
        layout.setSpacing(dialogs.DIALOG_SPACING)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(420)
        area.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setAutoFillBackground(False)
        inner_layout = QVBoxLayout(inner)
        icon_dir = bundled_resource_dir() / "icons" / "recommended"

        for wid, name, desc in RECOMMENDED_MODS:
            row = QHBoxLayout()
            icon_path = icon_dir / f"{wid}.png"
            if icon_path.exists():
                icon_label = QLabel()
                icon_label.setPixmap(QPixmap(str(icon_path)).scaled(
                    64, 64, aspectMode=Qt.AspectRatioMode.KeepAspectRatio))
                row.addWidget(icon_label)
            text_col = QVBoxLayout()
            name_row = QHBoxLayout()
            name_label = QLabel(name)
            name_label.setFont(theme.font("FONT_SIZE_LG", bold=True))
            name_row.addWidget(name_label)
            name_row.addStretch()
            if is_mod_subscribed(wid):
                subscribed = QLabel(t("mod.recommend_subscribed"))
                subscribed.setProperty("muted", True)
                name_row.addWidget(subscribed)
            else:
                sub_btn = QPushButton(t("mod.recommend_subscribe"))
                sub_btn.clicked.connect(lambda _c=False, w=wid: page._on_link(f"workshop-{w}"))
                name_row.addWidget(sub_btn)
            text_col.addLayout(name_row)
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            desc_label.setProperty("muted", True)
            text_col.addWidget(desc_label)
            row.addLayout(text_col, 1)
            inner_layout.addLayout(row)
        inner_layout.addStretch()
        area.setWidget(inner)
        layout.addWidget(area)
        close_btn = dialogs.style_button(QPushButton(t("dlg.close_btn")), "secondary")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
