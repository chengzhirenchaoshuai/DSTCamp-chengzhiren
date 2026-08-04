"""cluster.ini / server.ini 各字段的中英文显示名与功能说明。

内容整理自 Klei 官方文档与社区维基（Don't Starve Wiki 的 Dedicated Servers /
Simple Dedicated Server Setup 指南等），只收录这些资料里有据可查的常见字段。
未收录的字段（例如某些 mod 或非官方修改额外写入的键）原样显示英文键名、不
附加说明——不对未知字段的含义做任何猜测。
"""

from dstools.i18n import get_lang

# (section, key) -> {"zh": (显示名, 说明), "en": (label, description)}
CLUSTER_FIELD_INFO: dict[tuple[str, str], dict[str, tuple[str, str]]] = {
    ("GAMEPLAY", "game_mode"): {
        "zh": ("游戏模式", "决定死亡后的重生方式：survival(生存，变成鬼魂后可在祭坛复活)、"
               "endless(无尽，扣除部分血量后原地重生)、wilderness(荒野，死亡即永久删除角色，无法复活)。"),
        "en": ("Game Mode", "Determines respawn behavior after death: survival (become a ghost, "
               "revivable at an altar), endless (respawn on the spot with a health penalty), "
               "wilderness (permadeath, no revival)."),
    },
    ("GAMEPLAY", "max_players"): {
        "zh": ("最大玩家数", "服务器允许同时在线的玩家数量上限，范围通常为 1-64。"),
        "en": ("Max Players", "Maximum number of players allowed online at once, typically 1-64."),
    },
    ("GAMEPLAY", "pvp"): {
        "zh": ("玩家对战 (PVP)", "是否允许玩家之间互相造成伤害。"),
        "en": ("PvP", "Whether players can damage each other."),
    },
    ("GAMEPLAY", "pause_when_empty"): {
        "zh": ("无人时暂停", "服务器上没有玩家在线时是否暂停世界"),
        "en": ("Pause When Empty", "Whether the world simulation pauses while no players are online."),
    },
    ("GAMEPLAY", "vote_enabled"): {
        "zh": ("允许投票", "是否允许玩家发起投票（踢人、回档、重置世界等）。"),
        "en": ("Voting Enabled", "Whether players can start votes (kick, rollback, regenerate world, etc.)."),
    },
    ("GAMEPLAY", "vote_kick_enabled"): {
        "zh": ("投票踢人", "是否允许玩家发起投票，把其他玩家踢出服务器。"),
        "en": ("Vote Kick Enabled", "Whether players can start a vote to kick another player from the server."),
    },
    ("NETWORK", "cluster_name"): {
        "zh": ("服务器名称", "显示在服务器列表中的名称。"),
        "en": ("Cluster Name", "The name shown for this server in the server browser."),
    },
    ("NETWORK", "cluster_description"): {
        "zh": ("服务器描述", "在服务器列表详情中显示的说明文字。"),
        "en": ("Cluster Description", "Extended description shown when a player selects this server."),
    },
    ("NETWORK", "cluster_password"): {
        "zh": ("服务器密码", "玩家加入服务器需要输入的密码，留空表示无密码、公开加入。"),
        "en": ("Cluster Password", "Password required to join. Leave blank for a public server."),
    },
    ("NETWORK", "cluster_intention"): {
        "zh": ("服务器风格", "服务器列表里的风格标签，可选 cooperative(合作)、competitive(竞争)、"
               "social(社交)、madness(疯狂/无规则)，仅作展示分类用，不影响实际玩法规则。"),
        "en": ("Cluster Intention", "Server-browser style tag: cooperative, competitive, social, or "
               "madness. Display/filtering only -- doesn't change actual gameplay rules."),
    },
    ("NETWORK", "lan_only_cluster"): {
        "zh": ("仅限局域网", "开启后服务器只能被同一局域网内的玩家发现和连接，不会出现在公网服务器列表。"),
        "en": ("LAN Only", "Restricts the server to local-network discovery/connections only -- it "
               "won't appear in the public internet server list."),
    },
    ("NETWORK", "offline_cluster"): {
        "zh": ("离线模式", "开启后不会向 Klei 服务器列表注册，也不支持好友邀请等在线功能，"
               "此时不需要 cluster_token.txt"),
        "en": ("Offline Cluster", "When enabled, the server doesn't register with Klei's server list "
               "or support friend invites/other online features; a cluster_token.txt isn't required."),
    },
    ("NETWORK", "tick_rate"): {
        "zh": ("通信频率", "服务器每秒向客户端发送状态更新的次数，范围 15-60，"
               "数值越高同步越精细但越占用带宽，默认值15"),
        "en": ("Tick Rate", "How many times per second the server sends updates to clients (15-60). "
               "Higher is more precise but uses more bandwidth; Klei's recommended default is 15."),
    },
    ("NETWORK", "whitelist_slots"): {
        "zh": ("白名单预留人数", "为白名单账号预留的玩家席位数量。"),
        "en": ("Whitelist Slots", "Number of player slots reserved for whitelisted accounts."),
    },
    ("NETWORK", "autosaver_enabled"): {
        "zh": ("自动存档", "是否定期自动保存游戏进度。"),
        "en": ("Autosaver Enabled", "Whether the server periodically auto-saves game progress."),
    },
    ("NETWORK", "cluster_language"): {
        "zh": ("服务器语言", "影响服务器内玩家说话台词的语言，默认为英语"),
        "en": ("Cluster Language", "Language used by players in the server, default English"),
    },
    ("NETWORK", "connection_timeout"): {
        "zh": ("连接超时", "客户端连接握手允许的最长等待时间，单位毫秒，默认 8000。"),
        "en": ("Connection Timeout", "Maximum time (ms) allowed for a client connection handshake, "
               "default 8000."),
    },
    ("NETWORK", "idle_timeout"): {
        "zh": ("挂机超时", "玩家无操作多长时间后会被自动踢出，单位秒，默认 1800。"),
        "en": ("Idle Timeout", "How long (seconds) a player can be idle before being auto-kicked, "
               "default 1800."),
    },
    ("NETWORK", "override_dns"): {
        "zh": ("自定义 DNS", "给服务器指定使用的 DNS 地址，没有默认值，一般不需要手动设置。"),
        "en": ("Override DNS", "A custom DNS address for the server to use. No default -- usually "
               "doesn't need to be set manually."),
    },
    ("NETWORK", "cluster_cloud_id"): {
        "zh": ("云端标识符", "游戏自己在服务器首次注册/运行后生成写入的一串内部标识符，具体用途"
               "没有在官方文档中找到明确说明，推测跟 Klei 服务器列表/匹配后台有关。只读展示，"
               "不建议手动修改。"),
        "en": ("Cloud ID", "An internal identifier the game itself generates once the server has "
               "registered/run -- its exact purpose isn't documented anywhere official; likely "
               "related to Klei's server-list/matchmaking backend. Read-only -- editing it manually "
               "isn't recommended."),
    },
    ("MISC", "console_enabled"): {
        "zh": ("启用控制台", "是否允许通过服务器终端执行 Lua 控制台指令。"),
        "en": ("Console Enabled", "Whether Lua console commands can be executed from the server terminal."),
    },
    ("MISC", "max_snapshots"): {
        "zh": ("最大快照数", "每次存档时保留的历史存档快照数量上限，用于回档功能，默认 6。"),
        "en": ("Max Snapshots", "Maximum number of historical save snapshots kept for rollback, default 6."),
    },
    ("SHARD", "shard_enabled"): {
        "zh": ("世界互联", "是否启用主从世界架构（例如需要独立的洞穴世界时必须开启）。"),
        "en": ("Shard Enabled", "Whether the master/slave shard topology is enabled (required for a "
               "separate Caves world, for example)."),
    },
    ("SHARD", "bind_ip"): {
        "zh": ("监听ip", "主世界用于接受其他世界连接的网络地址，通常为 127.0.0.1 或 0.0.0.0。"),
        "en": ("Bind IP", "The address the master shard listens on for other shards' connections, typically 127.0.0.1 or 0.0.0.0."),
    },
    ("SHARD", "master_ip"): {
        "zh": ("主世界ip", "从世界（如洞穴）连接主世界时使用的目标地址。"),
        "en": ("Master IP", "The address secondary shards (e.g. Caves) connect to reach the master."),
    },
    ("SHARD", "master_port"): {
        "zh": ("互联端口", "各世界之间通信使用的 UDP 端口。"),
        "en": ("Master Port", "The UDP port used for inter-shard communication, written by the game "
               "itself; deleting it while shard_enabled stays true causes the server to fail to start."),
    },
    ("SHARD", "cluster_key"): {
        "zh": ("互联密码", "各世界之间相互验证身份用的共享密钥，各世界必须一致。"),
        "en": ("Cluster Key", "Shared secret the shards use to authenticate each other -- must match "),
    },
    ("STEAM", "steam_group_only"): {
        "zh": ("仅限 Steam 群组", "开启后只允许指定 Steam 群组的成员加入服务器，默认关闭。"),
        "en": ("Steam Group Only", "When enabled, only members of the specified Steam group can join. "
               "Default off."),
    },
    ("STEAM", "steam_group_id"): {
        "zh": ("Steam 群组 ID", "配合 steam_group_only/steam_group_admins 使用的 Steam 群组 ID，"
               "没有默认值。"),
        "en": ("Steam Group ID", "The Steam group ID used together with steam_group_only/"
               "steam_group_admins. No default."),
    },
    ("STEAM", "steam_group_admins"): {
        "zh": ("Steam 群组管理员", "开启后，上面指定 Steam 群组的管理员在这个服务器上自动拥有管理权限，"
               "默认关闭。"),
        "en": ("Steam Group Admins", "When enabled, admins of the specified Steam group automatically "
               "get admin privileges on this server. Default off."),
    },
}

