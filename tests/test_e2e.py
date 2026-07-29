"""End-to-end verification tests for dstools."""

import contextlib
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dstools.core.lua_parser import (
    LuaTableParser,
    parse_lua_table,
    serialize_lua_table,
    parse_lua_file,
)
from dstools.core.ini_parser import (
    parse_cluster_ini,
    parse_server_ini,
    write_cluster_ini,
)
from dstools.models import ClusterConfig
from dstools.core.mod_manager import (
    ModOverrides,
    load_mod_overrides,
    save_mod_overrides,
    enable_mod,
    disable_mod,
    set_mod_config,
    get_mod_config,
    list_mods,
    remove_mod,
    diff_mods,
    sync_mods,
)
from dstools.core.discovery import find_klei_root, discover_environment
from dstools.core.save_reader import list_save_sessions, get_save_summary, list_session_players
from dstools.core.config_manager import set_cluster_option, backfill_cluster_defaults
from dstools.core.character_names import get_character_display_name
from dstools.core.character_icons import find_mod_character_name, resolve_character
from dstools.core.app_settings import (
    load_settings, save_settings, get_player_note, set_player_note,
    get_minimize_on_close, set_minimize_on_close,
    get_cache_use_exe_dir, set_cache_use_exe_dir,
    set_backup_retention,
)
from dstools.core.backup_manager import create_backup, restore_backup, list_backups
from dstools.models import SaveSession, SaveSource
from dstools.core.modinfo_reader import parse_modinfo
from dstools.core.admin_manager import read_adminlist, add_admin, remove_admin, has_admin
from dstools.core.token_manager import read_token, write_token, mask_token, is_valid_token
from dstools.core.backup_utils import backup_file, _prune_old_backups
from dstools.core.cluster_copy import (
    validate_cluster_folder_name, suggest_new_cluster_name, copy_local_cluster_to_server,
)


@contextlib.contextmanager
def _isolated_settings_dir():
    """给读写 DSTCamp 自身设置/缓存的测试用——猴子补丁
    get_settings_dir() 指向一个临时目录，测试期间
    load_settings()/save_settings()/cache_dir() 全部间接落到这个临时目
    录，不会碰真实的 %APPDATA%/DSTCamp/。比"读出真实设置、测完再手动写
    回去"更安全：就算测试中途抛异常/被打断，真实用户数据也从来没被碰
    过，不需要指望 finally 里的恢复逻辑生效。

    要打两个补丁，不是一个：resource_paths.py 是用
    `from dstools.core.app_settings import get_settings_dir` 把函数抄
    了一份到自己的模块命名空间里，只改 app_settings 模块自己的属性，
    resource_paths.cache_dir() 用的还是抄过去的那份旧引用——两个模块
    各自的 `get_settings_dir` 名字都要替换掉才能让 load_settings()/
    save_settings()（走 app_settings 自己那份）和 cache_dir()（走
    resource_paths 抄的那份）同时生效。"""
    import dstools.core.app_settings as app_settings
    import dstools.core.resource_paths as resource_paths

    original_in_app_settings = app_settings.get_settings_dir
    original_in_resource_paths = resource_paths.get_settings_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        patched = lambda: Path(tmpdir)
        app_settings.get_settings_dir = patched
        resource_paths.get_settings_dir = patched
        try:
            yield Path(tmpdir)
        finally:
            app_settings.get_settings_dir = original_in_app_settings
            resource_paths.get_settings_dir = original_in_resource_paths


def test_lua_parser_basic():
    """Test basic Lua table parsing."""
    print("=" * 60)
    print("Test 1: Lua Parser - Basic")
    result = parse_lua_table('return {a=1, b="hello", c=true, d=false}')
    assert result == {"a": 1, "b": "hello", "c": True, "d": False}, f"Got: {result}"
    print("  PASS: Basic types parsed correctly")


def test_lua_parser_nested():
    """Test nested table parsing."""
    print("Test 2: Lua Parser - Nested Tables")
    result = parse_lua_table('return {a={b={c=42}}, d={1, 2, 3}}')
    assert "a" in result
    assert result["a"]["b"]["c"] == 42
    assert "1" in result["d"]
    print("  PASS: Nested tables parsed correctly")


