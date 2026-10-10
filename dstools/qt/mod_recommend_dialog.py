"""订阅推荐 Mod 的引导弹窗。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from dstools.features.mod.parser import is_mod_subscribed
from dstools.i18n import t
from dstools.qt import dialogs
from dstools.qt.mod_panel import _DEFAULT_ICON_PATH
from dstools.qt.theme import theme
from dstools.qt.widgets import Card
from dstools.shared.resource_paths import bundled_resource_dir

RECOMMENDED_MODS = [
    ("3444078585", "DontStarveLuaJit2", "LuaJIT 性能补丁，大幅降低卡顿"),
    ("2941527805", "Chinese++ Pro", "汉化其它模组的名称与配置项，Mod 列表和设置直接显示中文"),
    ("2998347052", "Say about your ping(Server)", "显示 Ping、网络与服务器性能及丢包率，并支持聊天播报"),
]

_ICON = 56  # 图标边长（逻辑像素）


class RecommendModsDialog(dialogs.Dialog):
    """每个推荐 Mod 一张圆角卡片：图标、名称、简介，右侧为订阅按钮或"已订阅"标记。"""

    def __init__(self, page):
        super().__init__(page.window(), t("mod.recommend_title"), width="lg")
        self.page = page
        inner = QWidget()
        inner.setObjectName("recommendInner")
        inner.setAutoFillBackground(False)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(10)
        icon_dir = bundled_resource_dir() / "icons" / "recommended"
        for wid, name, desc in RECOMMENDED_MODS:
            inner_layout.addWidget(self._mod_card(wid, name, desc, icon_dir / f"{wid}.png"))
        inner_layout.addStretch()

        area = QScrollArea()
        area.setObjectName("recommendArea")
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidgetResizable(True)
        # 视口和内层都不自绘底色，否则会盖出一块灰色底
        area.viewport().setAutoFillBackground(False)
        area.setStyleSheet("#recommendArea, #recommendInner { background: transparent; border: none; }")
        area.setWidget(inner)
        self.body.addWidget(area, 1)

        close_btn = dialogs.style_button(QPushButton(t("dlg.close_btn")), "secondary")
        close_btn.clicked.connect(self.accept)
        self.add_footer(right=[close_btn])
        dialogs.fit_to_screen(self, dialogs.DIALOG_WIDTHS["lg"], 120 + 92 * len(RECOMMENDED_MODS))

    def _mod_card(self, wid: str, name: str, desc: str, icon_path) -> QWidget:
        card = Card(radius=12, alpha=170)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(14)

        icon = QLabel()
        icon.setFixedSize(_ICON, _ICON)
        pixmap = self._icon_pixmap(icon_path if icon_path.exists() else _DEFAULT_ICON_PATH)
        if pixmap is not None:
            icon.setPixmap(pixmap)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(4)
        name_label = QLabel(name)
        name_label.setFont(theme.font("FONT_SIZE_MD", bold=True))
        name_label.setWordWrap(True)
        text.addWidget(name_label)
        desc_label = QLabel(desc)
        desc_label.setFont(theme.font("FONT_SIZE_SM"))
        desc_label.setWordWrap(True)
        desc_label.setProperty("muted", True)
        text.addWidget(desc_label)
        row.addLayout(text, 1)

        if is_mod_subscribed(wid):
            subscribed = QLabel("✓ " + t("mod.recommend_subscribed"))
            subscribed.setFont(theme.font("FONT_SIZE_SM", bold=True))
            subscribed.setStyleSheet(f"color: {theme.hex('SUCCESS')};")
            row.addWidget(subscribed, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            sub_btn = QPushButton(t("mod.recommend_subscribe"))
            sub_btn.clicked.connect(lambda _c=False, w=wid: self.page._on_link(f"workshop-{w}"))
            row.addWidget(sub_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _icon_pixmap(self, path) -> QPixmap | None:
        source = QPixmap(str(path))
        if source.isNull():
            return None
        # 按屏幕缩放比缩到物理像素再设 devicePixelRatio，避免被二次放大发虚
        dpr = self.devicePixelRatioF()
        side = round(_ICON * dpr)
        pixmap = source.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(dpr)
        return pixmap