SHARD_FIELD_INFO: dict[tuple[str, str], dict[str, tuple[str, str]]] = {
    ("NETWORK", "server_port"): {
        "zh": ("游戏端口", "该世界供游戏客户端连接使用的 UDP 端口，每个世界需要各自独立、不冲突的端口。"),
        "en": ("Server Port", "The UDP port game clients connect to for this shard -- each shard needs "
               "its own, non-conflicting port."),
    },
    ("SHARD", "is_master"): {
        "zh": ("是否为主世界", "true 表示这是主世界（如地面），false 表示这是从世界（如洞穴）。"),
        "en": ("Is Master", "true for the master shard (the surface world), false for a secondary "
               "shard (e.g. Caves)."),
    },
    ("SHARD", "name"): {
        "zh": ("世界名称", "该世界的显示名称，例如 Master、Caves。"),
        "en": ("Name", "The display name of this shard, e.g. Master, Caves."),
    },
    ("SHARD", "id"): {
        "zh": ("世界编号", "该世界在集群内的唯一数字编号。"),
        "en": ("ID", "This shard's unique numeric identifier within the cluster."),
    },
    ("ACCOUNT", "encode_user_path"): {
        "zh": ("加密用户路径", "是否对玩家存档路径中的用户 ID 做混淆编码，建议保持开启。"),
        "en": ("Encode User Path", "Whether player user-IDs in save paths are obfuscated -- recommended on."),
    },
    ("STEAM", "master_server_port"): {
        "zh": ("master_server_port", "STEAM使用的内部端口。服务器实际运行过程中并没有使用这个端口。每个服务器需要设置不同的端口。"),
        "en": ("master_server_port", "The internal port used by STEAM. During the actual operation of the server, this port is not utilized. Each server requires a different port to be set."),
    },
    ("STEAM", "authentication_port"): {
        "zh": ("authentication_port", "STEAM使用的内部端口。服务器实际运行过程中并没有使用这个端口。每个服务器需要设置不同的端口。"),
        "en": ("authentication_port", "The internal port used by STEAM. During the actual operation of the server, this port is not utilized. Each server requires a different port to be set."),
    },
}


