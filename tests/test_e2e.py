"""dstools 端到端验证测试。"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from dstools.shared.lua_parser import (
    LuaTableParser,
    parse_lua_table,
    serialize_lua_table,
    parse_lua_file,
)
from dstools.shared.ini_parser import (
    parse_cluster_ini,
    parse_server_ini,
    write_cluster_ini,
)
from dstools.models import ClusterConfig, ModEntry
from dstools.features.mod.manager import (
    ModOverrides,
    load_mod_overrides,
    save_mod_overrides,
    enable_mod,
    list_mods,
    sync_mods,
)
from dstools.shared.discovery import find_klei_root, discover_environment
from dstools.features.save_browser.reader import (
    list_save_sessions,
    get_save_summary,
    list_session_players,
)
from dstools.features.cluster_config.config_manager import (
    set_cluster_option,
    backfill_cluster_defaults,
    load_shard_config,
    save_shard_config,
    set_shard_option,
    get_shard_option,
)
from dstools.features.save_browser.character_names import get_character_display_name
from dstools.features.save_browser.character_icons import (
    find_mod_character_name,
    resolve_character,
)
from dstools.shared.app_settings import (
    load_settings,
    get_player_note,
    set_player_note,
    get_minimize_on_close,
    set_minimize_on_close,
    get_cache_use_exe_dir,
    set_cache_use_exe_dir,
    get_cache_dir_override,
    set_cache_dir_override,
    get_backup_auto_enabled,
    set_backup_auto_enabled,
    set_backup_retention,
    get_global_tokens,
    set_global_tokens,
)
from dstools.features.local_service.backup_manager import (
    backup_dir,
    create_backup,
    restore_backup,
    list_backups,
)
from dstools.models import SaveSession, SaveSource
from dstools.features.mod.parser import (
    find_game_mods_dir,
    find_mod_folder,
    is_dedicated_server_mods_dir,
    is_custom_steam_mod_id,
    is_workshop_content_id,
    list_installed_mod_ids,
    parse_modinfo,
    split_installed_mod_counts,
    visible_config_options,
)
from dstools.features.cluster_config.admin_manager import (
    read_adminlist,
    add_admin,
    remove_admin,
)
from dstools.shared.token_manager import (
    read_token,
    write_token,
    mask_token,
    is_valid_token,
)
from dstools.features.sakura.api import find_dstcamp_tunnel, sanitize_tunnel_name
from dstools.features.sakura.frpc import FrpcManager
from dstools.shared.app_settings import get_sakura_token, set_sakura_token
from dstools.shared.app_settings import get_luajit_enabled, set_luajit_enabled
from dstools.features.save_browser.cluster_copy import (
    validate_cluster_folder_name,
    suggest_new_cluster_name,
    copy_local_cluster_to_server,
)
from dstools.features.local_service.dedicated_server import find_bin64_dir
from dstools.features.local_service.luajit_injector import (
    WORKSHOP_ID,
    InjectorState,
    LuajitMarker,
    apply_uninstall,
    cleanup_legacy_local_mod_entry,
    detect_state,
    get_luajit_dir,
    is_workshop_subscribed,
    needs_regeneration,
    plan_install,
    read_marker,
    regenerate,
    resolve_launch_bin64_dir,
    write_marker,
)
from dstools.shared.steam_discovery import parse_library_folders, read_game_version_file
from dstools.shared.tex_convert import _has_vc2013_x86_runtime


@contextlib.contextmanager
def _isolated_settings_dir():
    """给读写 DSTCamp 自身设置/缓存的测试用——猴子补丁
    get_settings_dir() 指向一个临时目录，测试期间
    load_settings()/save_settings()/cache_dir() 全部间接落到这个临时目
    录，不会碰真实的 %APPDATA%/DSTCamp/。比"读出真实设置、测完再手动写
    回去"更安全：就算测试中途抛异常/被打断，真实用户数据也从来没被碰
    过，不需要指望 finally 里的恢复逻辑生效。

    要打两个补丁，不是一个：resource_paths.py 是用
    `from dstools.shared.app_settings import get_settings_dir` 把函数抄
    了一份到自己的模块命名空间里，只改 app_settings 模块自己的属性，
    resource_paths.cache_dir() 用的还是抄过去的那份旧引用——两个模块
    各自的 `get_settings_dir` 名字都要替换掉才能让 load_settings()/
    save_settings()（走 app_settings 自己那份）和 cache_dir()（走
    resource_paths 抄的那份）同时生效。"""
    import dstools.shared.app_settings as app_settings
    import dstools.shared.resource_paths as resource_paths

    original_in_app_settings = app_settings.get_settings_dir
    original_in_resource_paths = resource_paths.get_settings_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        def patched():
            return Path(tmpdir)

        app_settings.get_settings_dir = patched
        resource_paths.get_settings_dir = patched
        try:
            yield Path(tmpdir)
        finally:
            app_settings.get_settings_dir = original_in_app_settings
            resource_paths.get_settings_dir = original_in_resource_paths


def test_lua_parser_basic():
    """测试基础 Lua 表解析。"""
    print("=" * 60)
    print("Test 1: Lua Parser - Basic")
    result = parse_lua_table('return {a=1, b="hello", c=true, d=false}')
    assert result == {"a": 1, "b": "hello", "c": True, "d": False}, f"Got: {result}"
    print("  PASS: Basic types parsed correctly")


def test_lua_parser_nested():
    """测试嵌套表解析。"""
    print("Test 2: Lua Parser - Nested Tables")
    result = parse_lua_table("return {a={b={c=42}}, d={1, 2, 3}}")
    assert "a" in result
    assert result["a"]["b"]["c"] == 42
    assert "1" in result["d"]
    print("  PASS: Nested tables parsed correctly")


def test_lua_parser_roundtrip():
    """测试 Lua 表往返：解析 -> 序列化 -> 再解析。"""
    print("Test 3: Lua Parser - Round-trip")
    original = (
        "return {\n"
        '    ["workshop-123"]={\n'
        "        configuration_options={\n"
        "            audio=false,\n"
        '            language="ch",\n'
        "            volume=0.75,\n"
        "            count=42\n"
        "        },\n"
        "        enabled=true\n"
        "    },\n"
        '    ["workshop-456"]={\n'
        "        configuration_options={},\n"
        "        enabled=false\n"
        "    }\n"
        "}"
    )
    parsed = parse_lua_table(original)
    serialized = serialize_lua_table(parsed)
    re_parsed = parse_lua_table(serialized)
    assert parsed == re_parsed, (
        f"Round-trip failed!\nOriginal parsed: {parsed}\nRe-parsed: {re_parsed}"
    )
    print("  PASS: Round-trip preserves all data")


def test_lua_parser_real_data():
    """测试解析真实的 DST modoverrides.lua 文件。"""
    print("Test 4: Lua Parser - Real DST Data")
    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    mod_path = klei_root / "Cluster_3" / "Master" / "modoverrides.lua"
    if not mod_path.exists():
        print(f"  SKIP: {mod_path} not found")
        return

    # 解析
    data = parse_lua_file(mod_path)
    assert len(data) >= 30, f"Expected 30+ mods, got {len(data)}"
    print(f"  PASS: Parsed {len(data)} mods from real modoverrides.lua")

    # 往返
    serialized = serialize_lua_table(data)
    re_parsed = LuaTableParser(serialized).parse()

    # 校验 key 集合一致
    original_keys = set(data.keys())
    re_keys = set(re_parsed.keys())
    assert original_keys == re_keys, (
        f"Key mismatch: {original_keys - re_keys}, {re_keys - original_keys}"
    )

    # 校验每个 mod 都有 enabled 和 configuration_options 字段
    for wid, entry in data.items():
        assert "enabled" in entry, f"Missing 'enabled' in {wid}"
        assert "configuration_options" in entry, (
            f"Missing 'configuration_options' in {wid}"
        )

    print(f"  PASS: Round-trip verified ({len(data)} mods)")
    print("  PASS: All mods have required fields")


def test_ini_parser():
    """测试 INI 配置解析。"""
    print("\n" + "=" * 60)
    print("Test 5: INI Parser")

    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    # 测试 cluster.ini
    cluster_ini = klei_root / "Cluster_3" / "cluster.ini"
    if cluster_ini.exists():
        config = parse_cluster_ini(cluster_ini)
        assert config.gameplay["game_mode"] == "survival"
        assert "max_players" in config.gameplay
        assert "cluster_name" in config.network
        assert "shard_enabled" in config.shard
        print(
            f"  PASS: cluster.ini parsed - mode={config.gameplay['game_mode']}, "
            f"players={config.gameplay['max_players']}"
        )

        # 测试 INI 往返
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "cluster.ini"
            write_cluster_ini(config, tmp_path)
            re_parsed = parse_cluster_ini(tmp_path)
            assert config.gameplay == re_parsed.gameplay
            assert config.network == re_parsed.network
            print("  PASS: cluster.ini round-trip verified")

    # 测试 server.ini
    server_ini = klei_root / "Cluster_3" / "Master" / "server.ini"
    if server_ini.exists():
        config = parse_server_ini(server_ini)
        assert "server_port" in config.network
        assert config.shard.get("is_master") is True
        print(
            f"  PASS: server.ini parsed - port={config.network['server_port']}, "
            f"is_master={config.shard['is_master']}"
        )


def test_discovery():
    """测试路径发现。"""
    print("\n" + "=" * 60)
    print("Test 6: Discovery")

    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    assert klei_root.exists()
    print(f"  PASS: Found Klei root: {klei_root}")

    env = discover_environment(klei_root)
    assert env.user_id
    assert len(env.clusters) >= 1
    print(f"  PASS: User ID: {env.user_id}")
    print(f"  PASS: Clusters: {[c.name for c in env.clusters]}")

    for c in env.clusters:
        assert len(c.shards) >= 1
        assert c.source in (SaveSource.SERVER, SaveSource.LOCAL)
        print(
            f"  PASS: {c.name} has {len(c.shards)} shard(s), source={c.source.value}: "
            f"{[s.name for s in c.shards]}"
        )

    # 按 SaveSource 分类计数上报——不硬性要求两边都非空（这台机器目前
    # 两种都有，但换一台只装了专用服务器/只有本地存档的机器完全可能只
    # 有一边，不是 bug，不该让测试失败）。
    server_count = sum(1 for c in env.clusters if c.source == SaveSource.SERVER)
    local_count = sum(1 for c in env.clusters if c.source == SaveSource.LOCAL)
    assert server_count + local_count == len(env.clusters)
    print(f"  PASS: {server_count} server + {local_count} local cluster(s)")


def test_save_reader():
    """测试存档会话读取。"""
    print("\n" + "=" * 60)
    print("Test 7: Save Reader")

    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    env = discover_environment(klei_root)
    if not env.clusters:
        print("  SKIP: No clusters found")
        return

    for c in env.clusters:
        for s in c.shards:
            sessions = list_save_sessions(s.path)
            if sessions:
                print(f"  {c.name}/{s.name}: {len(sessions)} session(s)")

                for session in sessions:
                    summary = get_save_summary(session)
                    assert session.session_id
                    assert session.slots
                    assert session.source == c.source, (
                        "会话的 source 标记应该跟它所属 cluster 的一致"
                    )
                    print(
                        f"    Session {session.session_id}: {summary} (source={session.source.value})"
                    )

                    if session.metadata:
                        assert session.metadata.day >= 0
                        assert session.metadata.season
                        print(
                            f"    Metadata: day={session.metadata.day}, "
                            f"season={session.metadata.season}, "
                            f"phase={session.metadata.phase}"
                        )
                break  # 只测第一个有会话的 shard
        break  # 只测第一个 cluster


def test_mod_manager():
    """测试 Mod 管理操作——用临时目录+合成数据即可验证，不像前后其它测
    试那样依赖真实 DST 安装。

    只测项目实际用到的 5 个函数（enable_mod/list_mods/sync_mods/
    save_mod_overrides/load_mod_overrides）——disable_mod/set_mod_
    config/get_mod_config/remove_mod/get_mod/diff_mods 原本是给已删除
    的 CLI 用的，manager.py 本身也已经删掉了这几个函数。"""
    print("\n" + "=" * 60)
    print("Test 8: Mod Manager")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "modoverrides.lua"
        overrides = ModOverrides(path=tmp_path)

        enable_mod(overrides, "workshop-test-1")
        assert "workshop-test-1" in overrides.mods
        assert overrides.mods["workshop-test-1"].enabled
        print("  PASS: Enable mod (adds if not present)")

        save_mod_overrides(overrides)
        reloaded = load_mod_overrides(tmp_path)
        assert len(list_mods(reloaded)) == 1
        print("  PASS: Save and reload preserves data")

        a = ModOverrides(path=Path(tmpdir) / "a.lua")
        b = ModOverrides(path=Path(tmpdir) / "b.lua")
        enable_mod(a, "workshop-shared")
        enable_mod(a, "workshop-only-a")
        enable_mod(b, "workshop-shared")
        enable_mod(b, "workshop-only-b")

        sync_mods(a, b)
        assert "workshop-only-a" in b.mods
        assert "workshop-only-b" not in b.mods
        print("  PASS: Mod sync works")


def test_config_manager():
    """测试配置管理器操作。"""
    print("\n" + "=" * 60)
    print("Test 9: Config Manager")

    config = ClusterConfig()
    assert config.gameplay == {}
    assert config.network == {}

    set_cluster_option(config, "GAMEPLAY", "game_mode", "endless")
    set_cluster_option(config, "GAMEPLAY", "max_players", 10)
    set_cluster_option(config, "NETWORK", "cluster_name", "Test Server")

    assert config.gameplay["game_mode"] == "endless"
    assert config.gameplay["max_players"] == 10
    assert config.network["cluster_name"] == "Test Server"
    print("  PASS: Set cluster options with type coercion")

    # 测试写入-读取往返
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "cluster.ini"
        write_cluster_ini(config, tmp_path)

        assert tmp_path.exists()
        content = tmp_path.read_text()
        assert "[GAMEPLAY]" in content
        assert "game_mode = endless" in content
        assert "max_players = 10" in content
        print("  PASS: Write cluster.ini with correct format")

        re_parsed = parse_cluster_ini(tmp_path)
        assert re_parsed.gameplay["game_mode"] == "endless"
        assert re_parsed.gameplay["max_players"] == 10
        print("  PASS: Read back cluster.ini preserves values")


def test_list_session_players():
    """测试单个会话目录下按玩家角色发现/解析存档。"""
    print("\n" + "=" * 60)
    print("Test 11: Per-Player Character Save Reader")

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir) / "session" / "ABCDEF0123456789"
        session_dir.mkdir(parents=True)

        # 一个正常玩家：真实存档实测过的字节结构——3 字节任意二进制前缀 +
        # `return {...}` + 1 字节 0x01 结尾（大多数真实文件是这样；也有极
        # 少数文件结尾会跟着更多遗留垃圾，这里只测最常见的这种）。
        good_dir = session_dir / "A7GOODPLAYER"
        good_dir.mkdir()
        table_text = (
            "return {x=100.5,z=-50.25,data={health={health=120},"
            "sanity={current=150,sane=true},hunger={hunger=100},"
            'age={age=42}},age=0,prefab="wilson"}'
        )
        (good_dir / "0000000007").write_bytes(
            b"\x03\x11\x22" + table_text.encode("utf-8") + b"\x01"
        )
        (good_dir / "0000000007.meta").write_bytes(b'return {character="wilson"}\x00')

        # 一个损坏玩家：主文件里完全没有 return，模拟解析失败——验证
        # "一个玩家坏了不连累其他玩家"。
        bad_dir = session_dir / "A7BADPLAYER0"
        bad_dir.mkdir()
        (bad_dir / "0000000003").write_bytes(b"\x00\x01\x02\x03garbage, not lua at all")
        (bad_dir / "0000000003.meta").write_bytes(b'return {character="wolfgang"}\x00')

        session = SaveSession(session_id="ABCDEF0123456789", path=session_dir)
        players = list_session_players(session)
        assert len(players) == 2, f"Expected 2 players, got {len(players)}"

        by_id = {p.player_id: p for p in players}
        good = by_id["A7GOODPLAYER"]
        assert not good.parse_error, (
            f"Good player should parse cleanly, got: {good.parse_error}"
        )
        assert good.character == "wilson"
        assert good.health == 120
        assert good.sanity == 150 and good.sanity_sane is True
        assert good.hunger == 100
        assert good.age == 42
        assert good.x == 100.5 and good.z == -50.25
        print("  PASS: Well-formed player save parsed correctly (binary-framed file)")

        bad = by_id["A7BADPLAYER0"]
        assert bad.parse_error, "Corrupt player should have parse_error set"
        assert bad.player_id == "A7BADPLAYER0"
        print(
            "  PASS: Corrupt player entry isolated (parse_error set, other player unaffected)"
        )

        # 真机在本地存档上复现过的情况：跨世界传送/进程被打断时，DST 会把
        # 编号最新的槽位写成 0 字节占位文件，真正数据还在上一个槽位里——
        # 必须回退去读那一个，不能因为最新槽位是空文件就整条判定解析失败。
        empty_latest_dir = session_dir / "A7EMPTYLATEST"
        empty_latest_dir.mkdir()
        (empty_latest_dir / "0000000010").write_bytes(
            b"\x03\x11\x22"
            + 'return {data={health={health=88}},prefab="willow"}'.encode("utf-8")
            + b"\x01"
        )
        (empty_latest_dir / "0000000010.meta").write_bytes(
            b'return {character="willow"}\x00'
        )
        (empty_latest_dir / "0000000011").write_bytes(b"")  # 0 字节占位文件
        (empty_latest_dir / "0000000011.meta").write_bytes(
            b'return {character="willow"}\x00'
        )

        players = list_session_players(session)
        empty_latest = next(p for p in players if p.player_id == "A7EMPTYLATEST")
        assert not empty_latest.parse_error, (
            f"Should fall back to slot 10, got: {empty_latest.parse_error}"
        )
        assert empty_latest.slot_number == 10
        assert empty_latest.health == 88
        print(
            "  PASS: Falls back to the newest non-empty slot when the latest one is a 0-byte placeholder"
        )


def test_character_names():
    """测试角色 prefab 名到显示名称的查找。"""
    print("\n" + "=" * 60)
    print("Test 12: Character Name Lookup")

    assert get_character_display_name("wilson") == "威尔逊.P.希格斯伯里"
    assert get_character_display_name("willow", "en") == "Willow"
    assert get_character_display_name("wolfgang") == "沃尔夫冈"
    print("  PASS: Known vanilla characters resolve to verified display names")

    # 模组自定义角色查不到，原样返回，不猜测拼凑
    assert (
        get_character_display_name("some_modded_character") == "some_modded_character"
    )
    print("  PASS: Unknown/modded prefab falls back to raw name unchanged")


def test_character_icons():
    """测试 character_icons.py 里模组角色名扫描 + resolve_character 回退
    链。头像转换本身依赖真实 Steam 安装/ktech.exe，这里只覆盖不需要真机
    环境的部分：正则扫描模组 .lua 文件找角色名声明，以及 resolve_character
    在"官方表命中"和"哪里都找不到"两种情况下的行为。"""
    print("\n" + "=" * 60)
    print("Test 13: Character Icon / Mod Name Resolution")

    with tempfile.TemporaryDirectory() as tmp:
        mod_folder = Path(tmp) / "fake_mod"
        prefab_dir = mod_folder / "scripts" / "prefabs"
        prefab_dir.mkdir(parents=True)
        (prefab_dir / "testchar.lua").write_text(
            'STRINGS.CHARACTER_NAMES.testchar = "测试角色"\n'
            'STRINGS.CHARACTER_TITLES.testchar = "某个称号"\n',
            encoding="utf-8",
        )

        assert find_mod_character_name(mod_folder, "testchar") == "测试角色"
        print(
            "  PASS: Mod-declared STRINGS.CHARACTER_NAMES.<prefab> found via regex scan"
        )

        assert find_mod_character_name(mod_folder, "no_such_prefab") is None
        print("  PASS: Prefab not declared by this mod returns None (no guessing)")

    # 官方角色表命中：不需要 mod_overrides_path，直接走官方分支。
    name, _icon = resolve_character("wilson", None)
    assert name == "威尔逊.P.希格斯伯里"
    print(
        "  PASS: resolve_character resolves known vanilla prefab without touching mods"
    )

    # 哪里都找不到（未知 prefab + 不存在的 modoverrides 路径）：原样回退，
    # 不抛异常、不给头像。
    name, icon = resolve_character(
        "totally_unknown_prefab", Path(tmp) / "does_not_exist.lua"
    )
    assert name == "totally_unknown_prefab" and icon is None
    print("  PASS: Unresolvable prefab falls back to raw name with no icon")


def test_modinfo_reader():
    """用手写的合成 mod 数据测试 modinfo.lua 解析逻辑（parser.py）——这段
    逻辑此前完全没有功能测试覆盖，只在程序启动导入模块时被间接跑到。"""
    print("\n" + "=" * 60)
    print("Test 14: Modinfo Parsing")

    with tempfile.TemporaryDirectory() as tmp:
        mod_folder = Path(tmp) / "123456"
        mod_folder.mkdir()
        (mod_folder / "modinfo.lua").write_text(
            """
            name = "Test Mod"
            author = "Tester"
            version = "1.0.0"
            description = "A test mod for parsing."
            icon = "modicon.tex"
            icon_atlas = "modicon.xml"

            configuration_options = {
                {
                    name = "difficulty",
                    label = "Difficulty",
                    hover = "How hard",
                    options = {
                        {description = "Easy", data = "easy"},
                        {description = "Hard", data = "hard"},
                    },
                    default = "easy",
                },
            }
            """,
            encoding="utf-8",
        )

        info = parse_modinfo(mod_folder)
        assert info is not None
        assert (
            info.name == "Test Mod"
            and info.author == "Tester"
            and info.version == "1.0.0"
        )
        assert info.workshop_id == "workshop-123456"
        print(
            "  PASS: Top-level fields (name/author/version/workshop_id) parsed correctly"
        )

        assert len(info.config_options) == 1
        opt = info.config_options[0]
        assert opt.name == "difficulty" and opt.label == "Difficulty"
        assert [c["data"] for c in opt.choices] == ["easy", "hard"]
        print("  PASS: configuration_options choices parsed correctly")

        # 不存在 modinfo.lua 的文件夹：明确返回 None，不抛异常。
        assert parse_modinfo(Path(tmp) / "does_not_exist") is None
        print("  PASS: Missing modinfo.lua returns None")

        # client_only_mod=true 但同时 server_only_mod=true（DontStarveLuaJIT2
        # 的真实写法，作者确认过：饥荒引擎本身不读 server_only_mod，是给开
        # 服工具用的约定，表示"仍然当服务器 mod 处理，配置可编辑"）——不
        # 应该被判定成 client_only，见 ModInfo.client_only 的说明。
        local_folder = Path(tmp) / "654321"
        local_folder.mkdir()
        (local_folder / "modinfo.lua").write_text(
            'name = "Local Only Mod"\nclient_only_mod = true\n', encoding="utf-8"
        )
        assert parse_modinfo(local_folder).client_only is True
        print(
            "  PASS: plain client_only_mod=true (no server_only_mod) stays client_only"
        )

        server_folder = Path(tmp) / "654322"
        server_folder.mkdir()
        (server_folder / "modinfo.lua").write_text(
            'name = "LuaJIT-style Mod"\nclient_only_mod = true\nserver_only_mod = true\n',
            encoding="utf-8",
        )
        assert parse_modinfo(server_folder).client_only is False
        print(
            "  PASS: server_only_mod=true overrides client_only_mod, treated as a server mod"
        )

        # 单个配置项自己标 client = true（真实案例：某模组把"服务端设置"/
        # "客户端设置"分成两组，后者的每个选项都带这个字段）——不是引擎
        # 认的字段（真机对照过 modconfigurationscreen.lua 源码，压根没有
        # 引用），是给"开服工具"这类第三方管理软件的约定：这类设置只影
        # 响玩家自己客户端本地表现（快捷键、UI 位置），对着服务端存档的
        # modoverrides.lua 改了没有任何实际效果，本地服务器工具应该隐藏
        # 掉，见 ModConfigDialog 里 visible_config_options() 的调用。
        client_folder = Path(tmp) / "654323"
        client_folder.mkdir()
        (client_folder / "modinfo.lua").write_text(
            """
            name = "Mixed Config Mod"
            configuration_options = {
                { name = "", label = "Server Settings", options = {{description = "", data = false}}, default = false },
                { name = "server_opt", label = "Server Opt", options = {{description = "On", data = true}}, default = true },
                { name = "", label = "Client Settings", options = {{description = "", data = false}}, default = false },
                { name = "client_opt", label = "Client Opt", options = {{description = "On", data = true}}, default = true, client = true },
            }
            """,
            encoding="utf-8",
        )
        info = parse_modinfo(client_folder)
        by_name = {o.name: o for o in info.config_options}
        assert by_name["server_opt"].client is False
        assert by_name["client_opt"].client is True
        print("  PASS: 单个配置项的 client = true 字段解析正确")

        visible = visible_config_options(info.config_options)
        visible_names = [o.name for o in visible]
        assert visible_names == ["", "server_opt"], (
            f"应该只剩服务端标题+选项，客户端标题和选项整组一起隐藏: {visible_names}"
        )
        print(
            "  PASS: visible_config_options() 过滤纯客户端选项，且连带隐藏底下选项全被过滤的分组标题"
        )

        # 共享库 mod "Configs Extended"（创意工坊 3317960157）的约定字段
        # ——真机读过它的源码确认最终仍然写回同一份 modoverrides.lua，只
        # 是值的形状不是固定选项，ModConfigDialog 改用专门的编辑控件
        # （见 is_set_config/is_array_config/is_text_config 字段上的说
        # 明）。这里只测字段解析，控件层面的读写用真实 mod 文件
        # （3686724289）人工验证过。
        configs_extended_folder = Path(tmp) / "654324"
        configs_extended_folder.mkdir()
        (configs_extended_folder / "modinfo.lua").write_text(
            """
            name = "Configs Extended Style Mod"
            configuration_options = {
                { name = "ban_recipe_list", label = "Ban List", is_set_config = true,
                  options = {{description = "请启用配置扩展模组！", data = {}}}, default = {} },
                { name = "priority_list", label = "Priority List", is_array_config = true,
                  options = {{description = "请启用配置扩展模组！", data = {}}}, default = {} },
                { name = "welcome_msg", label = "Welcome Message", is_text_config = true,
                  options = {{description = "请启用配置扩展模组！", data = ""}}, default = "" },
                { name = "starting_items", label = "Starting Items", is_dictionary_config = true,
                  options = {{description = "请启用配置扩展模组！", data = {}}}, default = {} },
            }
            """,
            encoding="utf-8",
        )
        info = parse_modinfo(configs_extended_folder)
        by_name = {o.name: o for o in info.config_options}
        assert by_name["ban_recipe_list"].is_set_config is True
        assert by_name["ban_recipe_list"].is_header is False
        assert by_name["priority_list"].is_array_config is True
        assert by_name["welcome_msg"].is_text_config is True
        assert by_name["starting_items"].is_dictionary_config is True
        print(
            "  PASS: Configs Extended 风格的 is_set_config/is_array_config/is_text_config/is_dictionary_config 解析正确"
        )

        # is_dictionary_config 的真实存储形状是普通 Lua 表、键值都是字符
        # 串（跟 is_set_config 值固定为 true 不同）——之前 ModConfigOption
        # 完全没有这个字段，ModConfigDialog 会把它当成普通下拉框选项处
        # 理（choices 为空、找不到默认值），导致这种配置项在开服工具里
        # 根本无法编辑。这里验证 serialize_lua_table/parse_lua_file 这条
        # 通用 Lua 表读写路径本来就能正确处理 dict[str,str] 值（不需要为
        # 字典类型专门改序列化逻辑，真正缺的只是 GUI 编辑器和字段识别）。
        overrides_path = Path(tmp) / "modoverrides.lua"
        mod_overrides = ModOverrides(path=overrides_path)
        mod_overrides.mods["workshop-654324"] = ModEntry(
            workshop_id="workshop-654324",
            enabled=True,
            configuration_options={
                "starting_items": {"草": "6个", "树枝": "6个", "燧石": "2个"}
            },
        )
        save_mod_overrides(mod_overrides)
        reloaded = load_mod_overrides(overrides_path)
        assert reloaded.mods["workshop-654324"].configuration_options[
            "starting_items"
        ] == {"草": "6个", "树枝": "6个", "燧石": "2个"}
        print(
            "  PASS: is_dictionary_config 的字符串键值对配置项能正确写入/读回 modoverrides.lua"
        )

        # 真机复现过的坑（数据丢失）：is_array_config 的值不管是来自
        # modinfo.lua 的 default 还是游戏已经存进 modoverrides.lua 的存
        # 量数据，这个项目的 Lua 解析器都会解析成 "1"/"2"/"3"... 这种字
        # 符串数字 key 的 dict（Lua 数组和普通表是同一种数据结构，解析
        # 器忠实保留了这一点），不是原生 Python list——ModConfigDialog.
        # _raw_value_to_lines() 之前只认原生 list，任何真实存过的数组都
        # 会被当成"形状不对"兜底成空列表，编辑器显示成空的，点应用还会
        # 把这份假的空列表覆盖写回文件，真正清空原有数据。
        from dstools.features.mod.tab import ModConfigDialog

        parsed_array_shape = {
            "1": "torch",
            "2": "backpack",
            "3": "axe",
        }  # parse_lua_table() 对数组字面量的真实产出形状
        assert ModConfigDialog._raw_value_to_lines("array", parsed_array_shape) == [
            "torch",
            "backpack",
            "axe",
        ]
        assert ModConfigDialog._raw_value_to_lines("array", {}) == []
        assert ModConfigDialog._raw_value_to_lines("array", ["torch", "backpack"]) == [
            "torch",
            "backpack",
        ]
        print(
            '  PASS: is_array_config 识别 Lua 解析器实际产出的"数组形状 dict"，不再把存量数据当成空列表'
        )


def test_workshop_content_directory_filter():
    """Steam UGC 内容目录只接受标准 PublishedFileId_t 文件夹名。"""
    print("\n" + "=" * 60)
    print("Test 14b: Workshop Content Directory Filter")

    assert is_workshop_content_id("3485293431")
    assert is_workshop_content_id("18446744073709551615")
    for invalid in (
        "",
        "0",
        "00123",
        "3485293431_bak",
        "workshop-3485293431",
        "１２３",
        "18446744073709551616",
    ):
        assert not is_workshop_content_id(invalid), invalid
    print("  PASS: 仅接受非零 uint64 的规范 ASCII 十进制目录名，不限制十位")

    assert not is_custom_steam_mod_id("workshop-3485293431")
    assert not is_custom_steam_mod_id("workshop-18446744073709551615")
    for custom in ("CommonModSets", "my_local_mod", "workshop-demo", "workshop-00123"):
        assert is_custom_steam_mod_id(custom), custom
    print("  PASS: 自定义筛选只排除标准 workshop-<PublishedFileId_t> 标识")

    from dstools.models import Platform

    sample_ids = ["workshop-1", "workshop-2", "CommonModSets"]
    assert split_installed_mod_counts(sample_ids, Platform.STEAM) == (2, 1)
    assert split_installed_mod_counts(sample_ids, Platform.WEGAME) == (3, 0)
    print("  PASS: Steam 拆分普通/自定义计数，WeGame 不误判无前缀 ID")

    import dstools.features.mod.parser as mod_parser

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workshop = root / "content" / "322330"
        local_mods = root / "mods"
        workshop.mkdir(parents=True)
        local_mods.mkdir()
        for folder_name in ("3485293431", "3485293431_bak", "00123", "１２３"):
            folder = workshop / folder_name
            folder.mkdir()
            (folder / "modinfo.lua").write_text(
                f'name = "{folder_name}"\n', encoding="utf-8"
            )
        local = local_mods / "my_local_mod_bak"
        local.mkdir()
        (local / "modinfo.lua").write_text('name = "Local"\n', encoding="utf-8")
        legacy = local_mods / "workshop-463952377"
        legacy.mkdir()
        (legacy / "modinfo.lua").write_text('name = "Legacy"\n', encoding="utf-8")

        original_workshop = mod_parser.find_workshop_dir
        original_game_mods = mod_parser.find_game_mods_dir
        try:
            mod_parser.find_workshop_dir = lambda: workshop
            mod_parser.find_game_mods_dir = lambda: local_mods
            ids = list_installed_mod_ids()
            assert ids == [
                "workshop-3485293431",
                "my_local_mod_bak",
                "workshop-463952377",
            ]
            assert find_mod_folder("workshop-3485293431") == workshop / "3485293431"
            assert find_mod_folder("463952377") == legacy
            assert find_mod_folder("workshop-3485293431_bak") is None
        finally:
            mod_parser.find_workshop_dir = original_workshop
            mod_parser.find_game_mods_dir = original_game_mods
    print(
        "  PASS: content/322330 过滤备份目录，纯数字 V1 ID 能定位到 workshop-ID 运行目录"
    )

    # 没安装客户端时，专服自己的 mods 绝不能回退成客户端源目录，否则
    # “创建软连接”会得到源=目标的古怪提示，甚至存在误操作风险。
    from dstools.shared import app_settings

    with tempfile.TemporaryDirectory() as tmp:
        library = Path(tmp)
        server_mods = (
            library
            / "steamapps"
            / "common"
            / "Don't Starve Together Dedicated Server"
            / "mods"
        )
        server_mods.mkdir(parents=True)
        original_libraries = mod_parser.find_all_steam_libraries
        original_override = app_settings.get_steam_mods_path
        original_server_path = app_settings.get_dedicated_server_path
        try:
            mod_parser.find_all_steam_libraries = lambda: [library]
            app_settings.get_steam_mods_path = lambda: server_mods
            app_settings.get_dedicated_server_path = lambda: server_mods.parent
            assert is_dedicated_server_mods_dir(server_mods)
            assert find_game_mods_dir() is None
        finally:
            mod_parser.find_all_steam_libraries = original_libraries
            app_settings.get_steam_mods_path = original_override
            app_settings.get_dedicated_server_path = original_server_path
    print("  PASS: 未安装客户端时不把专服 mods 误识别为软连接源目录")


def test_admin_manager():
    """测试 adminlist.txt 读写往返（admin_manager.py）。"""
    from dstools.features.cluster_config.tab import _is_valid_dst_user_id

    print("\n" + "=" * 60)
    print("Test 15: Admin List Manager")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "adminlist.txt"

        assert read_adminlist(path) == []
        print("  PASS: Missing adminlist.txt reads as empty list")

        assert _is_valid_dst_user_id("KU_aaaaaaaa")
        assert _is_valid_dst_user_id("OU_76561198000000000")
        assert not _is_valid_dst_user_id("ou_76561198000000000")
        assert not _is_valid_dst_user_id("XX_aaaaaaaa")
        assert not _is_valid_dst_user_id("OU_bad-id")
        print("  PASS: KU_ and OU_ IDs accepted; invalid prefixes/characters rejected")

        assert add_admin(path, "KU_aaaaaaaa") is True
        assert add_admin(path, "OU_76561198000000000") is True
        assert add_admin(path, "KU_bbbbbbbb") is True
        assert add_admin(path, "KU_aaaaaaaa") is False, (
            "Adding an existing admin should be a no-op"
        )
        assert read_adminlist(path) == [
            "KU_aaaaaaaa", "OU_76561198000000000", "KU_bbbbbbbb",
        ]
        print("  PASS: add_admin appends new IDs and rejects duplicates")

        assert remove_admin(path, "KU_aaaaaaaa") is True
        assert remove_admin(path, "KU_aaaaaaaa") is False, (
            "Removing an absent admin should be a no-op"
        )
        assert read_adminlist(path) == ["OU_76561198000000000", "KU_bbbbbbbb"]
        print("  PASS: remove_admin removes an entry and is idempotent")


def test_token_manager():
    """测试 cluster_token.txt 读写往返及脱敏显示（token_manager.py）。"""
    print("\n" + "=" * 60)
    print("Test 16: Token Manager")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cluster_token.txt"

        assert read_token(path) == ""
        print("  PASS: Missing cluster_token.txt reads as empty string")

        token = "pds-g^KU_1234567890abcdefghijklmnop...c0w="
        write_token(path, token)
        assert read_token(path) == token
        print("  PASS: write_token/read_token round-trips exactly")

        assert is_valid_token(token) is True
        assert is_valid_token("") is False and is_valid_token("short") is False
        print(
            "  PASS: is_valid_token distinguishes real tokens from empty/short strings"
        )

        masked = mask_token(token)
        assert (
            masked.startswith(token[:8])
            and masked.endswith(token[-8:])
            and "..." in masked
        )
        assert mask_token("short") == "*" * len("short")
        print(
            "  PASS: mask_token shows only the ends of a real token, fully masks short ones"
        )


def test_cluster_copy():
    """测试"复制为服务器存档"逻辑（cluster_copy.py）：名称校验、默认名建
    议、以及实际的文件夹复制。"""
    print("\n" + "=" * 60)
    print("Test 18: Cluster Copy (local save -> server save)")

    # 应用户要求收紧成白名单（英文字母/数字/下划线，参照 Linux 主机名
    # 那种严格程度）——中文/空格/连字符/文件系统特殊符号统统拒绝，
    # "."/".."这两个曾经单独判的保留名现在也落在同一条 invalid_chars 里
    # （纯句点不可能匹配这个白名单，不需要再单独判一次）。
    assert validate_cluster_folder_name("MyServer") is None
    assert validate_cluster_folder_name("Cluster_5") is None
    assert validate_cluster_folder_name("") == "empty"
    assert validate_cluster_folder_name("   ") == "empty"
    assert validate_cluster_folder_name("bad/name") == "invalid_chars"
    assert validate_cluster_folder_name("..") == "invalid_chars"
    assert validate_cluster_folder_name("我的存档") == "invalid_chars"
    assert validate_cluster_folder_name("my server") == "invalid_chars"
    assert validate_cluster_folder_name("my-server") == "invalid_chars"
    print(
        "  PASS: validate_cluster_folder_name only accepts English letters/digits/underscore, "
        "rejects Chinese/spaces/hyphens/other punctuation (no Cluster_<N> format required)"
    )

    with tempfile.TemporaryDirectory() as tmp:
        klei_root = Path(tmp) / "klei_root"
        klei_root.mkdir()
        (klei_root / "Cluster_1").mkdir()  # 已占用

        assert suggest_new_cluster_name(klei_root, "Cluster_1") == "Cluster_2"
        assert suggest_new_cluster_name(klei_root, "MyLocalSave") == "MyLocalSave"
        print(
            "  PASS: suggest_new_cluster_name falls back to Cluster_N only when the "
            "preferred (source) name is already taken"
        )

        # 造一个假的本地 cluster 文件夹（cluster.ini + 一个假世界子目录），
        # 复制到 klei_root 下一个新名字。
        local_cluster = Path(tmp) / "local_user" / "Cluster_1"
        (local_cluster / "Master").mkdir(parents=True)
        (local_cluster / "cluster.ini").write_text(
            "[GAMEPLAY]\nmax_players=6\n", encoding="utf-8"
        )
        (local_cluster / "Master" / "server.ini").write_text(
            "[NETWORK]\n", encoding="utf-8"
        )

        logs = []
        dest = copy_local_cluster_to_server(
            local_cluster, klei_root, "Cluster_2", on_log=logs.append
        )
        assert dest == klei_root / "Cluster_2"
        assert (dest / "cluster.ini").read_text(encoding="utf-8") == (
            local_cluster / "cluster.ini"
        ).read_text(encoding="utf-8")
        assert (dest / "Master" / "server.ini").exists()
        assert local_cluster.exists() and (local_cluster / "cluster.ini").exists(), (
            "源文件夹必须保持不变"
        )
        assert len(logs) > 0
        print(
            "  PASS: copy_local_cluster_to_server copies the whole folder (files + shard "
            "subfolders) and leaves the source untouched"
        )

        try:
            copy_local_cluster_to_server(local_cluster, klei_root, "Cluster_2")
            assert False, "Copying onto an already-existing destination must raise"
        except FileExistsError:
            print(
                "  PASS: copying onto an existing destination raises instead of overwriting"
            )

        # 真机反馈过的真实 bug：源本地存档偶尔会带一个已经存在、但内容
        # 是空的 cluster_token.txt（比如以前手动建过又清空过）——旧逻辑
        # 只判断"目标文件存不存在"，复制过去后 exists() 为真，就误判成
        # "已经有 token 了"跳过自动填充，全局令牌池明明有值，新存档却
        # 还是空 token，启动时报"没有设置令牌"。改成按 is_valid_token()
        # 判断"内容像不像一个真令牌"，这里验证空文件也能被正确识别为
        # "无效"，从全局令牌池里正常补上。
        original_pool = get_global_tokens()
        try:
            fake_token = "x" * 30
            set_global_tokens([fake_token])
            (local_cluster / "cluster_token.txt").write_text("", encoding="utf-8")
            dest3 = copy_local_cluster_to_server(local_cluster, klei_root, "Cluster_3")
            assert read_token(dest3 / "cluster_token.txt") == fake_token, (
                "源存档带的是空 cluster_token.txt，应该被判定为无效并从全局令牌池自动补上"
            )
            print(
                "  PASS: copy_local_cluster_to_server treats an existing-but-empty "
                "cluster_token.txt as invalid and still auto-fills from the global token pool"
            )
        finally:
            set_global_tokens(original_pool)


def test_player_notes():
    """测试按玩家存储备注（app_settings.py）。"""
    print("\n" + "=" * 60)
    print("Test 19: Player Notes")

    with _isolated_settings_dir():
        assert get_player_note("TEST_NONEXISTENT_ID") == "", (
            "Unset note should be empty string"
        )
        print("  PASS: Unset player note defaults to empty string")

        set_player_note("TEST_PLAYER_A", "老王的存档")
        assert get_player_note("TEST_PLAYER_A") == "老王的存档"
        print("  PASS: Set/get player note round-trips")

        set_player_note("TEST_PLAYER_A", "")
        assert get_player_note("TEST_PLAYER_A") == "", (
            "Clearing a note should remove it, not leave an empty entry"
        )
        assert "TEST_PLAYER_A" not in load_settings().get("player_notes", {})
        print(
            "  PASS: Clearing a note removes the entry instead of leaving a blank one"
        )


def test_app_settings_toggles():
    """验证持久化开关，以及缓存、数据和安全目录的边界。"""
    print("\n" + "=" * 60)
    print("Test 20: App Settings Toggles")

    with _isolated_settings_dir():
        for get_fn, set_fn, default in (
            (get_minimize_on_close, set_minimize_on_close, True),
            (get_cache_use_exe_dir, set_cache_use_exe_dir, False),
            (get_backup_auto_enabled, set_backup_auto_enabled, True),
        ):
            assert get_fn() is default, f"{get_fn.__name__} 默认值应该是 {default}"
            set_fn(not default)
            assert get_fn() is not default
            set_fn(default)
            assert get_fn() is default
        print(
            "  PASS: minimize_on_close/cache_use_exe_dir/backup_auto_enabled 默认值+读写往返都正常"
        )

        from dstools.shared.app_settings import get_settings_dir
        from unittest.mock import patch

        from dstools.shared.resource_paths import (
            cache_root_dir,
            cache_dir,
            data_dir,
            path_is_ascii,
            runtime_tool_path,
            security_dir,
            validate_cache_root,
        )

        root = get_settings_dir()
        custom_cache = root / "custom-cache"
        assert get_cache_dir_override() is None
        set_cache_use_exe_dir(True)
        set_cache_dir_override(custom_cache)
        assert get_cache_dir_override() == custom_cache
        assert get_cache_use_exe_dir() is False
        assert cache_root_dir() == custom_cache
        assert validate_cache_root(custom_cache) is None
        assert path_is_ascii(custom_cache) is True
        assert validate_cache_root(root / "中文缓存") == "non_ascii"
        assert validate_cache_root(Path("relative-cache")) == "not_absolute"
        set_cache_dir_override(None)
        assert get_cache_dir_override() is None
        assert cache_root_dir() == root / "cache"
        print("  PASS: 自定义缓存目录可持久化，且拒绝中文路径、相对路径")

        legacy_background = cache_dir("background")
        legacy_background.mkdir(parents=True)
        (legacy_background / "custom.png").write_bytes(b"image")
        migrated = data_dir("background", legacy_cache_name="background")
        assert migrated == root / "data" / "background"
        assert (migrated / "custom.png").read_bytes() == b"image"
        assert security_dir("frp_selfhost") == root / "security" / "frp_selfhost"

        bundled_tool = root / "bundle" / "tools" / "frpc" / "frpc.exe"
        bundled_tool.parent.mkdir(parents=True)
        bundled_tool.write_bytes(b"frpc")
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", str(root / "bundle"), create=True),
        ):
            stable_tool = runtime_tool_path("frpc/frpc.exe")
        assert stable_tool.parent.parent.parent == root / "data" / "runtime_tools"
        assert stable_tool.read_bytes() == b"frpc"
        print("  PASS: 缓存、持久数据、安全材料和长驻工具使用独立目录")


def test_cache_path_user_guidance():
    """缓存路径异常应在启动时提醒，恢复无效默认值时使用专门提示。"""
    print("\n" + "=" * 60)
    print("Test 20b: Cache Path User Guidance")
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    from dstools.gui.app import DSToolsApp
    from dstools.gui import app as gui_app
    from dstools.shared import resource_paths

    invalid_path = Path("C:/Users/中文用户/AppData/Roaming/DSTCamp/cache")
    choose_cache = Mock()
    dummy = SimpleNamespace(root=object(), _choose_cache_dir=choose_cache)
    with (
        patch.object(resource_paths, "cache_root_dir", return_value=invalid_path),
        patch.object(resource_paths, "path_is_ascii", return_value=False),
        patch.object(gui_app.dlg, "ask_choice", return_value="fix") as ask_choice,
    ):
        DSToolsApp._check_cache_dir_on_startup(dummy)
    ask_choice.assert_called_once()
    assert str(invalid_path) in ask_choice.call_args.args[2]
    choose_cache.assert_called_once_with()

    dummy._show_cache_dir_error = Mock()
    with (
        patch.object(
            resource_paths,
            "cache_root_dir",
            return_value=Path("D:/DSTCampData/cache"),
        ),
        patch.object(
            resource_paths, "default_cache_root_dir", return_value=invalid_path
        ),
        patch.object(resource_paths, "validate_cache_root", return_value="non_ascii"),
        patch.object(gui_app.dlg, "show_warning") as show_warning,
    ):
        assert DSToolsApp._restore_default_cache_dir(dummy) is False
    show_warning.assert_called_once()
    assert str(invalid_path) in show_warning.call_args.args[2]
    dummy._show_cache_dir_error.assert_not_called()

    default_path = Path("C:/Users/Administrator/AppData/Roaming/DSTCamp/cache")
    dummy._prompt_restart_after_cache_change = Mock()
    with (
        patch.object(resource_paths, "cache_root_dir", return_value=default_path),
        patch.object(
            resource_paths, "default_cache_root_dir", return_value=default_path
        ),
        patch.object(resource_paths, "validate_cache_root") as validate_cache,
        patch.object(gui_app, "set_cache_dir_override") as save_cache_dir,
        patch.object(gui_app.dlg, "show_info") as show_info,
    ):
        assert DSToolsApp._restore_default_cache_dir(dummy) is False
    validate_cache.assert_not_called()
    save_cache_dir.assert_not_called()
    dummy._prompt_restart_after_cache_change.assert_not_called()
    show_info.assert_called_once()
    assert str(default_path) in show_info.call_args.args[2]

    valid_path = Path("D:/DSTCampData/cache")
    dummy._show_cache_dir_error = Mock()
    prompt_restart = Mock()
    dummy._prompt_restart_after_cache_change = prompt_restart
    with (
        patch.object(resource_paths, "cache_root_dir", return_value=invalid_path),
        patch.object(
            resource_paths,
            "validate_cache_root",
            side_effect=["non_ascii", None],
        ),
        patch.object(
            gui_app.filedialog,
            "askdirectory",
            side_effect=[str(invalid_path), str(valid_path)],
        ) as ask_directory,
        patch.object(gui_app, "set_cache_dir_override") as save_cache_dir,
    ):
        assert DSToolsApp._choose_cache_dir(dummy) is True
    assert ask_directory.call_count == 2
    dummy._show_cache_dir_error.assert_called_once()
    save_cache_dir.assert_called_once_with(valid_path)
    assert str(valid_path) in prompt_restart.call_args.args[1]

    dummy._restart_app = Mock()
    with patch.object(gui_app.dlg, "ask_choice", return_value="restart"):
        DSToolsApp._prompt_restart_after_cache_change(dummy, dummy.root, "saved")
    dummy._restart_app.assert_called_once_with(dummy.root)

    import scripts.run_gui as run_gui

    with (
        patch.object(run_gui, "_wait_for_process_exit") as wait_for_exit,
        patch.object(run_gui.subprocess, "Popen") as start_process,
        patch.object(run_gui.sys, "frozen", True, create=True),
    ):
        run_gui._run_restart_helper(12345, ["--example"])
    wait_for_exit.assert_called_once_with(12345)
    assert start_process.call_args.args[0][-1] == "--example"
    assert (
        start_process.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"]
        == "1"
    )

    dummy._restart_helper_command = Mock(return_value=["DSTCamp.exe", "--helper"])
    dummy._quit_app = Mock()
    with (
        patch.object(gui_app.sys, "frozen", True, create=True),
        patch.object(gui_app.subprocess, "Popen") as start_helper,
    ):
        DSToolsApp._quit_and_restart(dummy)
    assert start_helper.call_args.args[0] == ["DSTCamp.exe", "--helper"]
    assert (
        start_helper.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"]
        == "1"
    )
    dummy._quit_app.assert_called_once_with()
    print("  PASS: 有效路径保存后可立即重启，辅助进程会等待旧实例退出")


def test_mod_sync_junction():
    """验证 Mod 目录联接的直接替换、解除复制和源目标保护。"""
    print("\n" + "=" * 60)
    print("Test 21: Mod Sync Junction")

    from unittest.mock import patch

    from dstools.features.mod.sync import (
        _ensure_junction,
        detach_mod_sync_junction,
        plan_mod_sync,
    )

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "client_mods" / "workshop-123"
        target = Path(tmp) / "server_mods" / "workshop-123"
        src.mkdir(parents=True)
        (src / "modinfo.lua").write_text("name = 'test'")

        _ensure_junction(target, src)
        assert os.path.isjunction(target), "target 应该变成指向 src 的目录联接"
        assert (target / "modinfo.lua").read_text() == "name = 'test'", (
            "透过联接应该能读到 src 里的真实内容"
        )
        print("  PASS: first call creates a junction pointing at the client mod folder")

        _ensure_junction(target, src)
        assert os.path.isjunction(target), "重复调用应该保持联接，不报错"
        print("  PASS: calling again on an already-correct junction is a no-op")

        # 已有真实文件夹按新规则直接删除，不再保留长期备份。
        real_target = Path(tmp) / "server_mods" / "workshop-456"
        real_src = Path(tmp) / "client_mods" / "workshop-456"
        real_src.mkdir(parents=True)
        (real_src / "modinfo.lua").write_text("name = 'legacy copy source'")
        real_target.mkdir(parents=True)
        (real_target / "modinfo.lua").write_text("stale copied content")

        replaced = _ensure_junction(real_target, real_src, allow_replace=True)
        assert os.path.isjunction(real_target), "已存在的真实文件夹应该被替换成联接"
        assert (
            real_target / "modinfo.lua"
        ).read_text() == "name = 'legacy copy source'", (
            "替换后应该读到 src 的内容，不是残留的旧复制内容"
        )
        assert real_src.exists() and (real_src / "modinfo.lua").exists(), (
            "删除 target 这个联接本身，绝不能牵连删除它指向的 src 真实内容"
        )
        assert replaced is True
        assert not list(real_target.parent.glob("mods.dstcamp-backup-*"))
        print(
            "  PASS: an existing real folder is deleted without backup and replaced by a junction"
        )

        # 用户确认的是永久删除；因此 mklink 后续失败时不会偷偷恢复或创建
        # 长期备份，但客户端源目录始终不得受影响。
        rollback_target = Path(tmp) / "server_mods" / "rollback"
        rollback_src = Path(tmp) / "client_mods" / "rollback"
        rollback_target.mkdir(parents=True)
        rollback_src.mkdir(parents=True)
        (rollback_target / "old.txt").write_text("keep")

        class FailedMklink:
            returncode = 1
            stderr = "forced failure"
            stdout = ""

        with patch(
            "dstools.features.mod.sync.subprocess.run", return_value=FailedMklink()
        ):
            try:
                _ensure_junction(rollback_target, rollback_src, allow_replace=True)
            except OSError:
                pass
            else:
                raise AssertionError("mklink 失败时应该抛出异常")
        assert not os.path.lexists(rollback_target)
        assert rollback_src.is_dir()
        print(
            "  PASS: failed junction creation does not restore a deleted server folder"
        )

        # 源目录和目标目录相同的场景必须在真正改动前拒绝。
        same_root = Path(tmp) / "same_install"
        same_target = same_root / "mods"
        same_target.mkdir(parents=True)
        plan = plan_mod_sync(same_root, same_target)
        assert plan.invalid_reason and not plan.needs_confirm_delete
        print("  PASS: same source and target are rejected before replacement")

        # 解除整目录联接时要完整复制客户端 mods，而不是恢复历史备份或只
        # 迁移某一类 V1 目录。
        install = Path(tmp) / "dedicated"
        server_mods = install / "mods"
        client_mods = Path(tmp) / "whole_client_mods"
        server_mods.mkdir(parents=True)
        client_mods.mkdir()
        (server_mods / "stock.txt").write_text("server stock", encoding="utf-8")
        (client_mods / "client-root.txt").write_text("client copy", encoding="utf-8")
        client_v1 = client_mods / "workshop-987654321"
        client_v1.mkdir()
        (client_v1 / "modinfo.lua").write_text('name = "Client V1"', encoding="utf-8")
        assert _ensure_junction(server_mods, client_mods, allow_replace=True) is True
        assert os.path.isjunction(server_mods)
        with patch(
            "dstools.features.mod.legacy_v1.running_dst_processes", return_value=()
        ):
            detached = detach_mod_sync_junction(install, client_mods)
        assert detached.removed and detached.copied
        assert not os.path.isjunction(server_mods)
        assert not (server_mods / "stock.txt").exists()
        assert (server_mods / "client-root.txt").read_text(
            encoding="utf-8"
        ) == "client copy"
        assert (server_mods / "workshop-987654321" / "modinfo.lua").is_file()
        assert set(detached.copied_entries) == {"client-root.txt", "workshop-987654321"}
        print("  PASS: removing the junction copies the complete client mods folder")

        # 复制阶段失败时不能先删联接；这是解除操作最重要的失败安全边界。
        failed_install = Path(tmp) / "failed_dedicated"
        failed_target = failed_install / "mods"
        _ensure_junction(failed_target, client_mods)
        with (
            patch(
                "dstools.features.mod.legacy_v1.running_dst_processes", return_value=()
            ),
            patch(
                "dstools.features.mod.sync.shutil.copytree",
                side_effect=OSError("forced copy failure"),
            ),
        ):
            failed = detach_mod_sync_junction(failed_install, client_mods)
        assert failed.errors and os.path.isjunction(failed_target)
        print("  PASS: a failed copy leaves the existing junction untouched")


def test_theme_set_theme():
    """测试 theme.py 的 set_theme()——实时切换主题的机制。纯逻辑（模块级
    颜色变量重新赋值），不需要真实 Tk 窗口。验证：(a) 切换主题（如
    gray/mint）确实会重新赋值调色板；(b) 未知主题名回退到 "gray" 而不是
    抛异常；(c) 背景图相关字段（BG_IMAGE_ENABLED）已经从 theme.py 彻底
    移除——背景图现在跟主题解耦，不管当前是哪个主题都会叠加显示（见
    custom_background.py）。"""
    print("\n" + "=" * 60)
    print("Test 22: Theme Live Switch")

    from dstools.shared.gui import theme

    original_primary = theme.PRIMARY
    try:
        theme.set_theme("gray")
        assert theme.PRIMARY == "#8A97A3"
        assert not hasattr(theme, "BG_IMAGE_ENABLED"), (
            "背景图已跟主题解耦，theme.py 不应再有这个字段"
        )
        assert theme.WINDOW_ALPHA == 1.0, (
            "整窗透明效果已经按用户要求去掉，只保留图片自身的透明度"
        )
        print("  PASS: set_theme() reassigns theme.py's module-level color constants")

        theme.set_theme("mint")
        assert theme.PRIMARY == "#6FCF97"
        theme.set_theme("gray")
        assert theme.PRIMARY == "#8A97A3"
        print("  PASS: switching between real themes (gray/mint) reassigns the palette")

        theme.set_theme("some_removed_theme_name")
        assert theme.PRIMARY == "#8A97A3"
        print("  PASS: unknown theme name falls back to gray instead of raising")
    finally:
        theme.set_theme("gray")
        theme.PRIMARY = original_primary  # 双保险，确保测试不影响后续状态


def test_world_reader_and_view_model():
    """世界 Lua 的读取状态、原子保存和 UI 无关视图模型必须可独立验证。"""
    print("\n" + "=" * 60)
    print("Test 23: World Reader and View Model")

    from dstools.features.world.reader import (
        LeveldataStatus,
        WorldPreset,
        load_leveldata,
        save_leveldata,
    )
    from dstools.features.world.view_model import (
        WorldDisplayOverride,
        build_world_view_model,
    )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        path = root / "leveldataoverride.lua"
        path.write_text(
            'return { id = "ENDLESS", name = "Endless", desc = "keep me", '
            'location = "forest", custom = { enabled = true }, '
            'overrides = { day = "default", autumn = "longseason" } }',
            encoding="utf-8",
        )
        result = load_leveldata(path)
        assert result.status == LeveldataStatus.OK and result.preset is not None
        preset = result.preset
        preset.overrides[0].value = "onlyday"
        save_leveldata(preset, path)
        reloaded = load_leveldata(path)
        assert reloaded.status == LeveldataStatus.OK and reloaded.preset is not None
        assert reloaded.preset.description == "keep me"
        assert reloaded.preset.raw["custom"]["enabled"] is True
        assert {item.key: item.value for item in reloaded.preset.overrides}[
            "day"
        ] == "onlyday"
        assert not list(root.glob("*.tmp")), "原子写入完成后不应遗留临时文件"

        assert load_leveldata(root / "missing.lua").status == LeveldataStatus.MISSING
        invalid = root / "invalid.lua"
        invalid.write_text("return { overrides = {", encoding="utf-8")
        assert load_leveldata(invalid).status == LeveldataStatus.INVALID

    view = build_world_view_model(WorldPreset(location="forest"), {}, [])
    rows = [row for items in view.rules_by_category.values() for row in items]
    day = next(row for row in rows if row.key == "day")
    assert isinstance(day, WorldDisplayOverride) and day.persisted is False
    print(
        "  PASS: world I/O preserves metadata, reports errors, and uses explicit display defaults"
    )


def test_world_catalog_audit_and_cave_hidden_forest_sections():
    """洞穴共享项需被识别；未知项必须被审计报告而非伪造为可编辑设置。"""
    print("\n" + "=" * 60)
    print("Test 24: World Catalog Audit")

    from dstools.features.world.audit import audit_leveldata_paths
    from dstools.features.world.categories import get_setting_info

    assert get_setting_info("day", "cave")[0] == "other"
    assert get_setting_info("basicresource_regrowth", "cave")[0] == "other"
    assert get_setting_info("roads", "cave")[0] == "other"
    assert get_setting_info("day", "porkland")[0] == "global", (
        "day 是 world=nil 且未被 delete_items 删除的全局项"
    )
    assert get_setting_info("butterfly", "porkland")[0] == "creatures", (
        "butterfly 在猪镇白名单里"
    )
    assert get_setting_info("specialevent", "porkland")[0] == "other", (
        "猪镇 Mod 明确删除了该设置"
    )
    assert get_setting_info("layout_mode", "porkland")[0] == "other", (
        "地图内部元数据不能伪造为设置"
    )

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "leveldataoverride.lua"
        path.write_text(
            'return { location = "cave", overrides = { day = "default", roads = "default", '
            'layout_mode = "RestrictNodesByKey" } }',
            encoding="utf-8",
        )
        report = audit_leveldata_paths([path])
    cave = report.by_location["cave"]
    assert report.statuses == {"ok": 1}
    assert cave.recognized_overrides == 0
    assert cave.unknown_keys == {"day", "layout_mode", "roads"}
    print(
        "  PASS: catalog audit separates verified settings from preserved unknown metadata"
    )


def test_world_catalog_layers_are_isolated():
    """原版目录与猪镇 Mod 覆盖层必须分离。"""
    print("\n" + "=" * 60)
    print("Test 25: World Catalog Layer Isolation")

    from dstools.features.world.catalog_resolver import resolve_vanilla_settings
    from dstools.features.world.categories import FOREST_RULES_DICT, get_setting_info

    assert "specialevent" in FOREST_RULES_DICT
    assert "specialevent" not in resolve_vanilla_settings("porkland", True)
    assert "day" in resolve_vanilla_settings("porkland", True)
    assert "butterfly" in resolve_vanilla_settings("porkland", True)
    assert get_setting_info("specialevent", "porkland")[0] == "other"
    assert get_setting_info("day", "porkland")[0] == "global"
    assert get_setting_info("butterfly", "porkland")[0] == "creatures"
    assert get_setting_info("season_start", "porkland")[0] == "other"
    assert get_setting_info("regrowth", "porkland")[0] == "other"
    print(
        "  PASS: vanilla catalog remains unchanged and Porkland uses an isolated whitelist overlay"
    )


def test_porkland_location_selector():
    """世界选择器只切换 Master 身份，不污染洞穴或 overrides。"""
    print("\n" + "=" * 60)
    print("Test 27: Porkland World Location Selector")
    from dstools.features.world.location_selector import (
        available_master_locations,
        select_master_location,
    )
    from dstools.features.world.reader import WorldOverride, WorldPreset

    assert available_master_locations(set()) == ("forest",)
    # 3322803908 的发布版 modservercreationmain.lua 把 Master 候选表
    # 明确写成仅 PORKLAND；森林只在开发模式/显式开关下才会加入。
    assert available_master_locations({"workshop-3322803908"}) == ("porkland",)
    preset = WorldPreset(
        preset_id="SURVIVAL_TOGETHER",
        name="地上",
        location="forest",
        overrides=[WorldOverride(key="task_set", value="default")],
        raw={"location": "forest", "overrides": {"task_set": "default"}},
    )
    porkland = select_master_location(preset, "porkland")
    assert porkland.location == "porkland"
    assert porkland.preset_id == "PORKLAND_DEFAULT"
    assert porkland.name == "猪镇"
    assert porkland.raw["location"] == "porkland"
    assert porkland.overrides == preset.overrides
    assert preset.location == "forest"
    print("  PASS: location selection is isolated and reversible")


def test_world_creation_plan_and_atomic_writer():
    """创建层生成双世界目录，拒绝覆盖并可回读。"""
    print("\n" + "=" * 60)
    print("Test 28: World Creation Plan and Writer")
    from dstools.features.world.creation import (
        WorldCreationPlan,
        WorldShardPlan,
        create_world,
    )
    from dstools.features.world.reader import load_leveldata

    with tempfile.TemporaryDirectory() as td:
        plan = WorldCreationPlan(
            cluster_name="Cluster_Porkland_Test",
            mod_ids=frozenset({"3322803908"}),
            master=WorldShardPlan("porkland", "PORKLAND_DEFAULT", "猪镇", "危险丛林"),
            caves=WorldShardPlan("cave", "DST_CAVE", "洞穴"),
        )
        path = create_world(plan, Path(td))
        master = load_leveldata(path / "Master" / "leveldataoverride.lua").preset
        caves = load_leveldata(path / "Caves" / "leveldataoverride.lua").preset
        assert master and master.location == "porkland"
        assert caves and caves.location == "cave"
        assert (path / "cluster.ini").exists()
        mod_overrides = (path / "Master" / "modoverrides.lua").read_text(
            encoding="utf-8"
        )
        assert "workshop-3322803908" in mod_overrides and "enabled" in mod_overrides
        try:
            create_world(plan, Path(td))
        except FileExistsError:
            pass
        else:
            raise AssertionError("existing cluster must not be overwritten")
    print("  PASS: atomic creation and read-back validation")


def test_world_categories_bilingual():
    """测试 categories.py 的 get_setting_info()/get_categories() 会根据当
    前 i18n 语言返回中/英文名——对应修复过的 bug"世界设置切英文不生
    效"。"""
    print("\n" + "=" * 60)
    print("Test 23: World Categories Bilingual")

    from dstools.features.world.categories import get_setting_info, get_categories
    from dstools.i18n import get_lang, set_lang

    original_lang = get_lang()
    try:
        set_lang("zh")
        cat, is_rule, name = get_setting_info("day", "forest")
        assert (cat, is_rule, name) == ("global", True, "昼夜选项")
        categories = dict(get_categories("forest", "rules"))
        assert categories["global"] == "全局"
        print("  PASS: zh returns Chinese names")

        set_lang("en")
        cat, is_rule, name = get_setting_info("day", "forest")
        assert (cat, is_rule, name) == ("global", True, "Day/Night Cycle")
        categories = dict(get_categories("forest", "rules"))
        assert categories["global"] == "General"
        print("  PASS: en returns English names")

        # 未知 key 兜底：分类 "other"，名字原样回退成 key 本身。
        cat, is_rule, name = get_setting_info("totally_unknown_key_xyz", "forest")
        assert (cat, is_rule, name) == ("other", False, "totally_unknown_key_xyz")
        print('  PASS: unknown key falls back to ("other", False, key)')
    finally:
        set_lang(original_lang)