def test_lua_parser_roundtrip():
    """Test Lua table round-trip (parse -> serialize -> parse)."""
    print("Test 3: Lua Parser - Round-trip")
    original = (
        'return {\n'
        '    ["workshop-123"]={\n'
        '        configuration_options={\n'
        '            audio=false,\n'
        '            language="ch",\n'
        '            volume=0.75,\n'
        '            count=42\n'
        '        },\n'
        '        enabled=true\n'
        '    },\n'
        '    ["workshop-456"]={\n'
        '        configuration_options={},\n'
        '        enabled=false\n'
        '    }\n'
        '}'
    )
    parsed = parse_lua_table(original)
    serialized = serialize_lua_table(parsed)
    re_parsed = parse_lua_table(serialized)
    assert parsed == re_parsed, f"Round-trip failed!\nOriginal parsed: {parsed}\nRe-parsed: {re_parsed}"
    print("  PASS: Round-trip preserves all data")


def test_lua_parser_real_data():
    """Test parsing real DST modoverrides.lua."""
    print("Test 4: Lua Parser - Real DST Data")
    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    mod_path = klei_root / "Cluster_3" / "Master" / "modoverrides.lua"
    if not mod_path.exists():
        print(f"  SKIP: {mod_path} not found")
        return

    # Parse
    data = parse_lua_file(mod_path)
    assert len(data) >= 30, f"Expected 30+ mods, got {len(data)}"
    print(f"  PASS: Parsed {len(data)} mods from real modoverrides.lua")

    # Round-trip
    serialized = serialize_lua_table(data)
    re_parsed = LuaTableParser(serialized).parse()

    # Check key sets match
    original_keys = set(data.keys())
    re_keys = set(re_parsed.keys())
    assert original_keys == re_keys, f"Key mismatch: {original_keys - re_keys}, {re_keys - original_keys}"

    # Check all mods have enabled and configuration_options
    for wid, entry in data.items():
        assert "enabled" in entry, f"Missing 'enabled' in {wid}"
        assert "configuration_options" in entry, f"Missing 'configuration_options' in {wid}"

    print(f"  PASS: Round-trip verified ({len(data)} mods)")
    print(f"  PASS: All mods have required fields")


def test_ini_parser():
    """Test INI config parsing."""
    print("\n" + "=" * 60)
    print("Test 5: INI Parser")

    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

    # Test cluster.ini
    cluster_ini = klei_root / "Cluster_3" / "cluster.ini"
    if cluster_ini.exists():
        config = parse_cluster_ini(cluster_ini)
        assert config.gameplay["game_mode"] == "survival"
        assert "max_players" in config.gameplay
        assert "cluster_name" in config.network
        assert "shard_enabled" in config.shard
        print(f"  PASS: cluster.ini parsed - mode={config.gameplay['game_mode']}, "
              f"players={config.gameplay['max_players']}")

        # Test round-trip for INI
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "cluster.ini"
            write_cluster_ini(config, tmp_path)
            re_parsed = parse_cluster_ini(tmp_path)
            assert config.gameplay == re_parsed.gameplay
            assert config.network == re_parsed.network
            print("  PASS: cluster.ini round-trip verified")

    # Test server.ini
    server_ini = klei_root / "Cluster_3" / "Master" / "server.ini"
    if server_ini.exists():
        config = parse_server_ini(server_ini)
        assert "server_port" in config.network
        assert config.shard.get("is_master") == True
        print(f"  PASS: server.ini parsed - port={config.network['server_port']}, "
              f"is_master={config.shard['is_master']}")


def test_discovery():
    """Test path discovery."""
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
        print(f"  PASS: {c.name} has {len(c.shards)} shard(s), source={c.source.value}: "
              f"{[s.name for s in c.shards]}")

    # 按 SaveSource 分类计数上报——不硬性要求两边都非空（这台机器目前
    # 两种都有，但换一台只装了专用服务器/只有本地存档的机器完全可能只
    # 有一边，不是 bug，不该让测试失败）。
    server_count = sum(1 for c in env.clusters if c.source == SaveSource.SERVER)
    local_count = sum(1 for c in env.clusters if c.source == SaveSource.LOCAL)
    assert server_count + local_count == len(env.clusters)
    print(f"  PASS: {server_count} server + {local_count} local cluster(s)")