# (section, key) -> [(写入文件的原始值, 中文显示名, 英文显示名), ...]
# 用下拉框代替自由输入，防止手滑打出游戏不认识的值 -- 下拉框里显示的是
# 翻译后的名称，但选中后实际写回 ini 文件的仍然是原始英文/locale值,
# 不会因为翻译显示而改变游戏实际读取的内容。
ENUM_FIELDS: dict[tuple[str, str], list[tuple[str, str, str]]] = {
    ("GAMEPLAY", "game_mode"): [
        ("survival", "生存", "Survival"),
        ("endless", "无尽", "Endless"),
        ("wilderness", "荒野", "Wilderness"),
    ],
    # 服务器语言，仅支持中文、繁体中文、英文
    ("NETWORK", "cluster_language"): [
        ("zh", "中文", "Chinese"),
        ("zht", "繁体中文", "Chinese (Traditional)"),
        ("en", "英文", "English"),
    ],
    ("NETWORK", "cluster_intention"): [
        ("cooperative", "合作 (cooperative)", "Cooperative"),
        ("competitive", "竞争 (competitive)", "Competitive"),
        ("social", "社交 (social)", "Social"),
        ("madness", "疯狂 (madness)", "Madness"),
    ],
}


def get_enum_choices(section: str, key: str) -> list[tuple[str, str]] | None:
    """(原始值, 当前语言下的显示名) 列表，找不到则返回 None。"""
    entries = ENUM_FIELDS.get((section, key))
    if not entries:
        return None
    zh = get_lang() == "zh"
    return [(raw, zh_label if zh else en_label) for raw, zh_label, en_label in entries]