def test_world_ocean_frequency_labels():
    """真机反馈过的真实 bug："世界设置→世界生成(仅查看)→敌对生物以及
    刷新点"里"海草"(ocean_waterplant)这一项的取值直接显示成了原始字
    符串 "ocean_default"，没翻译成中文。查过游戏自己的
    scripts/map/customize.lua 源码确认：ocean_waterplant/ocean_seastack
    这两个字段用的是"ocean_"+普通频率词的取值集合（never/rare/
    uncommon/default/often/mostly/always/insane 各自加上 "ocean_" 前
    缀），显示文案跟不带前缀的版本完全一样（源码里是
    `{text = data.text, data = "ocean_"..data.data}`，文案字段原样复
    用）。之前 render.py 的 _VALUE_LABELS 只补了 "ocean_uncommon" 一
    个，漏了其它 7 档，包括这次实际触发问题的 "ocean_default"。"""
    print("\n" + "=" * 60)
    print("Test 38: World Ocean Frequency Value Labels")

    from dstools.features.world.render import get_value_label

    expected = {
        "ocean_never": "无",
        "ocean_rare": "很少",
        "ocean_uncommon": "较少",
        "ocean_default": "默认",
        "ocean_often": "较多",
        "ocean_mostly": "很多",
        "ocean_always": "大量",
        "ocean_insane": "疯狂",
    }
    for raw_value, zh_label in expected.items():
        got = get_value_label("ocean_waterplant", raw_value)
        assert got == zh_label, f"{raw_value} 应该翻译成 {zh_label!r}，实际是 {got!r}"
        assert got != raw_value, f"{raw_value} 不应该原样透出未翻译的原始字符串"
    print(
        "  PASS: get_value_label() 正确翻译全部 8 档 ocean_ 前缀频率取值，不再原样透出原始字符串"
    )