def test_save_reader():
    """Test save session reading."""
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
                    assert session.source == c.source, "会话的 source 标记应该跟它所属 cluster 的一致"
                    print(f"    Session {session.session_id}: {summary} (source={session.source.value})")

                    if session.metadata:
                        assert session.metadata.day >= 0
                        assert session.metadata.season
                        print(f"    Metadata: day={session.metadata.day}, "
                              f"season={session.metadata.season}, "
                              f"phase={session.metadata.phase}")
                break  # Only test first shard with sessions
        break  # Only test first cluster


def test_mod_manager():
    """Test mod management operations. Pure tempdir/synthetic data --
    no real DST install needed, unlike the other tests around it."""
    print("\n" + "=" * 60)
    print("Test 8: Mod Manager")

    # Use a temp file for safe testing
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "modoverrides.lua"

        # Create empty overrides
        overrides = ModOverrides(path=tmp_path)

        # Test add and enable
        enable_mod(overrides, "workshop-test-1")
        assert "workshop-test-1" in overrides.mods
        assert overrides.mods["workshop-test-1"].enabled
        print("  PASS: Enable mod (adds if not present)")

        # Test disable
        disable_mod(overrides, "workshop-test-1")
        assert not overrides.mods["workshop-test-1"].enabled
        print("  PASS: Disable mod")

        # Test config
        set_mod_config(overrides, "workshop-test-1", "language", "ch")
        set_mod_config(overrides, "workshop-test-1", "volume", 0.75)
        assert get_mod_config(overrides, "workshop-test-1", "language") == "ch"
        assert get_mod_config(overrides, "workshop-test-1", "volume") == 0.75
        print("  PASS: Set/get mod config")

        # Test save and reload
        save_mod_overrides(overrides)
        reloaded = load_mod_overrides(tmp_path)
        assert len(list_mods(reloaded)) == 1
        assert get_mod_config(reloaded, "workshop-test-1", "language") == "ch"
        print("  PASS: Save and reload preserves data")

        # Test remove
        assert remove_mod(overrides, "workshop-test-1")
        assert "workshop-test-1" not in overrides.mods
        print("  PASS: Remove mod")

        # Test diff
        a = ModOverrides(path=Path("/tmp/a.lua"))
        b = ModOverrides(path=Path("/tmp/b.lua"))
        enable_mod(a, "workshop-shared")
        enable_mod(a, "workshop-only-a")
        enable_mod(b, "workshop-shared")
        enable_mod(b, "workshop-only-b")

        diff = diff_mods(a, b)
        assert "workshop-only-a" in diff["only_in_a"]
        assert "workshop-only-b" in diff["only_in_b"]
        print("  PASS: Mod diff works")

        # Test sync
        sync_mods(a, b)
        assert "workshop-only-a" in b.mods
        assert "workshop-only-b" not in b.mods
        print("  PASS: Mod sync works")


def test_config_manager():
    """Test config manager operations."""
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

    # Test write and read round-trip
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


def test_cli_import():
    """Test that CLI module imports correctly."""
    print("\n" + "=" * 60)
    print("Test 10: CLI Import")
    from dstools.cli.main import cli
    assert cli is not None
    print("  PASS: CLI module imports successfully")


def test_list_session_players():
    """Test per-player character save discovery/parsing under a session dir."""
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
            'return {x=100.5,z=-50.25,data={health={health=120},'
            'sanity={current=150,sane=true},hunger={hunger=100},'
            'age={age=42}},age=0,prefab="wilson"}'
        )
        (good_dir / "0000000007").write_bytes(b"\x03\x11\x22" + table_text.encode("utf-8") + b"\x01")
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
        assert not good.parse_error, f"Good player should parse cleanly, got: {good.parse_error}"
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
        print("  PASS: Corrupt player entry isolated (parse_error set, other player unaffected)")

        # 真机在本地存档上复现过的情况：跨分片传送/进程被打断时，DST 会把
        # 编号最新的槽位写成 0 字节占位文件，真正数据还在上一个槽位里——
        # 必须回退去读那一个，不能因为最新槽位是空文件就整条判定解析失败。
        empty_latest_dir = session_dir / "A7EMPTYLATEST"
        empty_latest_dir.mkdir()
        (empty_latest_dir / "0000000010").write_bytes(
            b"\x03\x11\x22" + 'return {data={health={health=88}},prefab="willow"}'.encode("utf-8") + b"\x01"
        )
        (empty_latest_dir / "0000000010.meta").write_bytes(b'return {character="willow"}\x00')
        (empty_latest_dir / "0000000011").write_bytes(b"")  # 0 字节占位文件
        (empty_latest_dir / "0000000011.meta").write_bytes(b'return {character="willow"}\x00')

        players = list_session_players(session)
        empty_latest = next(p for p in players if p.player_id == "A7EMPTYLATEST")
        assert not empty_latest.parse_error, f"Should fall back to slot 10, got: {empty_latest.parse_error}"
        assert empty_latest.slot_number == 10
        assert empty_latest.health == 88
        print("  PASS: Falls back to the newest non-empty slot when the latest one is a 0-byte placeholder")