# (section, key) -> (最小值, 最大值)——官方文档明确给出取值范围的数值字段，界面上禁止输入/保存范围之外的值。
RANGE_FIELDS: dict[tuple[str, str], tuple[int, int]] = {
    ("NETWORK", "tick_rate"): (15, 60),
}


def get_range_limits(section: str, key: str) -> tuple[int, int] | None:
    return RANGE_FIELDS.get((section, key))


# 游戏自己生成、用途没有官方文档说明的字段——不管是不是服务器存档，一律
# 只读展示，不提供看起来能编辑但改了很可能没意义甚至有副作用的输入框。
ALWAYS_READONLY_FIELDS: set[tuple[str, str]] = {
    ("NETWORK", "cluster_cloud_id"),
}

# 这些字段即使值看起来像数字/布尔（密码设成"0"这种纯数字很常见），也必
# 须原样当字符串处理，不能被 ini_parser.py/config_manager.py 里"猜类型"
# 的通用逻辑转成 int/bool——转成 int 后 `if password:` 会把密码"0"误判成
# "没有密码"。
NO_TYPE_COERCE_FIELDS: set[tuple[str, str]] = {
    ("NETWORK", "cluster_password"),
}


def get_field_info(section: str, key: str, is_shard: bool = False) -> tuple[str, str] | None:
    """查找某个 cluster.ini/server.ini 字段的 (显示名, 说明)，取当前界面语言的版本。

    表里查不到就返回 None——调用方此时应该退回原样显示英文键名，不附加说明。
    """
    table = SHARD_FIELD_INFO if is_shard else CLUSTER_FIELD_INFO
    info = table.get((section, key))
    if not info:
        return None
    return info.get(get_lang()) or info.get("zh")
