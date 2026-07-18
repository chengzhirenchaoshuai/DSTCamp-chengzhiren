"""End-to-end verification tests for dstools."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

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
from dstools.core.save_reader import list_save_sessions, get_save_summary
from dstools.core.config_manager import load_cluster_config, set_cluster_option, save_cluster_config


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