def test_character_names():
    """Test character prefab -> display name lookup."""
    print("\n" + "=" * 60)
    print("Test 12: Character Name Lookup")

    assert get_character_display_name("wilson") == "威尔逊.P.希格斯伯里"
    assert get_character_display_name("willow", "en") == "Willow"
    assert get_character_display_name("wolfgang") == "沃尔夫冈"
    print("  PASS: Known vanilla characters resolve to verified display names")

    # 模组自定义角色查不到，原样返回，不猜测拼凑
    assert get_character_display_name("some_modded_character") == "some_modded_character"
    print("  PASS: Unknown/modded prefab falls back to raw name unchanged")


def test_character_icons():
    """Test mod character-name scanning + resolve_character fallback chain
    (character_icons.py). 头像转换本身依赖真实 Steam 安装/ktech.exe，这里
    只覆盖不需要真机环境的部分：正则扫描模组 .lua 文件找角色名声明、以及
    resolve_character 在"官方表命中"和"哪里都找不到"两种情况下的行为。"""
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
        print("  PASS: Mod-declared STRINGS.CHARACTER_NAMES.<prefab> found via regex scan")

        assert find_mod_character_name(mod_folder, "no_such_prefab") is None
        print("  PASS: Prefab not declared by this mod returns None (no guessing)")

    # 官方角色表命中：不需要 mod_overrides_path，直接走官方分支。
    name, _icon = resolve_character("wilson", None)
    assert name == "威尔逊.P.希格斯伯里"
    print("  PASS: resolve_character resolves known vanilla prefab without touching mods")

    # 哪里都找不到（未知 prefab + 不存在的 modoverrides 路径）：原样回退，
    # 不抛异常、不给头像。
    name, icon = resolve_character("totally_unknown_prefab", Path(tmp) / "does_not_exist.lua")
    assert name == "totally_unknown_prefab" and icon is None
    print("  PASS: Unresolvable prefab falls back to raw name with no icon")


def test_modinfo_reader():
    """Test modinfo.lua parsing (modinfo_reader.py) against a hand-written
    synthetic mod -- this logic previously had zero functional test
    coverage (only ever loaded at import time via gui/app.py)."""
    print("\n" + "=" * 60)
    print("Test 14: Modinfo Parsing")

    with tempfile.TemporaryDirectory() as tmp:
        mod_folder = Path(tmp) / "123456"
        mod_folder.mkdir()
        (mod_folder / "modinfo.lua").write_text(
            '''
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
            ''',
            encoding="utf-8",
        )

        info = parse_modinfo(mod_folder)
        assert info is not None
        assert info.name == "Test Mod" and info.author == "Tester" and info.version == "1.0.0"
        assert info.workshop_id == "workshop-123456"
        print("  PASS: Top-level fields (name/author/version/workshop_id) parsed correctly")

        assert len(info.config_options) == 1
        opt = info.config_options[0]
        assert opt.name == "difficulty" and opt.label == "Difficulty"
        assert [c["data"] for c in opt.choices] == ["easy", "hard"]
        print("  PASS: configuration_options choices parsed correctly")

        # 不存在 modinfo.lua 的文件夹：明确返回 None，不抛异常。
        assert parse_modinfo(Path(tmp) / "does_not_exist") is None
        print("  PASS: Missing modinfo.lua returns None")


