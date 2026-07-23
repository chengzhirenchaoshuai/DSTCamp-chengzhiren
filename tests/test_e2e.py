"""End-to-end verification tests for dstools."""

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
    write_server_ini,
)
from dstools.models import ClusterConfig, ShardConfig
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
from dstools.core.config_manager import load_cluster_config, set_cluster_option, save_cluster_config
from dstools.core.character_names import get_character_display_name
from dstools.core.character_icons import find_mod_character_name, resolve_character
from dstools.core.app_settings import (
    load_settings, save_settings, get_player_note, set_player_note,
    get_minimize_on_close, set_minimize_on_close,
    get_cache_use_exe_dir, set_cache_use_exe_dir,
)
from dstools.models import SaveSession
from dstools.core.modinfo_reader import parse_modinfo
from dstools.core.admin_manager import read_adminlist, write_adminlist, add_admin, remove_admin, has_admin
from dstools.core.token_manager import read_token, write_token, mask_token, is_valid_token
from dstools.core.backup_utils import backup_file, _prune_old_backups
from dstools.core.cluster_copy import (
    validate_cluster_folder_name, suggest_new_cluster_name, copy_local_cluster_to_server,
)


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
        print(f"  PASS: {c.name} has {len(c.shards)} shard(s): "
              f"{[s.name for s in c.shards]}")


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
                    print(f"    Session {session.session_id}: {summary}")

                    if session.metadata:
                        assert session.metadata.day >= 0
                        assert session.metadata.season
                        print(f"    Metadata: day={session.metadata.day}, "
                              f"season={session.metadata.season}, "
                              f"phase={session.metadata.phase}")
                break  # Only test first shard with sessions
        break  # Only test first cluster


def test_mod_manager():
    """Test mod management operations."""
    print("\n" + "=" * 60)
    print("Test 8: Mod Manager")

    klei_root = find_klei_root()
    if not klei_root:
        print("  SKIP: No DST data found")
        return

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

    # 这几个函数读写的是用户真实的 %APPDATA%/DSTCamp/settings.json，
    # 测试前后必须把它还原成原样，不能在用户机器上留下测试痕迹。
    before = load_settings()
    try:
        assert get_player_note("TEST_NONEXISTENT_ID") == "", "Unset note should be empty string"
        print("  PASS: Unset player note defaults to empty string")

        set_player_note("TEST_PLAYER_A", "老王的存档")
        assert get_player_note("TEST_PLAYER_A") == "老王的存档"
        print("  PASS: Set/get player note round-trips")

        set_player_note("TEST_PLAYER_A", "")
        assert get_player_note("TEST_PLAYER_A") == "", "Clearing a note should remove it, not leave an empty entry"
        assert "TEST_PLAYER_A" not in load_settings().get("player_notes", {})
        print("  PASS: Clearing a note removes the entry instead of leaving a blank one")
    finally:
        save_settings(before)
        assert load_settings() == before, "Real settings.json must be restored after this test"
        print("  PASS: Real settings.json restored to its pre-test state")


def test_app_settings_toggles():
    """Test the minimize-on-close / cache-use-exe-dir persisted toggles
    (app_settings.py) added for the tray + settings-dialog feature."""
    print("\n" + "=" * 60)
    print("Test 20: App Settings Toggles")

    before = load_settings()
    try:
        # 测"没设置过时的默认值"要先把这两个 key 从真实设置里摘掉，不能直接
        # 假设当前机器上的持久化值就是默认值——这台机器上 minimize_on_close
        # 之前测试托盘功能时被手动置过 False，一直没改回来，直接断言"现在
        # 读到的就是默认值"并不可靠。
        cleared = dict(before)
        cleared.pop("minimize_on_close", None)
        cleared.pop("cache_use_exe_dir", None)
        save_settings(cleared)

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
    finally:
        save_settings(before)
        assert load_settings() == before, "Real settings.json must be restored after this test"
        print("  PASS: Real settings.json restored to its pre-test state")


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
    window needed. Only one theme ("custom_bg") exists now, so there's no
    "switch between two themes" to test -- what's actually worth verifying
    is (a) it assigns the expected palette/flags and (b) an unknown name
    falls back to "custom_bg" instead of raising."""
    print("\n" + "=" * 60)
    print("Test 22: Theme Live Switch")

    from dstools.gui import theme

    original_primary = theme.PRIMARY
    try:
        theme.set_theme("custom_bg")
        assert theme.PRIMARY == "#8A97A3"
        assert theme.BG_IMAGE_ENABLED is True
        assert theme.WINDOW_ALPHA == 1.0, "整窗透明效果已经按用户要求去掉，只保留图片自身的透明度"
        print("  PASS: set_theme() reassigns theme.py's module-level color constants")

        theme.set_theme("some_removed_theme_name")
        assert theme.PRIMARY == "#8A97A3"
        print("  PASS: unknown theme name falls back to custom_bg instead of raising")
    finally:
        theme.set_theme("custom_bg")
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