def test_custom_background():
    """测试 custom_background.py 的按比例裁剪（绝不拉伸）+ 不透明度混合逻
    辑——对应需求"支持自定义背景图，按比例裁剪不拉伸，可调不透明度贴合
    主题"。纯 PIL 逻辑，不需要真实 Tk 窗口。"""
    print("\n" + "=" * 60)
    print("Test 24: Custom Background Image")

    from PIL import Image

    from dstools.shared.custom_background import (
        _center_crop_to_ratio,
        render_background,
    )

    # 宽图裁窄比例：裁掉左右两侧，裁完的宽高比必须刚好等于目标比例
    # （不是拉伸变形出来的），且没有超出原图尺寸。
    wide = Image.new("RGB", (400, 100), "red")
    cropped = _center_crop_to_ratio(wide, 1.0)
    assert cropped.size == (100, 100), (
        "Wide image cropped to a square must trim the sides, not stretch"
    )
    print("  PASS: wider-than-target image is center-cropped on the sides")

    # 高图裁宽比例：裁掉上下两侧。
    tall = Image.new("RGB", (100, 400), "blue")
    cropped2 = _center_crop_to_ratio(tall, 2.0)
    assert cropped2.size == (100, 50), (
        "Tall image cropped to a wide ratio must trim top/bottom, not stretch"
    )
    print("  PASS: taller-than-target image is center-cropped on top/bottom")

    # 不透明度混合的两个边界：0 = 完全是主题纯色（图片全隐），1 = 完全是原图。
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "bg.png"
        Image.new("RGB", (50, 50), (255, 0, 0)).save(src)

        transparent = render_background(src, 20, 20, 0.0, "#00FF00")
        assert transparent.getpixel((10, 10)) == (0, 255, 0), (
            "opacity=0 must show only the theme's blend color"
        )

        opaque = render_background(src, 20, 20, 1.0, "#00FF00")
        assert opaque.getpixel((10, 10)) == (255, 0, 0), (
            "opacity=1 must show only the original image"
        )
        print("  PASS: opacity=0/1 blend to pure theme color / pure image respectively")