def test_admin_manager():
    """Test adminlist.txt read/write round-trip (admin_manager.py)."""
    print("\n" + "=" * 60)
    print("Test 15: Admin List Manager")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "adminlist.txt"

        assert read_adminlist(path) == []
        print("  PASS: Missing adminlist.txt reads as empty list")

        assert add_admin(path, "KU_aaaaaaaa") is True
        assert add_admin(path, "KU_bbbbbbbb") is True
        assert add_admin(path, "KU_aaaaaaaa") is False, "Adding an existing admin should be a no-op"
        assert read_adminlist(path) == ["KU_aaaaaaaa", "KU_bbbbbbbb"]
        assert has_admin(path, "KU_bbbbbbbb") is True
        print("  PASS: add_admin appends new IDs and rejects duplicates")

        assert remove_admin(path, "KU_aaaaaaaa") is True
        assert remove_admin(path, "KU_aaaaaaaa") is False, "Removing an absent admin should be a no-op"
        assert read_adminlist(path) == ["KU_bbbbbbbb"]
        print("  PASS: remove_admin removes an entry and is idempotent")


def test_token_manager():
    """Test cluster_token.txt read/write round-trip + masking (token_manager.py)."""
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
        print("  PASS: is_valid_token distinguishes real tokens from empty/short strings")

        masked = mask_token(token)
        assert masked.startswith(token[:8]) and masked.endswith(token[-8:]) and "..." in masked
        assert mask_token("short") == "*" * len("short")
        print("  PASS: mask_token shows only the ends of a real token, fully masks short ones")


def test_backup_utils():
    """Test the real backup-copy-and-prune path (backup_utils.py) -- prior
    coverage only ever hit the "source file doesn't exist" early return."""
    print("\n" + "=" * 60)
    print("Test 17: Backup Utils")

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "cluster.ini"
        src.write_text("[GAMEPLAY]\nmax_players = 6\n", encoding="utf-8")

        backup_path = backup_file(src)
        assert backup_path is not None and backup_path.exists()
        assert backup_path.parent == src.parent / "backup"
        assert backup_path.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
        print("  PASS: backup_file copies the source into a backup/ subfolder with matching content")

        # _prune_old_backups 直接测（不依赖真的连续调用 backup_file 在同一
        # 秒内产生足够多互不相同的时间戳文件名，那样会因为文件名撞车而不
        # 可靠）：手工造 7 个时间戳递增的备份文件，裁剪到只留 5 个最新的。
        backup_dir = src.parent / "backup"
        for i in range(7):
            (backup_dir / f"cluster.ini.bak.2024010{i}_000000").write_text("x", encoding="utf-8")
        _prune_old_backups(backup_dir, "cluster.ini", 5)
        remaining = sorted(p.name for p in backup_dir.glob("cluster.ini.bak.*"))
        assert len(remaining) == 5
        assert remaining == sorted(remaining), "The newest (lexically largest) timestamps must survive"
        assert "cluster.ini.bak.20240100_000000" not in remaining
        assert "cluster.ini.bak.20240101_000000" not in remaining
        print("  PASS: _prune_old_backups keeps only the newest max_backups copies")


def test_cluster_copy():
    """Test the "复制为服务器存档" logic (cluster_copy.py): name
    validation, default-name suggestion, and the actual folder copy."""
    print("\n" + "=" * 60)
    print("Test 18: Cluster Copy (local save -> server save)")

    assert validate_cluster_folder_name("MyServer") is None
    assert validate_cluster_folder_name("Cluster_5") is None
    assert validate_cluster_folder_name("") == "empty"
    assert validate_cluster_folder_name("   ") == "empty"
    assert validate_cluster_folder_name("bad/name") == "invalid_chars"
    assert validate_cluster_folder_name("..") == "reserved"
    print("  PASS: validate_cluster_folder_name accepts arbitrary legal names, "
          "rejects empty/illegal-char/reserved names (no Cluster_<N> format required)")

    with tempfile.TemporaryDirectory() as tmp:
        klei_root = Path(tmp) / "klei_root"
        klei_root.mkdir()
        (klei_root / "Cluster_1").mkdir()  # 已占用

        assert suggest_new_cluster_name(klei_root, "Cluster_1") == "Cluster_2"
        assert suggest_new_cluster_name(klei_root, "MyLocalSave") == "MyLocalSave"
        print("  PASS: suggest_new_cluster_name falls back to Cluster_N only when the "
          "preferred (source) name is already taken")

        # 造一个假的本地 cluster 文件夹（cluster.ini + 一个假分片子目录），
        # 复制到 klei_root 下一个新名字。
        local_cluster = Path(tmp) / "local_user" / "Cluster_1"
        (local_cluster / "Master").mkdir(parents=True)
        (local_cluster / "cluster.ini").write_text("[GAMEPLAY]\nmax_players=6\n", encoding="utf-8")
        (local_cluster / "Master" / "server.ini").write_text("[NETWORK]\n", encoding="utf-8")

        logs = []
        dest = copy_local_cluster_to_server(local_cluster, klei_root, "Cluster_2", on_log=logs.append)
        assert dest == klei_root / "Cluster_2"
        assert (dest / "cluster.ini").read_text(encoding="utf-8") == (local_cluster / "cluster.ini").read_text(encoding="utf-8")
        assert (dest / "Master" / "server.ini").exists()
        assert local_cluster.exists() and (local_cluster / "cluster.ini").exists(), "源文件夹必须保持不变"
        assert len(logs) > 0
        print("  PASS: copy_local_cluster_to_server copies the whole folder (files + shard "
              "subfolders) and leaves the source untouched")

        try:
            copy_local_cluster_to_server(local_cluster, klei_root, "Cluster_2")
            assert False, "Copying onto an already-existing destination must raise"
        except FileExistsError:
            print("  PASS: copying onto an existing destination raises instead of overwriting")


