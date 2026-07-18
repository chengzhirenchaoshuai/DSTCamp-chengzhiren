"""Quick test of Lua parser with real DST data."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dstools.core.lua_parser import parse_lua_file, serialize_lua_table, LuaTableParser
from dstools.core.discovery import find_klei_root, discover_environment
from dstools.core.ini_parser import parse_cluster_ini, parse_server_ini
from dstools.core.save_reader import list_save_sessions, read_session_metadata, get_save_summary
from dstools.core.mod_manager import load_mod_overrides, list_mods
from dstools.core.config_manager import load_cluster_config

# Find DST data
klei_root = find_klei_root()
print(f"Klei root: {klei_root}")

# Discover environment
env = discover_environment(klei_root)
print(f"User ID: {env.user_id}")
print(f"Clusters: {[c.name for c in env.clusters]}")

for cluster in env.clusters:
    print(f"\n=== {cluster.name} ===")
    print(f"  Shards: {[s.name for s in cluster.shards]}")

    # Test 1: Parse cluster.ini
    if cluster.path:
        ini_path = cluster.path / "cluster.ini"
        if ini_path.exists():
            config = load_cluster_config(ini_path)
            print(f"  [cluster.ini]")
            print(f"    Game mode: {config.gameplay.get('game_mode', 'N/A')}")
            print(f"    Max players: {config.gameplay.get('max_players', 'N/A')}")
            print(f"    Cluster name: {config.network.get('cluster_name', 'N/A')}")

    for shard in cluster.shards:
        print(f"\n  --- {shard.name} ---")

        # Test 2: Parse modoverrides.lua
        if shard.mod_overrides_path:
            mods = load_mod_overrides(shard.mod_overrides_path)
            mod_list = list_mods(mods)
            enabled = sum(1 for m in mod_list if m.enabled)
            print(f"  Mods: {len(mod_list)} total, {enabled} enabled")
            if mod_list:
                # Show first 3 mods
                for m in mod_list[:3]:
                    opts = len(m.configuration_options)
                    print(f"    {m.workshop_id}: enabled={m.enabled}, {opts} options")

                # Test round-trip
                try:
                    raw = parse_lua_file(shard.mod_overrides_path)
                    serialized = serialize_lua_table(raw)
                    reparsed = LuaTableParser(serialized).parse()
                    keys_ok = set(raw.keys()) == set(reparsed.keys())
                    print(f"  Round-trip: {'OK' if keys_ok else 'FAIL'} ({len(raw)} -> {len(reparsed)} mods)")
                except Exception as e:
                    print(f"  Round-trip ERROR: {e}")

        # Test 3: List save sessions
        sessions = list_save_sessions(shard.path)
        if sessions:
            print(f"  Save sessions: {len(sessions)}")
            for sess in sessions[:3]:
                summary = get_save_summary(sess)
                print(f"    {sess.session_id[:16]}: {summary}")

print("\n=== All tests passed! ===")
