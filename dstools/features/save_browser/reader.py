"""DST 存档元数据读取。

读取 .meta 文件和存档会话目录，提取世界信息（天数、季节、时段等），
不需要解析二进制存档本身。
"""

from pathlib import Path

from dstools.shared.lua_parser import LuaParseError, parse_lua_file, parse_lua_table
from dstools.models import PlayerCharacterSave, SaveMetadata, SaveSession, SaveSlot, SaveSource


def list_save_sessions(shard_path: Path) -> list[SaveSession]:
    """列出一个世界（shard）存档目录下的全部会话。

    一个存档会话是 save/session/ 下的一个目录，里面装着编号存档槽
    文件（如 0000000488）和各自对应的 .meta 文件。

    Args:
        shard_path: 世界目录路径（如 Cluster_3/Master/）。

    Returns:
        SaveSession 对象列表。
    """
    sessions = []
    session_dir = shard_path / "save" / "session"

    if not session_dir.exists():
        return sessions

    for entry in sorted(session_dir.iterdir()):
        if not entry.is_dir():
            continue

        # 会话 ID 通常是 16 位十六进制字符串；不是目录的条目上面已经跳过了
        session = _build_session(entry)
        if session.slots:  # 只保留真正有存档数据的会话
            try:
                session.metadata = read_session_metadata(session)
            except Exception:
                pass  # 元数据是可选的，读不到不影响其余字段
            sessions.append(session)

    return sessions


def _read_meta_file(meta_path: Path) -> SaveMetadata | None:
    """读取并解析一个 .meta 文件，提取存档元数据。

    Args:
        meta_path: .meta 文件路径。

    Returns:
        SaveMetadata，文件不存在或解析失败则返回 None。
    """
    if not meta_path.exists():
        return None

    try:
        raw = parse_lua_file(meta_path)
    except Exception:
        return None

    metadata = SaveMetadata(raw=raw)

    clock = raw.get("clock", {})
    if isinstance(clock, dict):
        metadata.day = clock.get("cycles", 0)
        metadata.phase = clock.get("phase", "")

    seasons = raw.get("seasons", {})
    if isinstance(seasons, dict):
        metadata.season = seasons.get("season", "")
        metadata.days_in_season = seasons.get("elapseddaysinseason", 0)
        metadata.days_left_in_season = seasons.get("remainingdaysinseason", 0)

    return metadata


def _build_session(session_path: Path) -> SaveSession:
    """从会话目录路径构建一个 SaveSession。"""
    session = SaveSession(
        session_id=session_path.name,
        path=session_path,
        source=SaveSource.SERVER,
    )

    # 找出存档槽文件（数字文件名，各自可能配一个同名 .meta 文件）
    for entry in sorted(session_path.iterdir()):
        if entry.is_file() and entry.name.isdigit():
            meta_file = session_path / f"{entry.name}.meta"
            slot = SaveSlot(
                slot_number=int(entry.name),
                save_file=entry,
                meta_file=meta_file if meta_file.exists() else None,
                size=entry.stat().st_size,
            )
            session.slots.append(slot)

    return session


def read_session_metadata(session: SaveSession) -> SaveMetadata | None:
    """从会话里最新的 .meta 文件读取元数据。

    Args:
        session: 要读取的 SaveSession。

    Returns:
        SaveMetadata，找不到任何 .meta 文件则返回 None。
    """
    meta_files = [s.meta_file for s in session.slots if s.meta_file and s.meta_file.exists()]
    if not meta_files:
        return None

    return _read_meta_file(meta_files[-1])


def get_save_summary(session: SaveSession) -> str:
    """生成一个存档会话的人类可读摘要。

    Args:
        session: 要生成摘要的 SaveSession。

    Returns:
        人类可读的字符串，如 "第417天, 夏季第12天, 白天"。
    """
    parts = []

    if session.metadata:
        meta = session.metadata
        if meta.day > 0:
            parts.append(f"第{meta.day}天")

        season_names = {
            "summer": "夏季", "winter": "冬季",
            "autumn": "秋季", "spring": "春季",
        }
        if meta.season:
            season_cn = season_names.get(meta.season, meta.season)
            parts.append(f"{season_cn}")
            if meta.days_in_season > 0:
                parts[-1] = f"{season_cn}第{int(meta.days_in_season)}天"

        phase_names = {
            "day": "白天", "dusk": "黄昏", "night": "夜晚",
        }
        if meta.phase:
            phase_cn = phase_names.get(meta.phase, meta.phase)
            parts.append(phase_cn)

    if not parts:
        parts.append("(无元数据)")

    # 附加存档槽数量
    parts.append(f"[{len(session.slots)}个存档槽]")

    return ", ".join(parts)