def test_player_notes():
    """Test per-player note storage (app_settings.py)."""
    print("\n" + "=" * 60)
    print("Test 19: Player Notes")

    with _isolated_settings_dir():
        assert get_player_note("TEST_NONEXISTENT_ID") == "", "Unset note should be empty string"
        print("  PASS: Unset player note defaults to empty string")

        set_player_note("TEST_PLAYER_A", "老王的存档")
        assert get_player_note("TEST_PLAYER_A") == "老王的存档"
        print("  PASS: Set/get player note round-trips")

        set_player_note("TEST_PLAYER_A", "")
        assert get_player_note("TEST_PLAYER_A") == "", "Clearing a note should remove it, not leave an empty entry"
        assert "TEST_PLAYER_A" not in load_settings().get("player_notes", {})
        print("  PASS: Clearing a note removes the entry instead of leaving a blank one")


def test_app_settings_toggles():
    """Test the minimize-on-close / cache-use-exe-dir persisted toggles
    (app_settings.py) added for the tray + settings-dialog feature."""
    print("\n" + "=" * 60)
    print("Test 20: App Settings Toggles")

    with _isolated_settings_dir():
        assert get_minimize_on_close() is True, "Default should be enabled"
        print("  PASS: minimize_on_close defaults to True when unset")

        set_minimize_on_close(False)
        assert get_minimize_on_close() is False
        set_minimize_on_close(True)
        assert get_minimize_on_close() is True
        print("  PASS: minimize_on_close round-trips")

        assert get_cache_use_exe_dir() is False, "Default should be disabled"
        print("  PASS: cache_use_exe_dir defaults to False when unset")

        set_cache_use_exe_dir(True)
        assert get_cache_use_exe_dir() is True
        set_cache_use_exe_dir(False)
        assert get_cache_use_exe_dir() is False
        print("  PASS: cache_use_exe_dir round-trips")


def test_mod_sync_incremental_copy():
    """Test mod_sync.py's _skip_if_unchanged_copy2 -- the fix for "同步mod
    文件到服务器每次都很慢" (it used to unconditionally re-copy every file
    via shutil.copytree)."""
    print("\n" + "=" * 60)
    print("Test 21: Mod Sync Incremental Copy")

    from dstools.core.mod_sync import _skip_if_unchanged_copy2

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.txt")
        dst = os.path.join(tmp, "dst.txt")

        with open(src, "w") as f:
            f.write("hello")
        _skip_if_unchanged_copy2(src, dst)
        assert open(dst).read() == "hello"
        print("  PASS: first copy always happens (destination didn't exist)")

        # 目标 mtime 设成比源更新，但内容（大小）不同——size 校验应该
        # 阻止误判为"没变化"而跳过。
        future = time.time() + 100
        os.utime(dst, (future, future))
        with open(src, "w") as f:
            f.write("changed, longer content")
        _skip_if_unchanged_copy2(src, dst)
        assert open(dst).read() == "changed, longer content", \
            "Size mismatch must force a real copy even if dst mtime is newer"
        print("  PASS: size mismatch forces a real copy (mtime alone isn't trusted)")

        # 现在源和目标完全一致——上一步的真实复制（shutil.copy2）已经把
        # dst 的 mtime 同步成源当时的 mtime 了，大小也相同。再调一次应该
        # 直接跳过、不做任何真实复制；用 dst 的 mtime 有没有变化来验证
        # "跳过"确实发生了（真复制一定会刷新 mtime，哪怕内容一样）。
        dst_mtime_before = os.stat(dst).st_mtime
        _skip_if_unchanged_copy2(src, dst)
        assert os.stat(dst).st_mtime == dst_mtime_before, \
            "Unchanged file (same mtime + size) should be skipped, not re-copied"
        print("  PASS: unchanged file (same mtime + size) is skipped")