def test_mod_resolve_cache():
    """测试 cache.py 里给 resolve_full_modinfo() 结果做的磁盘持久化缓存
    ——加这层缓存是因为 Lua 沙箱全量解析之前只在内存里缓存一份，每次重
    启应用都要为没变过的 mod 重新跑一遍（真机反馈过"启动要卡 3 秒"，
    profile 出来这是大头之一）。这里只测纯数据逻辑（mtime 失效判断 +
    JSON 往返，含 ModConfigOption 这个 dataclass 的序列化/反序列化），
    不需要真的跑一遍 Lua 沙箱。"""
    print("\n" + "=" * 60)
    print("Test 25: Mod Resolve Cache")

    # _isolated_settings_dir() 必须在 import mod.cache 之前进
    # 入——那个模块的 _CACHE_DIR 是 import 时算好的模块级常量
    # （cache_dir("mod_full_resolve")），只有在补丁生效期间第一次
    # import 才能让它落在隔离的临时目录里，不写真实的
    # %APPDATA%/DSTCamp/cache/。
    with _isolated_settings_dir():
        from dstools.features.mod.cache import load_cached_result, save_result
        from dstools.features.mod.parser import ModConfigOption

        workshop_id = "test-workshop-resolve-cache"
        with tempfile.TemporaryDirectory() as tmp:
            modinfo_path = Path(tmp) / "modinfo.lua"
            modinfo_path.write_text("name = 'x'", encoding="utf-8")

            assert load_cached_result(workshop_id, modinfo_path) is None
            print("  PASS: no cache yet returns None")

            result = {
                "name": "测试Mod",
                "config_options": [
                    ModConfigOption(name="opt1", label="选项1", default="a")
                ],
            }
            save_result(workshop_id, result)
            cached = load_cached_result(workshop_id, modinfo_path)
            assert cached is not None and cached["name"] == "测试Mod"
            assert isinstance(cached["config_options"][0], ModConfigOption)
            assert cached["config_options"][0].name == "opt1"
            print(
                "  PASS: save/load round-trips config_options as real ModConfigOption objects"
            )

            # modinfo.lua 比缓存新——缓存失效，返回 None，跟 icons.py
            # 图标缓存同一套 mtime 判断逻辑。
            future = time.time() + 100
            os.utime(modinfo_path, (future, future))
            assert load_cached_result(workshop_id, modinfo_path) is None
            print("  PASS: cache invalidated once modinfo.lua's mtime moves past it")

            # 真机复现过的坑：缓存文件没有 _cache_format_version 字段
            # （模拟 ModConfigOption 加 client/is_set_config 这几个新字
            # 段之前生成的旧缓存）——mtime 没过期不代表内容对当前代码仍
            # 然正确，缺这层版本号判断的话旧缓存会被当成"仍然新鲜"直接
            # 复用，新加的字段永远读不到（表现为"明明修了 bug，但界面还
            # 是老样子"）。这里手工写一份没有版本号的旧格式缓存，mtime
            # 故意设置得比 modinfo.lua 更新，确保测的是版本号判断本身，
            # 不是又测了一遍上面的 mtime 判断。
            from dstools.features.mod.cache import _cache_path

            stale_path = _cache_path(workshop_id)
            stale_path.write_text(
                json.dumps(
                    {
                        "name": "旧格式测试Mod",
                        "config_options": [
                            {"name": "opt1", "label": "选项1", "default": "a"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            newer = time.time() + 200
            os.utime(stale_path, (newer, newer))
            assert load_cached_result(workshop_id, modinfo_path) is None, (
                "没有 _cache_format_version 的旧格式缓存应该被当成失效"
            )
            print(
                "  PASS: 没有 _cache_format_version 的旧格式缓存被判定失效，强制重新走一遍 sandbox"
            )


def test_mod_version_resolution():
    """版本号必须来自完整成功执行后的最终值，失败时不采用中间值。"""
    print("\n" + "=" * 60)
    print("Test 25b: Trusted Mod Version Resolution")

    from dstools.features.mod.local_version import (
        VERSION_CONFIRMED,
        VERSION_UNDECLARED,
        VERSION_UNRESOLVED,
        normalize_version_for_compare,
        normalize_version_result,
        resolve_local_mod_version,
    )
    from dstools.features.mod.sandbox import resolve_mod_version, resolve_mod_versions
    from dstools.features.mod import version_cache

    result = resolve_mod_versions(
        'local prefix = "1."\nlocal title = "Dynamic Mod"\nname = title\n'
        'version = prefix .. "2.3"\n'
        'version = version .. "-final"\nversion_compatible = "1.2"',
        folder_name="workshop-123",
    )
    assert result == {
        "name": {"declared": True, "value": "Dynamic Mod"},
        "icon": {"declared": False},
        "icon_atlas": {"declared": False},
        "version": {"declared": True, "value": "1.2.3-final"},
        "version_compatible": {"declared": True, "value": "1.2"},
    }
    normalized = normalize_version_result(result, "sandbox")
    assert normalized.name == "Dynamic Mod"
    assert normalized.name_status == VERSION_CONFIRMED
    assert normalized.version == "1.2.3-final"
    assert normalized.status == VERSION_CONFIRMED
    assert normalized.version_compatible == "1.2"
    assert normalized.compatible_status == VERSION_CONFIRMED
    assert normalized.source == "sandbox"
    print("  PASS: 同一次完整执行取得最终名称、版本和兼容版本")

    conditional = resolve_mod_version(
        'version = folder_name == "workshop-123" and "workshop" or "local"',
        folder_name="workshop-123",
    )
    assert conditional == {"declared": True, "value": "workshop"}
    print("  PASS: folder_name 按真实 Workshop 标识注入")

    assert resolve_mod_versions('version = "temporary"\nmissing_engine_api()') is None
    assert normalize_version_result(None, "sandbox").status == VERSION_UNRESOLVED
    print("  PASS: 完整脚本失败时不采用报错前的临时版本")

    undeclared = resolve_mod_versions('name = "No Version"')
    undeclared_result = normalize_version_result(undeclared, "sandbox")
    assert undeclared_result.status == VERSION_UNDECLARED
    assert undeclared_result.compatible_status == VERSION_UNDECLARED
    invalid = normalize_version_result(
        {
            "version": {"declared": True, "value": True},
            "version_compatible": {"declared": False},
        },
        "sandbox",
    )
    assert invalid.status == VERSION_UNRESOLVED
    print("  PASS: 未声明与非法类型被明确区分")

    fallback = normalize_version_result(
        {
            "version": {"declared": True, "value": " V1.2.3 "},
            "version_compatible": {"declared": False},
        },
        "sandbox",
    )
    assert fallback.effective_version_compatible == "V1.2.3"
    assert fallback.compare_version == "v1.2.3"
    assert fallback.compare_version_compatible == "v1.2.3"
    assert normalize_version_for_compare(" V1.2.3 ") == "v1.2.3"
    commented = normalize_version_result(
        {
            "version": {"declared": True, "value": "0.0.6  --版本"},
            "version_compatible": {"declared": False},
        },
        "sandbox",
    )
    assert commented.version == "0.0.6"
    print("  PASS: 比较值按游戏逻辑去空白并转小写，兼容版本未声明时回退版本")

    with tempfile.TemporaryDirectory() as tmp:
        old_cache_dir = version_cache._CACHE_DIR
        version_cache._CACHE_DIR = Path(tmp) / "cache"
        try:
            first_dir = Path(tmp) / "source-a"
            second_dir = Path(tmp) / "source-b"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "modinfo.lua"
            second = second_dir / "modinfo.lua"
            first.write_text('version = "1"', encoding="utf-8")
            second.write_text('version = "1"', encoding="utf-8")
            cached_result = {
                "version": {"declared": True, "value": "1"},
                "version_compatible": {"declared": False},
            }
            version_cache.save_version_result(
                "workshop-123", first, "workshop-123", cached_result
            )
            assert (
                version_cache.load_version_result("workshop-123", first, "workshop-123")
                == cached_result
            )
            assert (
                version_cache.load_version_result(
                    "workshop-123", second, "workshop-123"
                )
                is None
            )
            first.write_text('version = "2"', encoding="utf-8")
            assert (
                version_cache.load_version_result("workshop-123", first, "workshop-123")
                is None
            )
            first.write_text(
                'version = "2"\nversion_compatible = "1"', encoding="utf-8"
            )
            local = resolve_local_mod_version("workshop-123", first_dir, "workshop-123")
            assert local.version == "2" and local.version_compatible == "1"
            assert local.status == VERSION_CONFIRMED
        finally:
            version_cache._CACHE_DIR = old_cache_dir
    print("  PASS: 缓存同时校验 SHA-256 与来源路径，不会跨副本串值")


def test_workshop_source_details_parser():
    """源端详情使用宽缓冲区读取，稳定字段偏移必须和 Steam SDK 一致。"""
    print("\n" + "=" * 60)
    print("Test 25c: Workshop Source Details Buffer")

    import struct
    from dstools.features.mod.workshop_api import (
        _parse_ugc_details_buffer,
        workshop_version_from_details,
    )

    raw = bytearray(32768)
    struct.pack_into("<Q", raw, 0, 3485293431)
    struct.pack_into("<i", raw, 8, 1)
    struct.pack_into("<I", raw, 16, 245850)
    struct.pack_into("<I", raw, 20, 322330)
    raw[24 : 24 + len("测试 Mod".encode())] = "测试 Mod".encode()
    struct.pack_into("<I", raw, 8168, 100)
    struct.pack_into("<I", raw, 8172, 200)
    tags = b"server_only,gameplay,version:1.4.3"
    raw[8187 : 8187 + len(tags)] = tags
    struct.pack_into("<Q", raw, 9216, 123456)
    raw[9232 : 9232 + len(b"content.zip")] = b"content.zip"
    struct.pack_into("<i", raw, 9492, 654321)
    details = _parse_ugc_details_buffer(bytes(raw))
    assert details.workshop_id == 3485293431
    assert details.title == "测试 Mod"
    assert details.creator_app_id == 245850 and details.consumer_app_id == 322330
    assert details.time_created == 100 and details.time_updated == 200
    assert details.tags == ("server_only", "gameplay", "version:1.4.3")
    assert workshop_version_from_details(details) == "1.4.3"
    assert details.content_handle == 123456
    assert details.filename == "content.zip" and details.file_size == 654321
    print("  PASS: 源端详情稳定字段从宽缓冲区正确解析")


def test_workshop_status_evidence_priority():
    """实际目录、版本和 Manifest 证据必须覆盖 Steam 的陈旧 Installed 位。"""
    print("\n" + "=" * 60)
    print("Test 25d: Workshop Status Evidence Priority")

    from dstools.features.mod.local_version import LocalModVersion, VERSION_CONFIRMED
    from dstools.features.mod.workshop_api import (
        WorkshopInstallInfo,
        WorkshopItemDetails,
        WorkshopItemState,
    )
    from dstools.features.mod.workshop_status import (
        WorkshopModEvidence,
        WorkshopModState,
        evaluate_workshop_status,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        missing = root / "3485293431"
        stale = WorkshopModEvidence(
            workshop_id=3485293431,
            steam_state=WorkshopItemState(5),
            install_info=WorkshopInstallInfo(missing, 100, 1),
        )
        status = evaluate_workshop_status(stale)
        assert status.state == WorkshopModState.MISSING
        assert "Steam 仍标记为已安装" in status.reasons[0]
        print("  PASS: flags=5 但实际目录不存在时判定文件缺失")

        installed = root / "installed"
        installed.mkdir()
        (installed / "modinfo.lua").write_text('version = "V1.2.3"', encoding="utf-8")
        version = LocalModVersion(
            "V1.2.3", VERSION_CONFIRMED, "", "undeclared", "sandbox"
        )
        current = WorkshopModEvidence(
            workshop_id=1,
            steam_state=WorkshopItemState(5),
            install_info=WorkshopInstallInfo(installed, 100, 1),
            source_version=version,
            source_details=WorkshopItemDetails(1, 1, time_updated=1),
        )
        assert evaluate_workshop_status(current).state == WorkshopModState.CURRENT
        print("  PASS: 目录与 modinfo 存在、版本可信且 Steam 无更新时判定最新")

        suspected = WorkshopModEvidence(
            **{**current.__dict__, "source_details": None}
        )
        suspected_status = evaluate_workshop_status(suspected)
        assert suspected_status.state == WorkshopModState.SUSPECTED_OUTDATED
        assert suspected_status.needs_action and suspected_status.can_update

        steam_update = WorkshopModEvidence(
            **{**current.__dict__, "steam_state": WorkshopItemState(13)}
        )
        assert (
            evaluate_workshop_status(steam_update).state
            == WorkshopModState.UPDATE_AVAILABLE
        )
        cached_update = WorkshopModEvidence(
            **{**current.__dict__, "cached_manifest_version": "1.2.2"}
        )
        cached_status = evaluate_workshop_status(cached_update)
        assert cached_status.state == WorkshopModState.UPDATE_AVAILABLE
        assert cached_status.remote_version == "1.2.2"
        exact_compare = WorkshopModEvidence(
            **{**current.__dict__, "cached_manifest_version": "1.2.3"}
        )
        assert (
            evaluate_workshop_status(exact_compare).state
            == WorkshopModState.UPDATE_AVAILABLE
        ), "不能擅自忽略本地 V 前缀"
        exact_current = WorkshopModEvidence(
            **{**current.__dict__, "cached_manifest_version": "V1.2.3"}
        )
        assert evaluate_workshop_status(exact_current).state == WorkshopModState.CURRENT
        case_only = WorkshopModEvidence(
            **{**current.__dict__, "cached_manifest_version": "v1.2.3"}
        )
        assert evaluate_workshop_status(case_only).state == WorkshopModState.CURRENT, (
            "Steam version 标签会转小写，纯大小写差异不能误报更新"
        )
        live_remote = WorkshopModEvidence(
            **{
                **current.__dict__,
                "source_details": WorkshopItemDetails(
                    1,
                    1,
                    time_updated=1,
                    tags=("all_clients_require_mod", "version:V1.2.3"),
                ),
                "remote_version": "V1.2.3",
                "remote_version_source": "steam_workshop_tag",
                "cached_manifest_version": "0.9",
            }
        )
        live_status = evaluate_workshop_status(live_remote)
        assert live_status.state == WorkshopModState.CURRENT
        assert live_status.remote_version == "V1.2.3", (
            "实时 Steam 标签必须优先于旧 Klei 缓存"
        )
        active_update = WorkshopModEvidence(
            **{
                **current.__dict__,
                "active_path": installed,
                "active_version": LocalModVersion(
                    "1.2.2", VERSION_CONFIRMED, "", "undeclared", "sandbox"
                ),
            }
        )
        assert (
            evaluate_workshop_status(active_update).state
            == WorkshopModState.UPDATE_AVAILABLE
        )
        print("  PASS: Steam 更新位、游戏缓存版本和服务器实际版本均可触发更新")

        corrupt = WorkshopModEvidence(
            **{
                **current.__dict__,
                "manifest_valid": False,
                "manifest_error": "缺少 scripts/main.lua",
            }
        )
        corrupt_status = evaluate_workshop_status(corrupt)
        assert corrupt_status.state == WorkshopModState.CURRENT
        assert "缺少 scripts/main.lua" in corrupt_status.reasons
        print("  PASS: Manifest 弱证据不会覆盖 Steam 的更新状态结论")

        legacy_file = root / "521637598935453868_legacy.bin"
        legacy_file.write_bytes(b"legacy workshop payload")
        legacy = WorkshopModEvidence(
            workshop_id=463952377,
            steam_state=WorkshopItemState(7),
            install_info=WorkshopInstallInfo(
                legacy_file, legacy_file.stat().st_size, 1
            ),
            legacy_package_valid=True,
            legacy_package_version=LocalModVersion(
                "1.0", VERSION_CONFIRMED, "", "undeclared", "legacy_package"
            ),
        )
        legacy_status = evaluate_workshop_status(legacy)
        assert legacy_status.state == WorkshopModState.LEGACY_PACKAGE_READY
        assert legacy_status.can_update
        assert "首次加载" in legacy_status.reasons[0]
        legacy_runtime = root / "workshop-463952377"
        legacy_runtime.mkdir()
        (legacy_runtime / "modinfo.lua").write_text('version = "1.0"', encoding="utf-8")
        legacy_complete = WorkshopModEvidence(
            **{
                **legacy.__dict__,
                "discovered_path": legacy_runtime,
                "source_version": LocalModVersion(
                    "1.0", VERSION_CONFIRMED, "", "undeclared", "sandbox"
                ),
                "legacy_package_valid": True,
            }
        )
        assert (
            evaluate_workshop_status(legacy_complete).state == WorkshopModState.CURRENT
        )
        stale_remote_legacy = WorkshopModEvidence(
            **{
                **legacy_complete.__dict__,
                "remote_version": "9.9",
                "cached_manifest_version": "8.8",
            }
        )
        stale_remote_status = evaluate_workshop_status(stale_remote_legacy)
        assert stale_remote_status.state == WorkshopModState.CURRENT
        assert stale_remote_status.remote_version == "1.0"
        runtime_mismatch = WorkshopModEvidence(
            **{
                **legacy_complete.__dict__,
                "source_version": LocalModVersion(
                    version="1.2", status=VERSION_CONFIRMED
                ),
            }
        )
        runtime_mismatch_status = evaluate_workshop_status(runtime_mismatch)
        assert runtime_mismatch_status.state == WorkshopModState.UPDATE_AVAILABLE
        assert runtime_mismatch_status.update_expected_version == "1.0"
        steam_update = WorkshopModEvidence(
            **{
                **legacy_complete.__dict__,
                "steam_state": WorkshopItemState(15),
            }
        )
        steam_update_status = evaluate_workshop_status(steam_update)
        assert steam_update_status.state == WorkshopModState.UPDATE_AVAILABLE
        assert steam_update_status.remote_version == "1.0"
        assert steam_update_status.update_expected_version == ""
        legacy_server_missing = WorkshopModEvidence(
            **{
                **legacy_complete.__dict__,
                "active_path": root / "server-mods" / "workshop-463952377",
                "active_version": LocalModVersion(),
            }
        )
        server_missing_status = evaluate_workshop_status(legacy_server_missing)
        assert server_missing_status.state == WorkshopModState.LEGACY_PACKAGE_READY
        legacy_modified = WorkshopModEvidence(
            **{
                **legacy_complete.__dict__,
                "source_version": LocalModVersion(
                    "1.1", VERSION_CONFIRMED, "", "undeclared", "sandbox"
                ),
                "legacy_package_version": LocalModVersion(
                    "1.0", VERSION_CONFIRMED, "", "undeclared", "legacy_package"
                ),
            }
        )
        modified_status = evaluate_workshop_status(legacy_modified)
        assert modified_status.state == WorkshopModState.UPDATE_AVAILABLE
        assert modified_status.remote_version == "1.0"
        assert "Legacy 下载包不同" in modified_status.reasons[0]
        print("  PASS: LegacyItem 必须同时具备有效下载包和已解压运行目录")

        local_only = WorkshopModEvidence(
            workshop_id=2,
            steam_state=WorkshopItemState(0),
            discovered_path=installed,
            source_version=version,
        )
        local_status = evaluate_workshop_status(local_only)
        assert local_status.state == WorkshopModState.LOCAL_FILES
        assert local_status.local_path == installed
        print("  PASS: 发现真实目录但无 Steam 记录时标记仅有本地文件")

        empty_folder = root / "empty-workshop-folder"
        empty_folder.mkdir()
        empty_leftover = WorkshopModEvidence(
            workshop_id=3,
            steam_state=WorkshopItemState(0),
            discovered_path=empty_folder,
        )
        assert (
            evaluate_workshop_status(empty_leftover).state
            == WorkshopModState.NOT_INSTALLED
        )
        print("  PASS: 无 Steam 记录的空目录判定为未安装而不是文件缺失")

        unavailable = WorkshopModEvidence(
            workshop_id=2428854303,
            steam_state=WorkshopItemState(0),
            source_details=WorkshopItemDetails(2428854303, 15),
        )
        unavailable_status = evaluate_workshop_status(unavailable)
        assert unavailable_status.state == WorkshopModState.SOURCE_UNAVAILABLE
        assert "EResult=15" in unavailable_status.reasons[0]
        print("  PASS: Steam 拒绝访问且无本地文件时判定源端不可用")


def test_workshop_snapshot_uses_one_steam_session():
    """组合刷新必须只初始化一次 SteamAPI，标题失败不能丢本地证据。"""
    print("\n" + "=" * 60)
    print("Test 25d2: Workshop Snapshot Single Session")

    import dstools.features.mod.workshop_api as workshop_api

    opened = []

    class FakeSession:
        def __init__(self, dll_path, backend):
            opened.append((dll_path, backend))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def item_state(self, workshop_id):
            return workshop_api.WorkshopItemState(5)

        def subscribed_item_ids(self):
            return [11, 22, 33]

        def item_install_details(self, workshop_id):
            return workshop_api.WorkshopInstallInfo(
                Path(f"C:/workshop/{workshop_id}"), 123, 456
            )

        def query_item_details(self, workshop_ids, timeout=20.0):
            return [
                workshop_api.WorkshopItemDetails(
                    workshop_id=item, result=1, title=f"Mod {item}"
                )
                for item in workshop_ids
            ]

    original = workshop_api.SteamWorkshopSession
    with tempfile.TemporaryDirectory() as tmp:
        dll = Path(tmp) / "steam_api64.dll"
        dll.write_bytes(b"fake")
        try:
            workshop_api.SteamWorkshopSession = FakeSession
            states, installs, details = (
                workshop_api._get_workshop_item_snapshot_in_process(
                    [11, 22], detail_ids=[22], dll_path=dll
                )
            )
        finally:
            workshop_api.SteamWorkshopSession = original
    assert len(opened) == 1
    assert set(states) == {11, 22} and set(installs) == {11, 22}
    assert set(details) == {22} and details[22].title == "Mod 22"
    print("  PASS: 状态、安装记录和缺失标题共用一次 Steam 会话")

    opened.clear()
    with tempfile.TemporaryDirectory() as tmp:
        dll = Path(tmp) / "steam_api64.dll"
        dll.write_bytes(b"fake")
        try:
            workshop_api.SteamWorkshopSession = FakeSession
            states, installs, details = (
                workshop_api._get_workshop_item_snapshot_in_process(
                    [11, 22], dll_path=dll, include_subscribed=True
                )
            )
        finally:
            workshop_api.SteamWorkshopSession = original
    assert len(opened) == 1
    assert set(states) == {11, 22, 33} and set(installs) == {11, 22, 33}
    assert set(details) == {33} and details[33].title == "Mod 33"
    print("  PASS: ACF 与目录均缺失的订阅项目仍由 Steam 账号枚举补回")

    class FailingTitleSession(FakeSession):
        def query_item_details(self, workshop_ids, timeout=20.0):
            raise TimeoutError("模拟标题查询超时")

    opened.clear()
    with tempfile.TemporaryDirectory() as tmp:
        dll = Path(tmp) / "steam_api64.dll"
        dll.write_bytes(b"fake")
        try:
            workshop_api.SteamWorkshopSession = FailingTitleSession
            states, installs, details = (
                workshop_api._get_workshop_item_snapshot_in_process(
                    [11], detail_ids=[11], dll_path=dll
                )
            )
        finally:
            workshop_api.SteamWorkshopSession = original
    assert len(opened) == 1
    assert set(states) == {11} and set(installs) == {11} and details == {}
    print("  PASS: 标题查询失败仍保留状态和安装记录")


def test_dst_mod_manifest_verification():
    """MNFS 路径哈希必须识别缺失文件，同时允许 Mod 运行时产生额外文件。"""
    print("\n" + "=" * 60)
    print("Test 25e: DST Mod Manifest Verification")

    import struct
    from dstools.features.mod.workshop_manifest import (
        ManifestFormatError,
        find_cached_manifest_versions,
        load_mod_manifest,
        parse_mod_manifest_bytes,
        read_cached_manifest_version,
        sdbm_path_hash,
        verify_mod_manifest,
    )

    assert sdbm_path_hash("modinfo.lua") == 0xCD796EDA
    assert sdbm_path_hash("scripts/components/smart_minisign.lua") == 0x11E699C6
    assert sdbm_path_hash("修改者指南.txt") == 0x847C5105
    print("  PASS: ASCII 与中文路径哈希均和真实 Workshop Manifest 条目一致")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mod = root / "workshop-123"
        scripts = mod / "scripts"
        scripts.mkdir(parents=True)
        (mod / "modinfo.lua").write_text('version = "1"', encoding="utf-8")
        (scripts / "main.lua").write_text("return true", encoding="utf-8")
        hashes = (
            sdbm_path_hash("modinfo.lua"),
            sdbm_path_hash("scripts/main.lua"),
            sdbm_path_hash("mod.manifest"),
        )
        (mod / "mod.manifest").write_bytes(
            struct.pack("<4sII3I", b"MNFS", 1, 3, *hashes)
        )
        parsed = load_mod_manifest(mod / "mod.manifest")
        assert parsed.path_hashes == hashes
        assert verify_mod_manifest(mod).valid is True

        (mod / "runtime-cache.txt").write_text("extra", encoding="utf-8")
        assert verify_mod_manifest(mod).valid is True
        (scripts / "main.lua").write_text("return false", encoding="utf-8")
        assert verify_mod_manifest(mod).valid is True, (
            "MNFS 只保存路径哈希，不能假装检测内容修改"
        )
        (scripts / "main.lua").unlink()
        missing = verify_mod_manifest(mod)
        assert missing.valid is False and len(missing.missing_hashes) == 1
        print("  PASS: 额外文件和内容修改不误报，删除声明文件会判定缺失")

        try:
            parse_mod_manifest_bytes(b"BAD!")
            raise AssertionError("损坏的 Manifest 不应解析成功")
        except ManifestFormatError:
            pass
        cache = root / "cached_mod_manifests"
        cache.mkdir()
        (cache / "workshop-123.manifest.version").write_text(" V1.2 ", encoding="utf-8")
        assert read_cached_manifest_version(root, 123) == "V1.2"

        game_root = root / "game"
        server_root = root / "server"
        game_cache = game_root / "cached_mod_manifests"
        server_cache = server_root / "cached_mod_manifests"
        game_cache.mkdir(parents=True)
        server_cache.mkdir(parents=True)
        game_version = game_cache / "workshop-987654321.manifest.version"
        server_version = server_cache / "workshop-987654321.manifest.version"
        game_version.write_text("1.0", encoding="utf-8")
        server_version.write_text("1.1", encoding="utf-8")
        os.utime(game_version, ns=(1_000_000_000, 1_000_000_000))
        os.utime(server_version, ns=(2_000_000_000, 2_000_000_000))
        versions = find_cached_manifest_versions(
            [987654321, 987654322], extra_install_roots=[game_root, server_root]
        )
        assert versions == {987654321: "1.1"}
        print("  PASS: 损坏格式被拒绝，并从游戏/专服缓存中选择最新远程版本")


def test_workshop_download_precheck_uses_physical_files():
    """Steam Installed 缓存不能掩盖被删除或损坏的真实 Mod 目录。"""
    print("\n" + "=" * 60)
    print("Test 25f: Workshop Download Physical Precheck")

    import struct
    from dstools.features.mod.workshop_api import (
        SteamWorkshopSession,
        WorkshopBackend,
        WorkshopItemDetails,
        WorkshopItemState,
        validate_workshop_install,
        workshop_source_error,
    )

    assert "可能已下架" in workshop_source_error(WorkshopItemDetails(2428854303, 15))

    class FakeDll:
        def __init__(self):
            self.download_calls = []

        def SteamAPI_ISteamUGC_DownloadItem(self, ugc, workshop_id, high_priority):
            self.download_calls.append((workshop_id, high_priority))
            return True

    class FakeSession:
        backend = WorkshopBackend.CLIENT
        ugc = object()

        def __init__(self, path):
            self.path = path
            self.dll = FakeDll()

        def _ensure_started(self):
            pass

        def item_state(self, workshop_id):
            return WorkshopItemState(5)

        def item_install_info(self, workshop_id):
            return self.path

        def item_install_details(self, workshop_id):
            from dstools.features.mod.workshop_api import WorkshopInstallInfo

            return WorkshopInstallInfo(self.path, 0, 0)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid = root / "valid"
        valid.mkdir()
        (valid / "modinfo.lua").write_text('version = "1"', encoding="utf-8")
        assert validate_workshop_install(valid).valid

        (valid / "mod.manifest").write_bytes(
            struct.pack("<4sIII", b"MNFS", 1, 1, 0x12345678)
        )
        manifest_warning = validate_workshop_install(valid)
        assert manifest_warning.valid and manifest_warning.warning

        legacy = root / "123_legacy.bin"
        import zipfile

        with zipfile.ZipFile(legacy, "w") as package:
            package.writestr("modinfo.lua", 'version = "1"')
        assert validate_workshop_install(legacy, legacy_item=True).valid
        assert not validate_workshop_install(legacy).valid

        current_session = FakeSession(valid)
        current = SteamWorkshopSession.download_item(current_session, 123)
        assert current.completed and current.up_to_date
        assert not current_session.dll.download_calls

        missing_session = FakeSession(root / "deleted")
        repair = SteamWorkshopSession.download_item(missing_session, 123)
        assert repair.accepted and not repair.completed
        assert repair.details["repair"] is True
        assert missing_session.dll.download_calls

        force_path = root / "123"
        force_path.mkdir()
        force_modinfo = force_path / "modinfo.lua"
        force_modinfo.write_text('version = "modified"', encoding="utf-8")
        force_session = FakeSession(force_path)
        forced = SteamWorkshopSession.download_item(
            force_session, 123, expected_version="official"
        )
        assert forced.accepted and forced.details["version_repair"] is True
        backup = Path(forced.details["forced_modinfo_backup"])
        assert backup.is_file() and not force_modinfo.exists()
        SteamWorkshopSession._finish_forced_version_repair(forced, success=False)
        assert force_modinfo.read_text(encoding="utf-8") == 'version = "modified"'
        assert not backup.exists()

        redownload_root = root / "322330"
        redownload_path = redownload_root / "123"
        redownload_path.mkdir(parents=True)
        (redownload_path / "modinfo.lua").write_text('version = "old"', encoding="utf-8")
        redownload_session = FakeSession(redownload_path)
        redownload = SteamWorkshopSession.download_item(
            redownload_session, 123, force_redownload=True
        )
        assert redownload.accepted and redownload.details["forced_redownload"] is True
        assert not redownload_path.exists()
        assert redownload_session.dll.download_calls
        print("  PASS: 文件完整才跳过下载，Installed+目录缺失会强制进入修复")


def test_backup_manager_restore_clears_stale_slots():
    """restore_backup() 必须先清空会被覆盖的每一项再解压，不能只是在旧
    文件上覆盖解压——不这样做的话，备份之后又产生的新存档槽文件会跟备
    份里的旧槽位混在一起，游戏很可能还是照常挑编号最新的那个，恢复了个
    寂寞。这是 backup_manager.py 里最复杂、最容易静默出错的一段逻辑。"""
    print("\n" + "=" * 60)
    print("Test 26: Backup Restore Clears Stale Slots")

    with tempfile.TemporaryDirectory() as tmp:
        cluster = Path(tmp) / "Cluster_1"
        sess = cluster / "Master" / "save" / "session" / "ABCDEF0123456789"
        sess.mkdir(parents=True)
        (sess / "0000000001").write_text("old_slot_data")
        (cluster / "Master" / "server.ini").write_text("[NETWORK]\nserver_port=1\n")
        (cluster / "Master" / "modoverrides.lua").write_text("return {}")
        (cluster / "cluster.ini").write_text("[GAMEPLAY]\nmax_players=6\n")

        backup_zip = create_backup(cluster)
        print(f"  PASS: created backup {backup_zip.name}")

        # 模拟备份之后又产生了更新的存档槽位。
        (sess / "0000000002").write_text("newer_slot_after_backup")
        assert sorted(p.name for p in sess.iterdir()) == ["0000000001", "0000000002"]

        restore_backup(cluster, backup_zip)
        remaining = sorted(p.name for p in sess.iterdir())
        assert remaining == ["0000000001"], (
            f"应该只剩备份里的旧槽位，实际是 {remaining}"
        )
        print("  PASS: restore_backup() removes slots created after the backup")

        assert (
            cluster / "Master" / "server.ini"
        ).read_text() == "[NETWORK]\nserver_port=1\n"
        print("  PASS: restored config files match the backed-up content")


def test_backup_manager_prune_retention_boundary():
    """备份保留份数（app_settings.get_backup_retention()）超过时自动删
    掉最旧的。用手工构造、时间戳互不相同的旧备份文件模拟"已经攒了很多
    份"，比连续调用 create_backup() 更贴近真实使用场景（真实场景里两次
    备份之间至少隔几分钟，不会在同一秒内触发好几次自动去重后缀，直接
    连续调用反而会绕进那段自动去重逻辑本身，测的东西就跑偏了），再用一
    次真实的 create_backup() 验证会触发裁剪、且顺序正确。"""
    print("\n" + "=" * 60)
    print("Test 27: Backup Retention Boundary")

    with _isolated_settings_dir():
        # 保留份数的合法范围是 5~99（见 app_settings.set_backup_retention
        # 的 clamp）。
        set_backup_retention(5)
        with tempfile.TemporaryDirectory() as tmp:
            cluster = Path(tmp) / "Cluster_2"
            cluster.mkdir(parents=True)
            (cluster / "cluster.ini").write_text("[GAMEPLAY]\nmax_players=4\n")

            dest = backup_dir(cluster)  # 跟存档同级的统一备份目录，不是存档目录自己内部
            dest.mkdir(parents=True)
            for i in range(1, 8):  # 7 份时间戳递增的旧备份（都早于"现在"）
                (dest / f"Cluster_2_2026010{i}_000000.zip").write_bytes(b"")

            newest = create_backup(cluster)  # 第 8 份，真实时间戳，必然是最新的
            backups = list_backups(cluster)
            assert len(backups) == 5, f"应该只保留 5 份，实际 {len(backups)} 份"
            print("  PASS: only the most recent 5 backups are kept")

            assert backups[0] == newest, "最新的一份必须排在最前面"
            assert backups == sorted(backups, key=lambda p: p.name, reverse=True)
            print("  PASS: list_backups() orders newest-first")


def test_backfill_cluster_defaults_only_fills_missing():
    """backfill_cluster_defaults() 只能补缺的字段，不能覆盖已经存在的
    值——这是最容易被后续重构不小心破坏（"补默认值"误写成"覆盖已有
    值"）、且后果是用户已保存配置被悄悄吞掉的一类 bug。"""
    print("\n" + "=" * 60)
    print("Test 28: Cluster Defaults Backfill Only Fills Missing")

    config = ClusterConfig(
        gameplay={"vote_enabled": False}, network={}, misc={}, shard={}, steam={}
    )
    backfill_cluster_defaults(config)

    assert config.gameplay["vote_enabled"] is False, (
        "已经显式设置的值不应该被默认值覆盖"
    )
    print("  PASS: explicitly-set values are not overwritten")

    assert config.network["tick_rate"] == 15, "缺失的字段应该被补上官方默认值"
    assert config.misc["max_snapshots"] == 6
    print("  PASS: missing fields are backfilled with official defaults")

    # 用户拿真实存档手工核对过一轮之后新补的默认值（见 reference/带注释
    # 版本的cluster.ini）——顺带确认 STEAM 这个新分区也会被正确回填。
    assert config.gameplay["pvp"] is False
    assert config.gameplay["pause_when_empty"] is True
    assert config.network["cluster_name"] == "[Host]'s World"
    assert config.network["cluster_description"] == ""
    assert config.network["cluster_password"] == ""
    assert config.network["cluster_language"] == "en"
    assert config.misc["console_enabled"] is True
    assert config.steam["steam_group_only"] is False
    assert config.steam["steam_group_admins"] is False
    print(
        "  PASS: newly-verified defaults (incl. the new STEAM section) are backfilled too"
    )

    # bind_ip/master_ip/master_port/cluster_key 是游戏在 shard_enabled=
    # true 时自己生成写入的——应用户明确要求（"清空 cluster.ini 也要全
    # 部配置项齐全，点保存直接覆盖文件"），这里改成主动补上确认过的官
    # 方默认值，相当于顺手修复"手动删掉这几个字段导致开服报错"这个坑。
    assert config.shard["bind_ip"] == "127.0.0.1"
    assert config.shard["master_ip"] == "127.0.0.1"
    assert config.shard["master_port"] == 10888
    assert config.shard["cluster_key"] == "defaultPass"
    print(
        "  PASS: game-generated SHARD fields (bind_ip/master_ip/master_port/cluster_key) are now backfilled too"
    )

    # game_mode/max_players/cluster_cloud_id 没有一个"确认过"的官方默认
    # 值——但用户明确要求"删除任意设置都不能导致配置页面缺少这一项"，
    # 所以这三个字段现在也会出现（不会从 config 里彻底消失），只是补的
    # 是空字符串，不是编造一个看起来正常的假值。
    assert config.gameplay["game_mode"] == ""
    assert config.gameplay["max_players"] == ""
    assert config.network["cluster_cloud_id"] == ""
    print(
        "  PASS: fields with no confirmed official default (game_mode/max_players/cluster_cloud_id) "
        "still show up (blank), instead of disappearing or being faked"
    )

    # 空字符串等同于"没有"，也要被当成缺失补上默认值——用户明确要求"值
    # 为空也用默认值"，不是只处理 key 整个不存在的情况。
    config2 = ClusterConfig(
        gameplay={}, network={"cluster_name": ""}, misc={}, shard={}, steam={}
    )
    backfill_cluster_defaults(config2)
    assert config2.network["cluster_name"] == "[Host]'s World", (
        "空字符串也应该被当成缺失，补上默认值"
    )
    print("  PASS: an explicit empty string is treated the same as a missing key")


def test_cluster_ini_steam_section_roundtrip():
    """真机反馈过的数据丢失 bug：parse_cluster_ini()/write_cluster_ini()
    原来只认 GAMEPLAY/NETWORK/MISC/SHARD 四个分区，完全不知道 [STEAM]
    这个分区的存在——如果用户的 cluster.ini 里已经配置了 Steam 群组相关
    设置，只要在这个工具里点一次"保存"，整个 [STEAM] 分区会被静默吞掉，
    因为 write_cluster_ini() 会用只认识的四个分区重新生成整个文件。这里
    测的是"解析出来的 ClusterConfig 里要有 steam 字段" + "写回文件后
    [STEAM] 分区必须还在，值也要一致"。"""
    print("\n" + "=" * 60)
    print("Test 35: cluster.ini [STEAM] Section Round-Trip")

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cluster.ini"
        path.write_text(
            "[GAMEPLAY]\nmax_players=8\n\n"
            "[STEAM]\nsteam_group_only=true\nsteam_group_id=123456\nsteam_group_admins=false\n",
            encoding="utf-8",
        )
        config = parse_cluster_ini(path)
        assert config.steam.get("steam_group_only") is True
        assert config.steam.get("steam_group_id") == 123456
        assert config.steam.get("steam_group_admins") is False
        print("  PASS: parse_cluster_ini() reads the [STEAM] section")

        write_cluster_ini(config, path)
        reloaded = parse_cluster_ini(path)
        assert reloaded.steam.get("steam_group_only") is True
        assert reloaded.steam.get("steam_group_id") == 123456
        assert reloaded.gameplay.get("max_players") == 8, (
            "保存 [STEAM] 的同时不能弄丢其它分区"
        )
        print(
            "  PASS: write_cluster_ini() keeps the [STEAM] section instead of silently dropping it"
        )


def test_sakura_frp_tunnel_matching():
    """find_dstcamp_tunnel()/sanitize_tunnel_name() 是纯函数。樱花的真实
    隧道名规则是 3-20 个字符、只能用字母数字和下划线（实测报错确认过，
    连字符都不允许），所以命名约定不是直接拼"dstcamp-存档名-世界名"这种
    可读字符串（会超长/带非法字符），是短哈希——这里测的是"格式始终合
    法" + "同样的输入每次都算出同一个名字"（find_dstcamp_tunnel() 靠这个
    确定性现查匹配，不在本地存隧道 ID 缓存表），以及 source/platform 也
    必须参与哈希（真机复现过的 bug：本地存档"复制为服务器存档"后目录名
    相同，如果只按目录名+世界名算隧道名，两边会互相冒充对方的映射状
    态；同理 Steam/WeGame 两边如果有同名存档也会撞）。"""
    print("\n" + "=" * 60)
    print("Test 29: SakuraFrp Tunnel Name Matching")

    name = sanitize_tunnel_name("Cluster_1", "Master", "server", "steam")
    assert 3 <= len(name) <= 20, f"隧道名长度必须在 3-20 之间: {name}"
    assert all(c.isalnum() or c == "_" for c in name), (
        f"隧道名只能是字母数字和下划线: {name}"
    )
    print("  PASS: sanitize_tunnel_name() 输出符合樱花的命名规则")

    assert sanitize_tunnel_name("Cluster_1", "Master", "server", "steam") == name, (
        "同样的输入应该每次都算出同一个名字"
    )
    assert sanitize_tunnel_name("Cluster_1", "Caves", "server", "steam") != name, (
        "不同世界应该算出不同的名字"
    )
    print("  PASS: 同一世界确定性可复现，不同世界不会撞名")

    assert sanitize_tunnel_name("Cluster_1", "Master", "local", "steam") != name, (
        "同名存档不同来源（本地 vs 服务器）不应该撞名"
    )
    assert sanitize_tunnel_name("Cluster_1", "Master", "server", "wegame") != name, (
        "同名存档不同平台（Steam vs WeGame）不应该撞名"
    )
    print(
        "  PASS: source/platform 不同时不会撞名（本地/服务器存档同名、Steam/WeGame 同名两种场景）"
    )

    caves_name = sanitize_tunnel_name("Cluster_1", "Caves", "server", "steam")
    tunnels = [
        {"id": 1, "name": name, "remote": "12345"},
        {"id": 2, "name": caves_name, "remote": "12346"},
        {"id": 3, "name": "someone_elses_tunnel", "remote": "8080"},
    ]
    found = find_dstcamp_tunnel(tunnels, "Cluster_1", "Master", "server", "steam")
    assert found is not None and found["id"] == 1, "应该按名字匹配到对应世界的隧道"
    print("  PASS: find_dstcamp_tunnel() matches the right shard")

    assert (
        find_dstcamp_tunnel(tunnels, "Cluster_1", "Cave2", "server", "steam") is None
    ), "不存在的世界不应该匹配到任何隧道"
    assert (
        find_dstcamp_tunnel(tunnels, "Cluster_1", "Master", "local", "steam") is None
    ), "同名本地存档不应该匹配到服务器存档的隧道"
    print(
        "  PASS: no false match for a shard with no tunnel, nor for a same-named save of a different source"
    )


def test_sakura_server_port_rewrite():
    """ "开启樱花映射"最关键的一步：把樱花分配的远程端口回写进这个世界自
    己的 server.ini。这里只测这一步的读-改-写本身，不牵扯真实网络调用。"""
    print("\n" + "=" * 60)
    print("Test 30: Sakura Server Port Rewrite")

    with tempfile.TemporaryDirectory() as tmp:
        shard_dir = Path(tmp) / "Master"
        shard_dir.mkdir(parents=True)
        (shard_dir / "server.ini").write_text("[NETWORK]\nserver_port=10999\n")

        config = load_shard_config(shard_dir)
        assert get_shard_option(config, "NETWORK", "server_port") == 10999
        print("  PASS: original server_port read back correctly")

        set_shard_option(config, "NETWORK", "server_port", 23456)
        save_shard_config(config, shard_dir)

        reloaded = load_shard_config(shard_dir)
        assert get_shard_option(reloaded, "NETWORK", "server_port") == 23456, (
            "回写的端口应该能重新读回来"
        )
        print("  PASS: rewritten server_port persists after save+reload")


def test_sakura_token_settings_roundtrip():
    """get_sakura_token()/set_sakura_token() 的读写往返，隔离在临时设置
    目录里跑，绝不碰真实 %APPDATA%/DSTCamp/settings.json。"""
    print("\n" + "=" * 60)
    print("Test 31: Sakura Token Settings Roundtrip")

    with _isolated_settings_dir():
        assert get_sakura_token() is None, "没设置过应该是 None"
        set_sakura_token("fake-token-for-test-only")
        assert get_sakura_token() == "fake-token-for-test-only"
        print("  PASS: token round-trips through settings.json")

        set_sakura_token(None)
        assert get_sakura_token() is None, "清空之后应该重新变回 None，而不是空字符串"
        print(
            "  PASS: clearing the token removes the key instead of storing an empty string"
        )


def test_frpc_manager_key_convention():
    """FrpcManager 的 (cluster_path, shard_name) key 约定跟
    dedicated_server.ServerManager 一致——纯函数，不真的起子进程。"""
    print("\n" + "=" * 60)
    print("Test 32: FrpcManager Key Convention")

    mgr = FrpcManager()
    key_a = mgr._key(Path("C:/saves/Cluster_1"), "Master")
    key_b = mgr._key(Path("C:/saves/Cluster_1"), "Master")
    key_c = mgr._key(Path("C:/saves/Cluster_1"), "Caves")
    assert key_a == key_b, "同一个 (cluster_path, shard_name) 应该算出相同的 key"
    assert key_a != key_c, "不同世界应该算出不同的 key"
    assert mgr.get(Path("C:/saves/Cluster_1"), "Master") is None, (
        "没启动过的世界应该查不到进程"
    )
    print("  PASS: FrpcManager._key() matches ServerManager's convention")


@contextlib.contextmanager
def _fake_workshop_dir(
    root: Path,
    subscribed_ids: list[str],
    with_injector_files: bool = False,
    mod_version: str | None = None,
):
    """猴子补丁 luajit_injector.find_workshop_dir()（这里也是 "from ...
    import" 抄过去的独立引用，同 _isolated_settings_dir() 的道理，只补
    find_workshop_dir 原本定义所在的 parser 模块自己那份不生效），指向
    root/steamapps/workshop/content/322330/，按 subscribed_ids 建好
    <id>/modinfo.lua。root 由调用方提供（不是这个函数自己另开一个临时目
    录），这样能跟专用服务器安装目录建在同一个 fake Steam 库根目录下，
    模拟真实"专用服务器和创意工坊内容同属一个 Steam 库"的目录关系（
    needs_regeneration() 的组合测试需要这个前提）。

    with_injector_files=True 时在 WORKSHOP_ID 对应的文件夹下现造一份假
    的 bin64/windows/ 注入文件，够 _injector_source_dir()/apply_install()
    的测试用（不再涉及 zip/下载——作者确认过注入文件直接取自订阅内容，
    见 luajit_injector.py 顶部说明）。mod_version 给 WORKSHOP_ID 这个物
    品的 modinfo.lua 写一行 `version = "<mod_version>"`（真机验证过真实
    格式是这样，比如 "1.10.1"），够 current_injector_version()/
    needs_regeneration() 的测试用——不再需要伪造 appworkshop_322330.acf，
    因为 current_injector_version() 现在直接读 modinfo.lua 自己的
    version 字段。"""
    import dstools.features.local_service.luajit_injector as lj

    workshop_dir = root / "steamapps" / "workshop" / "content" / "322330"
    for wid in subscribed_ids:
        d = workshop_dir / wid
        d.mkdir(
            parents=True, exist_ok=True
        )  # 允许同一个 root 反复调用，模拟 Steam 原地更新订阅内容
        lines = ["name = 'test'"]
        if wid == WORKSHOP_ID and mod_version is not None:
            lines.append(f'version = "{mod_version}"')
        (d / "modinfo.lua").write_text("\n".join(lines), encoding="utf-8")
        if with_injector_files and wid == WORKSHOP_ID:
            bin64_win = d / "bin64" / "windows"
            bin64_win.mkdir(parents=True, exist_ok=True)
            (bin64_win / "Winmm.dll").write_bytes(b"fake winmm")
            (bin64_win / "Injector.dll").write_bytes(b"fake injector")
            deps = bin64_win / "deps"
            deps.mkdir(parents=True, exist_ok=True)
            (deps / "lua_helper.dll").write_bytes(b"fake nested dependency")

    original = lj.find_workshop_dir
    lj.find_workshop_dir = lambda: workshop_dir
    try:
        yield workshop_dir
    finally:
        lj.find_workshop_dir = original


def _make_fake_install_dir(root: Path, build_id: str | None = None) -> Path:
    """现造一份 <root>/steamapps/common/<产品名>/ 目录结构（安装目录），
    可选带上 version.txt（游戏自己写的内部版本号），模拟"这是某个 Steam
    库里的专用服务器安装目录"这个前提，不需要真的装 Steam。"""
    install_dir = (
        root / "steamapps" / "common" / "Don't Starve Together Dedicated Server"
    )
    install_dir.mkdir(parents=True)
    if build_id is not None:
        (install_dir / "version.txt").write_text(f"{build_id}\n", encoding="utf-8")
    return install_dir


def test_luajit_injector():
    """luajit_injector.py 只测离线可测的纯逻辑（游戏版本读取/隔离副
    本三态检测/resolve_launch_bin64_dir/标记文件往返/需要重新生成的判
    断/重新生成/创意工坊订阅检测/plan_install 的只读判断/卸载的幂等
    性），真实网络调用和真实注入效果按项目惯例不测，属于人工验证项。"""
    print("\n" + "=" * 60)
    print("Test 33: LuaJIT Injector")

    with tempfile.TemporaryDirectory() as tmp:
        install_dir = _make_fake_install_dir(Path(tmp), build_id="111")
        assert read_game_version_file(install_dir) == "111"
        assert read_game_version_file(install_dir.parent) is None, (
            "没有 version.txt 应该返回 None"
        )
    print("  PASS: steam_discovery.read_game_version_file() 正确读取 version.txt")

    with _isolated_settings_dir():
        with tempfile.TemporaryDirectory() as tmp:
            install_dir = _make_fake_install_dir(Path(tmp))
            bin64 = install_dir / "bin64"
            bin64.mkdir()
            luajit_dir = get_luajit_dir(install_dir)

            assert detect_state(bin64) is InjectorState.NOT_INSTALLED
            luajit_dir.mkdir(parents=True)
            (luajit_dir / "Injector.dll").write_bytes(b"x")
            assert detect_state(bin64) is InjectorState.DISABLED_LEFTOVER, (
                "副本存在但还没启用，应该是已关闭残留"
            )
            set_luajit_enabled(True)
            assert detect_state(bin64) is InjectorState.ACTIVE
            set_luajit_enabled(False)
            assert detect_state(bin64) is InjectorState.DISABLED_LEFTOVER
        print("  PASS: detect_state() 新语义（副本是否存在 + 是否启用）判定正确")

        with tempfile.TemporaryDirectory() as tmp:
            install_dir = _make_fake_install_dir(Path(tmp))
            assert resolve_launch_bin64_dir(install_dir) is None, "未启用应该返回 None"
            set_luajit_enabled(True)
            assert resolve_launch_bin64_dir(install_dir) is None, (
                "已启用但副本还没装过（缺锚点文件）应该返回 None"
            )
            luajit_dir = get_luajit_dir(install_dir)
            luajit_dir.mkdir(parents=True)
            (luajit_dir / "Injector.dll").write_bytes(b"x")
            assert resolve_launch_bin64_dir(install_dir) == luajit_dir, (
                "已启用且副本有效应该返回副本目录，给 ServerProcess 用来覆盖启动目录"
            )
        print("  PASS: resolve_launch_bin64_dir() 按启用状态 + 副本有效性判定正确")

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            assert read_marker(d) is None, "没有标记文件应该返回 None，不抛异常"
            write_marker(d, LuajitMarker(DST_version="123", luajit_version="1.0.0"))
            m = read_marker(d)
            assert m.DST_version == "123" and m.luajit_version == "1.0.0"
            # 落盘的 version.json 里 DST_version 应该是不带引号的数字（用户
            # 指定的格式），luajit_version 是语义化版本号字符串。
            raw = json.loads((d / "version.json").read_text(encoding="utf-8"))
            assert raw == {"DST_version": 123, "luajit_version": "1.0.0"}, (
                f"version.json 落盘格式不对: {raw}"
            )
        print(
            "  PASS: read_marker()/write_marker() 往返正确，version.json 字段名/格式符合预期"
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_dir = _make_fake_install_dir(root, build_id="111")
            set_luajit_enabled(False)  # 上一个子测试可能留下 True，这里显式复位
            with _fake_workshop_dir(root, [WORKSHOP_ID], mod_version="1.10.1"):
                assert needs_regeneration(install_dir) is False, "未启用应该是 False"
                set_luajit_enabled(True)
                assert needs_regeneration(install_dir) is False, (
                    "没有标记（还没成功装过）应该是 False"
                )

                luajit_dir = get_luajit_dir(install_dir)
                luajit_dir.mkdir(parents=True)
                write_marker(
                    luajit_dir, LuajitMarker(DST_version="111", luajit_version="1.10.1")
                )
                assert needs_regeneration(install_dir) is False, (
                    "游戏版本、配套 Mod 版本都一致，不需要重新生成"
                )

                write_marker(
                    luajit_dir, LuajitMarker(DST_version="000", luajit_version="1.10.1")
                )
                assert needs_regeneration(install_dir) is True, (
                    "游戏版本不一致（被更新过），需要重新生成"
                )

                write_marker(
                    luajit_dir, LuajitMarker(DST_version="111", luajit_version="1.10.0")
                )
                assert needs_regeneration(install_dir) is True, (
                    "配套 Mod 版本不一致（作者发布了新版本），也需要重新生成"
                )
        print(
            "  PASS: needs_regeneration() 按 DST_version/luajit_version 是否过期判定正确"
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_dir = _make_fake_install_dir(root, build_id="222")
            bin64 = install_dir / "bin64"
            bin64.mkdir()
            (bin64 / "game.exe").write_bytes(
                b"fake game exe"
            )  # 模拟真实 bin64 里的游戏文件

            with _fake_workshop_dir(
                root, [WORKSHOP_ID], with_injector_files=True, mod_version="1.10.1"
            ):
                luajit_dir = get_luajit_dir(install_dir)
                luajit_dir.mkdir(parents=True)
                write_marker(
                    luajit_dir, LuajitMarker(DST_version="111", luajit_version="1.10.0")
                )

                result = regenerate(bin64)
                assert result.ok is True, f"应该成功: {result.errors}"
                assert (luajit_dir / "game.exe").read_bytes() == b"fake game exe", (
                    "重新生成应该带上真实 bin64 里当前的游戏文件"
                )
                assert (luajit_dir / "Winmm.dll").read_bytes() == b"fake winmm", (
                    "注入文件应该直接取自订阅内容，不是重新联网下载"
                )
                assert (
                    luajit_dir / "deps" / "lua_helper.dll"
                ).read_bytes() == b"fake nested dependency", (
                    "注入包里的 deps 子目录和 DLL 也必须递归复制"
                )
                new_marker = read_marker(luajit_dir)
                assert new_marker.DST_version == "222", (
                    "标记里的 DST_version 应该更新成当前真实值"
                )
                assert new_marker.luajit_version == "1.10.1", (
                    "luajit_version 也应该更新成当前配套 Mod 的版本"
                )
                print(
                    "  PASS: regenerate() 用当前配套 Mod 内容重新生成副本，标记同步更新"
                )

                # 只有配套 Mod 版本变了、游戏本体没变时，选择性更新不应该
                # 碰 bin64 部分——放一个不在真实 bin64 里的哨兵文件，只有
                # "整个重新 copytree"才会让它消失，用它反向验证没有做没
                # 必要的整份重建。
                (luajit_dir / "existing_bin64_marker.txt").write_text(
                    "untouched", encoding="utf-8"
                )
                with _fake_workshop_dir(
                    root, [WORKSHOP_ID], with_injector_files=True, mod_version="1.10.2"
                ):
                    result2 = regenerate(bin64)
                    assert result2.ok is True, f"应该成功: {result2.errors}"
                    assert (luajit_dir / "existing_bin64_marker.txt").exists(), (
                        "只有 luajit_version 变了，DST_version 没变，不应该整个重新复制 bin64"
                    )
                    marker2 = read_marker(luajit_dir)
                    assert marker2.DST_version == "222", "DST_version 应该保持不变"
                    assert marker2.luajit_version == "1.10.2", (
                        "luajit_version 应该更新成新的配套 Mod 版本"
                    )
                    print(
                        "  PASS: regenerate() 只有配套 Mod 版本变了时选择性更新，不重新复制 bin64"
                    )

            # 没有订阅内容时应该优雅失败，不联网、不崩溃——必须用全新的
            # workshop 根目录，不能复用上面那个 root：_fake_workshop_dir()
            # 只按 subscribed_ids 新建文件夹，不会清空之前调用已经在磁盘
            # 上留下的 3444078585/bin64/windows/ 内容，传空列表并不会让
            # 已经写盘的注入文件消失。
            with tempfile.TemporaryDirectory() as tmp_empty:
                with _fake_workshop_dir(Path(tmp_empty), []):
                    shutil.rmtree(luajit_dir)
                    result_no_source = regenerate(bin64)
                    assert result_no_source.ok is False, "找不到订阅内容应该失败"
                    print("  PASS: regenerate() 找不到订阅内容时优雅失败")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _fake_workshop_dir(root, []):
                assert is_workshop_subscribed() is False
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _fake_workshop_dir(root, [WORKSHOP_ID]):
                assert is_workshop_subscribed() is True
        print("  PASS: is_workshop_subscribed() 按创意工坊本地内容目录判定正确")

        with tempfile.TemporaryDirectory() as tmp_mo:
            mo = load_mod_overrides(Path(tmp_mo) / "modoverrides.lua")
            enable_mod(mo, "dstcamp_luajit_mod")  # 早前版本遗留的旧 key
            enable_mod(mo, "workshop-123456")  # 无关的其它 mod，不该被动到
            assert cleanup_legacy_local_mod_entry(mo) is True
            assert "dstcamp_luajit_mod" not in mo.mods
            assert "workshop-123456" in mo.mods
            assert cleanup_legacy_local_mod_entry(mo) is False, (
                "已经清过一次，重复调用应该是无操作"
            )
        print("  PASS: cleanup_legacy_local_mod_entry() 只清掉旧 key，不动其它 mod")

        plan_missing = plan_install(None, server_running=False)
        assert plan_missing.blocked_reason == "bin64_not_found"
        print("  PASS: plan_install(bin64_dir=None) 判定 bin64_not_found")

        with tempfile.TemporaryDirectory() as tmp2:
            real_bin64 = Path(tmp2) / "bin64"
            real_bin64.mkdir()
            plan_running = plan_install(real_bin64, server_running=True)
            assert plan_running.blocked_reason == "server_running"
            print("  PASS: plan_install() 服务器运行中时判定 server_running")

            with _fake_workshop_dir(Path(tmp2), []):
                plan_not_subscribed = plan_install(real_bin64, server_running=False)
                assert plan_not_subscribed.blocked_reason == "workshop_not_subscribed"
            print(
                "  PASS: plan_install() 未订阅创意工坊配套 Mod 时判定 workshop_not_subscribed"
            )

            with _fake_workshop_dir(Path(tmp2), [WORKSHOP_ID]):
                plan_ok = plan_install(real_bin64, server_running=False)
                assert plan_ok.blocked_reason is None
                assert plan_ok.current_state is InjectorState.NOT_INSTALLED
            print("  PASS: plan_install() 已订阅、未运行时判定正常可安装")

            set_luajit_enabled(True)
            assert apply_uninstall(real_bin64) is True
            assert get_luajit_enabled() is False, "关闭应该只是把开关关掉"
            assert apply_uninstall(real_bin64) is False, (
                "已经关闭时重复调用应该幂等，不报错"
            )
            print(
                "  PASS: apply_uninstall() 只关闭 app_settings 开关（不删除任何文件），且重复调用是幂等的"
            )

    with tempfile.TemporaryDirectory() as tmp3:
        install_dir = Path(tmp3)
        assert find_bin64_dir(install_dir) is None, "空目录应该返回 None"
        (install_dir / "bin64").mkdir()
        (
            install_dir / "bin64" / "dontstarve_dedicated_server_nullrenderer_x64.exe"
        ).write_bytes(b"x")
        assert find_bin64_dir(install_dir) == install_dir / "bin64"
        print("  PASS: dedicated_server.find_bin64_dir() 找到/找不到都符合预期")


def test_steam_library_folder_casing():
    """真机复现过的真实 bug：注册表 SteamPath 大小写可能跟磁盘上真实目录
    名（也是 libraryfolders.vdf 里 Steam 自己记录的大小写）不一致，而这
    个大小写差异不是纯装饰性的——专用服务器进程内部按路径字符串做创意工
    坊内容查找，大小写不对会导致完全识别不到 mod（尽管 Windows 文件系统
    本身访问这个目录不区分大小写）。parse_library_folders() 必须优先信
    vdf 里的大小写，不能让 Path.__eq__ 在 Windows 上的大小写不敏感比较
    把"两份大小写不同但其实是同一个目录"误判成合法的两个库，进而把 vdf
    里正确大小写的版本当成重复项丢弃。"""
    print("\n" + "=" * 60)
    print("Test 34: Steam Library Folder Casing")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        real_dir = root / "Steam"  # 磁盘上真实的目录名，大小写正确
        (real_dir / "steamapps").mkdir(parents=True)
        vdf_text = (
            '"libraryfolders"\n{\n'
            f'\t"0"\n\t{{\n\t\t"path"\t\t"{str(real_dir).replace(chr(92), chr(92) * 2)}"\n\t}}\n'
            "}\n"
        )
        (real_dir / "steamapps" / "libraryfolders.vdf").write_text(
            vdf_text, encoding="utf-8"
        )

        # 模拟注册表返回的大小写跟磁盘真实大小写不一致（Windows 文件系统
        # 不区分大小写，这个路径本身照样能正常访问/exists() 判断为真）。
        steam_root_wrong_case = Path(str(real_dir).lower())
        assert steam_root_wrong_case.exists(), "Windows 上大小写不影响路径是否存在"

        libraries = parse_library_folders(steam_root_wrong_case)
        assert str(libraries[0]) == str(real_dir), (
            f"应该优先用 libraryfolders.vdf 里 Steam 自己记录的正确大小写，结果是 {libraries[0]}"
        )
        print(
            "  PASS: parse_library_folders() 优先采用 vdf 里的正确大小写，不被注册表的错误大小写覆盖"
        )


def test_font_style_switch():
    """测试字体样式一键切换功能的核心逻辑（纯逻辑，不需要真实 Tk 窗口）。
    验证：(a) shared/gui/fonts.py 的 set_font_style() 正确切
    换 PIL 渲染用的字体文件路径，且切换后清空缓存；(b) theme.py 的
    set_font_style_choice() 正确联动 FONT_FAMILY，并且同步更新了
    fonts.py 那一侧（Tk 和 PIL 两条渲染路径不能各用各的字体）；(c) 字
    体样式是跟颜色主题解耦的独立设置——切换颜色主题（set_theme()）不
    会改动已经选好的样式；(d) font_tuple() 的显式 bold=True 覆盖依然
    生效（不依赖任何全局字重状态）；(e) app_settings.py 的持久化读写
    能正确往返；(f) 打包进 tools/fonts/ 的可爱风字体文件确实存在（防
    止以后重构不小心把这个资源文件弄丢，PIL 侧会静默 fallback 到雅
    黑，不会报错提醒，容易被忽略）。"""
    print("\n" + "=" * 60)
    print("Test 35: Font Style Switch")

    from pathlib import Path

    from dstools.shared.gui import fonts, theme
    from dstools.shared import app_settings

    cute_font_path = (
        Path(__file__).resolve().parent.parent
        / "tools"
        / "fonts"
        / "KNMaiyuan-Regular.ttf"
    )
    assert cute_font_path.exists(), f"可爱风字体文件缺失: {cute_font_path}"
    print("  PASS: tools/fonts/KNMaiyuan-Regular.ttf 确实打包在仓库里")

    original_choice = theme.FONT_STYLE_CHOICE
    try:
        fonts.set_font_style("cute")
        assert fonts.get_font_style() == "cute"
        assert fonts.get_font(20) is not None, "切换样式后 PIL 侧仍应能正常取到字体对象"
        print("  PASS: fonts.set_font_style() 切换 PIL 渲染用的字体文件并清空缓存")

        theme.set_font_style_choice("cute")
        assert theme.FONT_STYLE_CHOICE == "cute"
        assert theme.FONT_FAMILY == "KN Maiyuan"
        assert fonts.get_font_style() == "cute", (
            "theme.set_font_style_choice() 必须同步联动 fonts.py 那一侧"
        )
        print(
            "  PASS: theme.set_font_style_choice() 联动 Tk 侧(FONT_FAMILY)与 PIL 侧(fonts.py)"
        )

        theme.set_font_style_choice("default")
        assert theme.FONT_FAMILY == "Microsoft YaHei UI Light"
        assert theme.font_tuple(12) == ("Microsoft YaHei UI Light", 12), (
            "没有显式 bold 参数时不应该额外带 bold 样式串"
        )
        assert theme.font_tuple(12, bold=True) == (
            "Microsoft YaHei UI Light",
            12,
            "bold",
        ), "显式 bold=True 是控件自身的强调，必须始终生效"
        print(
            "  PASS: font_tuple() 正确反映当前字体样式，且 bold=True 显式覆盖始终生效"
        )

        theme.set_font_style_choice("cute")
        theme.set_theme("mint")
        assert theme.FONT_STYLE_CHOICE == "cute", (
            "字体样式是独立于颜色主题的设置，切主题不应该改动它"
        )
        theme.set_theme("gray")
        assert theme.FONT_STYLE_CHOICE == "cute"
        print("  PASS: 切换颜色主题(set_theme())不会连带改动已选好的字体样式")

        # 荆南麦圆体笔画粗壮，跟微软雅黑用一样的字号看着更拥挤，需要整
        # 体放大——验证切到 cute 后字号阶梯按 FONT_SIZE_SCALE_BY_STYLE
        # 放大了，且切换颜色主题不会打乱这个放大倍数。
        theme.set_font_style_choice("default")
        default_base = theme.FONT_SIZE_BASE
        theme.set_font_style_choice("cute")
        cute_scale = theme.FONT_SIZE_SCALE_BY_STYLE["cute"]
        assert theme.FONT_SIZE_BASE == round(default_base * cute_scale), (
            "字体样式切到 cute 后，全局字号阶梯必须按 FONT_SIZE_SCALE_BY_STYLE 整体放大"
        )
        assert theme.FONT_SIZE_BASE > default_base, (
            "cute 的缩放系数应该让字号变大，不是不变或变小"
        )
        theme.set_theme("mint")
        assert theme.FONT_SIZE_BASE == round(default_base * cute_scale), (
            "切换颜色主题不应该打乱已经生效的字体样式缩放倍数"
        )
        theme.set_theme("gray")
        print("  PASS: 字体样式切换会按比例放大全局字号阶梯，且不受颜色主题切换影响")

        app_settings.set_font_style_choice("cute")
        assert app_settings.get_font_style_choice() == "cute"
        app_settings.set_font_style_choice("default")
        assert app_settings.get_font_style_choice() == "default"
        print("  PASS: app_settings.py 的字体样式持久化读写往返正确")
    finally:
        theme.set_font_style_choice(original_choice)
        app_settings.set_font_style_choice(original_choice)


def test_frp_selfhost_port_conflict_detection():
    """真机反馈过的真实 bug：自建 frps 已经部署过一次（服务在跑，绑定
    在端口 A），用户把绑定端口改成端口 B、B 又恰好被服务器上别的服务
    （比如 sshd）占用时，之前"只要 dstcamp-frps 服务在跑就跳过端口冲
    突检查"的判断会把这个真冲突放过去，装完/重启失败才暴露问题。

    测试 probe.py 的 _parse_probe_output() 正确解析新增的 FRPSPORT 字
    段（dstcamp-frps 当前实际绑定的端口，不是笼统的"服务在不在跑"），
    并验证 tab.py._start_deploy() 里改用的判断条件
    `port in used_ports and port != frps_bind_port`：目标端口是 frps
    自己当前绑定的那个端口时不算冲突（复用现有安装的正常场景），改成
    别的、被第三方服务占用的端口时才应该判定为冲突。"""
    print("\n" + "=" * 60)
    print("Test 36: Frp Selfhost Port Conflict Detection")

    from dstools.features.frp_selfhost.probe import _parse_probe_output

    output = (
        "UID:1000\nSUDO:ok\nSERVICE:active\nCPU:2\nMEM:1024,512\n"
        "PORTS:22,2323,7000,6010\nFRPSPORT:7000\n"
    )
    status = _parse_probe_output(output)
    assert status.used_ports == frozenset({22, 2323, 7000, 6010})
    assert status.frps_bind_port == 7000
    assert status.service_active is True
    print(
        "  PASS: _parse_probe_output() 正确解析 FRPSPORT 字段(frps 当前实际绑定的端口)"
    )

    def is_conflict(target_port: int) -> bool:
        return (
            status.reachable
            and target_port in status.used_ports
            and target_port != status.frps_bind_port
        )

    assert is_conflict(2323) is True, (
        "2323 被 sshd 占用、不是 frps 自己绑定的端口，必须判定为冲突"
    )
    assert is_conflict(7000) is False, (
        "7000 就是 frps 自己当前绑定的端口，复用现有安装场景不能误判为冲突"
    )
    assert is_conflict(9999) is False, "9999 完全没被占用，不该判定为冲突"
    print(
        "  PASS: 冲突判断改用 frps_bind_port 比对后，服务在跑但改用新端口的真冲突不再被放过"
    )

    # 服务从没装过时 FRPSPORT 字段为空，frps_bind_port 应该是 None，
    # 不能被误判等于任何整数端口。
    output_never_deployed = "UID:1000\nSUDO:ok\nSERVICE:inactive\nCPU:2\nMEM:1024,512\nPORTS:22\nFRPSPORT:\n"
    status2 = _parse_probe_output(output_never_deployed)
    assert status2.frps_bind_port is None
    assert (
        status2.reachable and 22 in status2.used_ports and 22 != status2.frps_bind_port
    ) is True, "从没部署过 frps 时，端口被其它服务占用也应该判定为冲突"
    print("  PASS: 从没部署过 frps 时 frps_bind_port 为 None，不会跟任何端口误判相等")


def test_ktech_runtime_detector():
    """缺少任一 VC++ 2013 x86 DLL 时必须在启动 ktech.exe 前识别出来。"""
    print("\n" + "=" * 60)
    print("Test 37: Ktech VC++ 2013 Runtime Detector")
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp)
        for dll_name in ("MSVCR120.dll", "MSVCP120.dll", "VCOMP120.dll"):
            (runtime_dir / dll_name).touch()
        assert _has_vc2013_x86_runtime(runtime_dir) is True

        # 真实反馈缺的是 VCOMP120.dll；此前会先让 Windows 弹加载器错误框。
        (runtime_dir / "VCOMP120.dll").unlink()
        assert _has_vc2013_x86_runtime(runtime_dir) is False
    print("  PASS: VCOMP120.dll 缺失会被无弹窗地识别为缺 VC++ 2013 x86 运行库")


def test_ktech_ascii_runtime_conversion():
    """中文安装/输入路径下，ktech 仍须从纯 ASCII 缓存副本中运行。"""
    print("\n" + "=" * 60)
    print("Test 37b: Ktech ASCII Runtime Conversion")
    from types import SimpleNamespace
    from unittest.mock import patch

    from dstools.shared import tex_convert

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_dir = root / "中文安装目录" / "tools" / "ktools"
        source_dir.mkdir(parents=True)
        (source_dir / "ktech.exe").write_bytes(b"fake-ktech")
        (source_dir / "CORE_RL_test.dll").write_bytes(b"fake-dll")
        cache_root = root / "ascii-cache"
        tex_path = root / "中文输入" / "icon.tex"
        tex_path.parent.mkdir()
        tex_path.write_bytes(b"fake-tex")
        out_path = root / "中文输出" / "icon.png"

        calls = []

        def fake_run(args, *, cwd, **_kwargs):
            calls.append((args, cwd))
            assert len(args) == 2
            assert args[1] == "input.tex"
            assert Path(args[0]).parent.is_relative_to(
                cache_root / "runtime" / "ktools"
            )
            assert Path(cwd).is_relative_to(cache_root / "runtime" / "ktech_jobs")
            assert (Path(cwd) / "input.tex").read_bytes() == b"fake-tex"
            (Path(cwd) / "input.png").write_bytes(b"fake-png")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        tex_convert._ktools_runtime_attempted = False
        tex_convert._ktools_runtime_dir = None
        try:
            with (
                patch.object(tex_convert, "_TOOLS_DIR", source_dir),
                patch.object(tex_convert, "_KTECH_EXE", source_dir / "ktech.exe"),
                patch.object(tex_convert, "cache_root_dir", return_value=cache_root),
                patch.object(tex_convert.subprocess, "run", side_effect=fake_run),
            ):
                assert tex_convert.tex_to_png(tex_path, out_path) is True
        finally:
            tex_convert._ktools_runtime_attempted = False
            tex_convert._ktools_runtime_dir = None

        assert len(calls) == 1
        assert out_path.read_bytes() == b"fake-png"
        runtime_dirs = list((cache_root / "runtime" / "ktools").iterdir())
        assert len(runtime_dirs) == 1
        assert len(runtime_dirs[0].name) == 64
        assert (runtime_dirs[0] / "CORE_RL_test.dll").is_file()
        assert (runtime_dirs[0] / ".bundle.sha256").is_file()
    print("  PASS: 整套 ktools 按内容哈希复制到英文缓存，转换只使用固定英文文件名")


def main():
    """运行全部测试。"""
    print("\n" + "█" * 60)
    print("  DSTOOLS - End-to-End Verification Tests")
    print("█" * 60)

    all_passed = True
    tests = [
        test_lua_parser_basic,
        test_lua_parser_nested,
        test_lua_parser_roundtrip,
        test_lua_parser_real_data,
        test_ini_parser,
        test_discovery,
        test_save_reader,
        test_mod_manager,
        test_config_manager,
        test_list_session_players,
        test_character_names,
        test_character_icons,
        test_modinfo_reader,
        test_workshop_content_directory_filter,
        test_admin_manager,
        test_token_manager,
        test_cluster_copy,
        test_player_notes,
        test_app_settings_toggles,
        test_cache_path_user_guidance,
        test_mod_sync_junction,
        test_theme_set_theme,
        test_world_reader_and_view_model,
        test_world_catalog_audit_and_cave_hidden_forest_sections,
        test_world_catalog_layers_are_isolated,
        test_porkland_location_selector,
        test_world_creation_plan_and_atomic_writer,
        test_world_categories_bilingual,
        test_custom_background,
        test_mod_resolve_cache,
        test_mod_version_resolution,
        test_workshop_source_details_parser,
        test_workshop_status_evidence_priority,
        test_workshop_snapshot_uses_one_steam_session,
        test_dst_mod_manifest_verification,
        test_workshop_download_precheck_uses_physical_files,
        test_backup_manager_restore_clears_stale_slots,
        test_backup_manager_prune_retention_boundary,
        test_backfill_cluster_defaults_only_fills_missing,
        test_cluster_ini_steam_section_roundtrip,
        test_sakura_frp_tunnel_matching,
        test_sakura_server_port_rewrite,
        test_sakura_token_settings_roundtrip,
        test_frpc_manager_key_convention,
        test_luajit_injector,
        test_steam_library_folder_casing,
        test_font_style_switch,
        test_frp_selfhost_port_conflict_detection,
        test_ktech_runtime_detector,
        test_ktech_ascii_runtime_conversion,
        test_world_ocean_frequency_labels,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"\n  FAIL: {e}")
            import traceback

            traceback.print_exc()
            all_passed = False

    print("\n" + "█" * 60)
    if all_passed:
        print("  ALL TESTS PASSED!")
    else:
        print("  SOME TESTS FAILED!")
    print("█" * 60)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