def _extract_lua_table_text(raw: bytes) -> str:
    """从玩家存档槽位文件的原始字节中提取出 `return {...}` 这一段文本.

    这类文件不是纯 Lua 文本——开头有几个字节的二进制前缀，结尾有时跟着
    几十到几百字节的遗留垃圾数据（实测是游戏覆写文件时，新内容比旧内容
    短、又没有截断文件留下的残留，偶尔看着像可读文本但其实不是存档的一
    部分）。从 `return` 关键字开始正向扫描、按花括号深度找真正的表结尾，
    跳过引号字符串内部的花括号干扰，比直接找最后一个 `}` 可靠——最后一
    个 `}` 有不小概率落在这段垃圾数据里，会把垃圾当成表内容混进来，或者
    因为中间夹了非 UTF-8 字节直接解码失败。找到深度归零的位置后，后面
    不管是什么内容都直接丢弃，不需要关心它是什么、有多长。
    """
    idx = raw.find(b"return")
    if idx == -1:
        raise LuaParseError("player save file has no 'return' table")
    depth = 0
    in_str = None
    start_brace = None
    i = idx
    n = len(raw)
    while i < n:
        ch = raw[i]
        c = chr(ch) if ch < 128 else None
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'"):
                in_str = c
            elif c == "{":
                if start_brace is None:
                    start_brace = i
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0 and start_brace is not None:
                    return raw[idx:i + 1].decode("utf-8")
        i += 1
    raise LuaParseError("player save file has unbalanced braces")


def _read_player_slot_table(path: Path) -> dict:
    """解析一个玩家角色存档槽位的主文件 (不是 .meta)，返回完整的表."""
    raw = path.read_bytes()
    text = _extract_lua_table_text(raw)
    return parse_lua_table(text, str(path))


def list_session_players(session: SaveSession) -> list[PlayerCharacterSave]:
    """列出一个存档会话里，每个玩家的最新角色状态.

    session.path 下面除了世界自己的数字存档槽（文件），还有一批子目录，
    每个对应一个在这个世界玩过的玩家（详见 PlayerCharacterSave 的说
    明——文件夹名是混淆编码过的，不是真实 Klei 账号 ID）。一个玩家的数
    据解析失败不能连累其他玩家、也不能让调用方拿到空列表——只把这一条
    记录标上 parse_error，player_id 之外的字段留空，其余玩家不受影响。
    """
    players: list[PlayerCharacterSave] = []
    if not session.path.exists():
        return players

    for entry in sorted(session.path.iterdir()):
        if not entry.is_dir():
            continue
        player = PlayerCharacterSave(player_id=entry.name)
        try:
            slot_files = sorted(
                (f for f in entry.iterdir() if f.is_file() and f.name.isdigit()),
                key=lambda f: int(f.name),
            )
            if not slot_files:
                player.parse_error = "no save slot found"
                players.append(player)
                continue
            # 跨世界传送、或者进程被异常打断保存时，DST 可能把编号最新的
            # 槽位写成一个 0 字节的占位文件，真正可用的最新角色数据还在
            # 上一个槽位里——真机在本地存档上复现过这个情况（Caves/Master
            # 世界最新槽位均为 0 字节，Master 上一个槽位仍是完整数据）。
            # 优先选最新的非空槽位；全部都是空的（比如玩家从没在这个世界
            # 存过档）才退回原来最新编号的那个，交给下面的解析逻辑按原样
            # 报错，不掩盖真正没有数据的情况。
            non_empty = [f for f in slot_files if f.stat().st_size > 0]
            latest = non_empty[-1] if non_empty else slot_files[-1]
            meta_path = entry / f"{latest.name}.meta"
            player.slot_number = int(latest.name)
            player.save_file = latest
            player.size = latest.stat().st_size

            if meta_path.exists():
                player.meta_file = meta_path
                meta_table = parse_lua_file(meta_path)
                player.character = meta_table.get("character", "")

            table = _read_player_slot_table(latest)
            player.raw = table
            player.x = table.get("x")
            player.z = table.get("z")
            data = table.get("data")
            if isinstance(data, dict):
                health = data.get("health")
                if isinstance(health, dict):
                    player.health = health.get("health")
                sanity = data.get("sanity")
                if isinstance(sanity, dict):
                    player.sanity = sanity.get("current")
                    player.sanity_sane = sanity.get("sane")
                hunger = data.get("hunger")
                if isinstance(hunger, dict):
                    player.hunger = hunger.get("hunger")
                temperature = data.get("temperature")
                if isinstance(temperature, dict):
                    player.temperature = temperature.get("current")
                moisture = data.get("moisture")
                if isinstance(moisture, dict):
                    player.moisture = moisture.get("moisture")
                age = data.get("age")
                if isinstance(age, dict):
                    player.age = age.get("age")
                skinner = data.get("skinner")
                if isinstance(skinner, dict):
                    player.skin_name = skinner.get("skin_name", "")
                    clothing = skinner.get("clothing")
                    if isinstance(clothing, dict):
                        player.clothing = clothing
                builder = data.get("builder")
                if isinstance(builder, dict):
                    recipes = builder.get("recipes")
                    if isinstance(recipes, dict):
                        player.recipes_count = len(recipes)
        except Exception as e:
            player.parse_error = str(e)
        players.append(player)

    return players