def test_theme_set_theme():
    """Test theme.py's set_theme() -- the live theme-switch mechanism.
    Pure logic (module-level color variable reassignment), no real Tk
    window needed. Four themes exist ("gray" default + mint/twilight/
    campfire) -- verify (a) switching actually reassigns the palette,
    (b) an unknown name falls back to "gray" instead of raising, (c)
    background-image support (BG_IMAGE_ENABLED) is gone from theme.py
    entirely now that it's decoupled from theming (see custom_background.py
    -- background image applies regardless of active theme)."""
    print("\n" + "=" * 60)
    print("Test 22: Theme Live Switch")

    from dstools.gui import theme

    original_primary = theme.PRIMARY
    try:
        theme.set_theme("gray")
        assert theme.PRIMARY == "#8A97A3"
        assert not hasattr(theme, "BG_IMAGE_ENABLED"), "背景图已跟主题解耦，theme.py 不应再有这个字段"
        assert theme.WINDOW_ALPHA == 1.0, "整窗透明效果已经按用户要求去掉，只保留图片自身的透明度"
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


def test_world_categories_bilingual():
    """Test world_categories.py's get_setting_info()/get_categories()
    returning zh/en names based on the current i18n language -- the fix for
    "世界设置切英文不生效"."""
    print("\n" + "=" * 60)
    print("Test 23: World Categories Bilingual")

    from dstools.core.world_categories import get_setting_info, get_categories
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
        print("  PASS: unknown key falls back to (\"other\", False, key)")
    finally:
        set_lang(original_lang)


def test_custom_background():
    """Test custom_background.py's crop-to-ratio (never stretch) + opacity
    blend logic -- the core of "支持自定义背景图，按比例裁剪不拉伸，可调
    不透明度贴合主题"。纯 PIL 逻辑，不需要真实 Tk 窗口。"""
    print("\n" + "=" * 60)
    print("Test 24: Custom Background Image")

    from PIL import Image

    from dstools.core.custom_background import _center_crop_to_ratio, render_background

    # 宽图裁窄比例：裁掉左右两侧，裁完的宽高比必须刚好等于目标比例
    # （不是拉伸变形出来的），且没有超出原图尺寸。
    wide = Image.new("RGB", (400, 100), "red")
    cropped = _center_crop_to_ratio(wide, 1.0)
    assert cropped.size == (100, 100), "Wide image cropped to a square must trim the sides, not stretch"
    print("  PASS: wider-than-target image is center-cropped on the sides")

    # 高图裁宽比例：裁掉上下两侧。
    tall = Image.new("RGB", (100, 400), "blue")
    cropped2 = _center_crop_to_ratio(tall, 2.0)
    assert cropped2.size == (100, 50), "Tall image cropped to a wide ratio must trim top/bottom, not stretch"
    print("  PASS: taller-than-target image is center-cropped on top/bottom")

    # 不透明度混合的两个边界：0 = 完全是主题纯色（图片全隐），1 = 完全是原图。
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "bg.png"
        Image.new("RGB", (50, 50), (255, 0, 0)).save(src)

        transparent = render_background(src, 20, 20, 0.0, "#00FF00")
        assert transparent.getpixel((10, 10)) == (0, 255, 0), "opacity=0 must show only the theme's blend color"

        opaque = render_background(src, 20, 20, 1.0, "#00FF00")
        assert opaque.getpixel((10, 10)) == (255, 0, 0), "opacity=1 must show only the original image"
        print("  PASS: opacity=0/1 blend to pure theme color / pure image respectively")


