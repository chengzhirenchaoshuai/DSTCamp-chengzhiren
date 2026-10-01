"""五套颜色主题的调色板数据——纯数据模块，不依赖任何界面库。

Tk 版（shared/gui/theme.py）和 Qt 版（qt/theme.py）共用这一份，新增主题/改色
只改这里。键的含义见 shared/gui/theme.py 顶部注释（WINDOW_ALPHA/FONT_FAMILY/
FONT_SIZE_*/CARD_RADIUS/CARD_MARGIN 等布局常量各套主题保持一致，只有调色板相
关键按主题取值）。
"""

THEMES = {
    "gray": {
        "PRIMARY": "#8A97A3", "PRIMARY_DARK": "#6B7A87", "PRIMARY_LIGHT": "#E7ECEF",
        "BG_SOFT": "#F4F6F7", "ACCENT": "#5C7A89", "TEXT": "#2E3438", "TEXT_MUTED": "#6E7880",
        "CARD_BG": "#FDFEFE", "CARD_BG_ALT": "#EFF3F4", "CARD_BORDER": "#D6DEE1",
        "SHADOW": "#C9D3D6", "ERROR": "#c62828", "SUCCESS": "#2E7D32", "HEADING": "#33393D",
        "BANNER_BG": "#fff3cd", "BANNER_TEXT": "#856404",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "mint": {
        "PRIMARY": "#6FCF97", "PRIMARY_DARK": "#57BF84", "PRIMARY_LIGHT": "#D7F5E4",
        "BG_SOFT": "#E8F8F0", "ACCENT": "#2D9CDB", "TEXT": "#2F3E46", "TEXT_MUTED": "#6B7C82",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#F4FBF7", "CARD_BORDER": "#CFEEDD",
        "SHADOW": "#C9E4D8", "ERROR": "#c62828", "SUCCESS": "#2E7D32", "HEADING": "#37474f",
        "BANNER_BG": "#fff3cd", "BANNER_TEXT": "#856404",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "twilight": {
        "PRIMARY": "#5B8DEF", "PRIMARY_DARK": "#3F6FD1", "PRIMARY_LIGHT": "#D9E6FB",
        "BG_SOFT": "#EEF3FC", "ACCENT": "#2D9CDB", "TEXT": "#2A3342", "TEXT_MUTED": "#6B7785",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#F5F8FE", "CARD_BORDER": "#D3E1FA",
        "SHADOW": "#C7D6F0", "ERROR": "#c62828", "SUCCESS": "#2E7D32", "HEADING": "#33415C",
        "BANNER_BG": "#fdecc8", "BANNER_TEXT": "#7a5a12",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "campfire": {
        "PRIMARY": "#E8A33D", "PRIMARY_DARK": "#C9822A", "PRIMARY_LIGHT": "#FBE7C6",
        "BG_SOFT": "#FBF2E3", "ACCENT": "#D9534F", "TEXT": "#4A3728", "TEXT_MUTED": "#8A7862",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#FDF6EC", "CARD_BORDER": "#F0DBB4",
        "SHADOW": "#E8D2A0", "ERROR": "#c62828", "SUCCESS": "#2E7D32", "HEADING": "#6B4A28",
        "BANNER_BG": "#fde3df", "BANNER_TEXT": "#a3392f",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
    "sakura": {
        "PRIMARY": "#F27CA0", "PRIMARY_DARK": "#D85F87", "PRIMARY_LIGHT": "#FBD9E4",
        "BG_SOFT": "#FFF0F5", "ACCENT": "#9B6FB3", "TEXT": "#4A2E39", "TEXT_MUTED": "#9C7C89",
        "CARD_BG": "#FFFFFF", "CARD_BG_ALT": "#FFF5F8", "CARD_BORDER": "#F5C4D3",
        "SHADOW": "#F0B8CB", "ERROR": "#c62828", "SUCCESS": "#2E7D32", "HEADING": "#7A3B54",
        "BANNER_BG": "#fff3cd", "BANNER_TEXT": "#856404",
        "WINDOW_ALPHA": 1.0, "FONT_FAMILY": "Microsoft YaHei UI Light", "CARD_RADIUS": 34,
        "CARD_MARGIN": 24,
        "FONT_SIZE_XL": 18, "FONT_SIZE_LG": 15, "FONT_SIZE_MD": 12,
        "FONT_SIZE_BASE": 11, "FONT_SIZE_SM": 10, "FONT_SIZE_XS": 9,
    },
}
THEME_NAMES = ["gray", "mint", "twilight", "campfire", "sakura"]  # 菜单里出现的顺序