def list_known_player_ids(shards: list) -> list[tuple[str, str]]:
    """扫描一组世界（shard）下的全部存档会话，收集出现过的玩家真实 ID。

    两种来源，日志能核实到的优先：
    1. server_log.txt 能关联到真实账号 ID 的（见
       connection_log.collect_player_identity_log）——这种连文件夹名本
       身是混淆编码的玩家也能拿到真实 ID，昵称也是日志里实测到的，不是
       猜的。
    2. 日志关联不到、但文件夹名本身就是 KU_/OU_ 开头的——encode_user_path
       关闭后才会是这种明文文件夹名（见 PlayerCharacterSave 的说明），用
       角色名当辨识提示（角色名不是玩家昵称，只是"看着比裸 ID 好认"）。
       混淆编码、日志又关联不上的文件夹名直接跳过，不拿混淆值冒充真实
       ID 写进 adminlist/blocklist。

    真机存档实测过：encode_user_path 关闭后，文件夹名不总是等于干净的
    KU_xxx——见过 "KU_dwt6dfPl_" 这种带一个尾部下划线的文件夹名，跟这个
    账号真实 ID（同一台机器 adminlist.txt 里记的、以及 server_log.txt
    "Client authenticated" 行里打印的）都是 "KU_dwt6dfPl"，没有下划线。
    直接把带下划线的原样交给调用方去写 adminlist.txt 会因为多一个字符
    匹配不上真实账号，管理员/黑名单形同虚设——这里统一去掉这一个尾部下
    划线再收集。

    Returns:
        按账号 ID 排序、去重后的 (账号ID, 辨识提示) 列表——提示优先用日志
        昵称，查不到昵称退回角色显示名，都没有就是空字符串。
    """
    from dstools.features.save_browser.character_names import get_character_display_name
    from dstools.features.save_browser.connection_log import collect_player_identity_log

    # 同一个真实账号可能在好几个不同的存档文件夹（不同次连接留下的）下
    # 都出现过——有的能查到昵称，有的只有角色名。昵称是日志实测到的更
    # 可信辨识信息，不能被后面遍历到的、只查到角色名的记录覆盖掉，所以
    # 昵称和角色名分开存，最后按"有昵称用昵称、没有才退回角色名"合并，
    # 不看遍历顺序。
    seen_ids: set[str] = set()
    nicknames: dict[str, str] = {}
    character_hints: dict[str, str] = {}
    for shard in shards:
        identity_log = collect_player_identity_log(shard.path)
        for session in list_save_sessions(shard.path):
            for player in list_session_players(session):
                identity = identity_log.get(player.player_id)
                if identity:
                    account_id, nickname = identity
                else:
                    account_id, nickname = player.player_id, None

                if account_id[:3] not in ("KU_", "OU_"):
                    continue
                if account_id.endswith("_"):
                    account_id = account_id[:-1]

                seen_ids.add(account_id)
                if nickname:
                    nicknames[account_id] = nickname
                elif player.character and account_id not in character_hints:
                    character_hints[account_id] = get_character_display_name(player.character)
    return sorted(
        (pid, nicknames.get(pid) or character_hints.get(pid, "")) for pid in seen_ids
    )


def known_nicknames(shards: list) -> dict[str, str]:
    """扫描一组世界的 server_log.txt，收集"账号ID -> 已确认昵称"的映射。

    只在真正查到过 "Client authenticated" 记录时才有值——不像
    list_known_player_ids() 那样在查不到昵称时退回角色名当辅助提示，这
    里要的是"已确认的玩家名称"，管理员/黑名单列表拿这个只标注真正核实
    过的昵称，不用角色名（角色名换局就变，不是玩家身份）凑数。
    """
    from dstools.features.save_browser.connection_log import collect_player_identity_log

    result: dict[str, str] = {}
    for shard in shards:
        for _player_id, (account_id, nickname) in collect_player_identity_log(shard.path).items():
            if nickname:
                result[account_id] = nickname
    return result