def test_mod_resolve_cache():
    """Test mod_resolve_cache.py's disk-persisted cache for
    resolve_full_modinfo() results -- added because the Lua 沙箱全量解析
    之前只在内存里缓存一份，每次重启应用都要为没变过的 mod 重新跑一遍
    （真机反馈过"启动要卡 3 秒"，profile 出来这是大头之一）。这里只测纯
    数据逻辑（mtime 失效判断 + JSON 往返，含 ModConfigOption 这个
    dataclass 的序列化/反序列化），不需要真的跑一遍 Lua 沙箱。"""
    print("\n" + "=" * 60)
    print("Test 25: Mod Resolve Cache")

    # _isolated_settings_dir() 必须在 import mod_resolve_cache 之前进
    # 入——那个模块的 _CACHE_DIR 是 import 时算好的模块级常量
    # （cache_dir("mod_full_resolve")），只有在补丁生效期间第一次
    # import 才能让它落在隔离的临时目录里，不写真实的
    # %APPDATA%/DSTCamp/cache/。
    with _isolated_settings_dir():
        from dstools.core.mod_resolve_cache import load_cached_result, save_result
        from dstools.core.modinfo_reader import ModConfigOption

        workshop_id = "test-workshop-resolve-cache"
        with tempfile.TemporaryDirectory() as tmp:
            modinfo_path = Path(tmp) / "modinfo.lua"
            modinfo_path.write_text("name = 'x'", encoding="utf-8")

            assert load_cached_result(workshop_id, modinfo_path) is None
            print("  PASS: no cache yet returns None")

            result = {
                "name": "测试Mod",
                "config_options": [ModConfigOption(name="opt1", label="选项1", default="a")],
            }
            save_result(workshop_id, result)
            cached = load_cached_result(workshop_id, modinfo_path)
            assert cached is not None and cached["name"] == "测试Mod"
            assert isinstance(cached["config_options"][0], ModConfigOption)
            assert cached["config_options"][0].name == "opt1"
            print("  PASS: save/load round-trips config_options as real ModConfigOption objects")

            # modinfo.lua 比缓存新——缓存失效，返回 None，跟 mod_icons.py
            # 图标缓存同一套 mtime 判断逻辑。
            future = time.time() + 100
            os.utime(modinfo_path, (future, future))
            assert load_cached_result(workshop_id, modinfo_path) is None
            print("  PASS: cache invalidated once modinfo.lua's mtime moves past it")


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
        assert remaining == ["0000000001"], f"应该只剩备份里的旧槽位，实际是 {remaining}"
        print("  PASS: restore_backup() removes slots created after the backup")

        assert (cluster / "Master" / "server.ini").read_text() == "[NETWORK]\nserver_port=1\n"
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

            backup_dir = cluster / "dstcamp_backups"
            backup_dir.mkdir(parents=True)
            for i in range(1, 8):  # 7 份时间戳递增的旧备份（都早于"现在"）
                (backup_dir / f"Cluster_2_2026010{i}_000000.zip").write_bytes(b"")

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

    config = ClusterConfig(gameplay={"vote_enabled": False}, network={}, misc={}, shard={})
    backfill_cluster_defaults(config)

    assert config.gameplay["vote_enabled"] is False, "已经显式设置的值不应该被默认值覆盖"
    print("  PASS: explicitly-set values are not overwritten")

    assert config.network["tick_rate"] == 15, "缺失的字段应该被补上官方默认值"
    assert config.misc["max_snapshots"] == 6
    print("  PASS: missing fields are backfilled with official defaults")


def main():
    """Run all tests."""
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
        test_cli_import,
        test_list_session_players,
        test_character_names,
        test_character_icons,
        test_modinfo_reader,
        test_admin_manager,
        test_token_manager,
        test_backup_utils,
        test_cluster_copy,
        test_player_notes,
        test_app_settings_toggles,
        test_mod_sync_incremental_copy,
        test_theme_set_theme,
        test_world_categories_bilingual,
        test_custom_background,
        test_mod_resolve_cache,
        test_backup_manager_restore_clears_stale_slots,
        test_backup_manager_prune_retention_boundary,
        test_backfill_cluster_defaults_only_fills_missing,
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
