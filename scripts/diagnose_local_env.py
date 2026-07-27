"""本机真实环境诊断脚本——不是自动化测试（没有 assert，纯打印），需要这台
机器真的装了 DST 并且有实际存档数据（find_klei_root() 会扫描真实路径）。
换一台没装 DST 的机器上跑，各步骤会因为 klei_root 是 None 而静默跳过、什么
都不打印，"通过"字样并不代表验证了什么，只用于开发时人工核对输出是否合理。

Usage (run from the project root): python scripts/diagnose_local_env.py
"""
import os
import sys

# 项目根目录（这个脚本在 scripts/ 下，比根目录多一层）加入 sys.path，
# 才能 import dstools。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dstools.core.lua_parser import parse_lua_file, serialize_lua_table, LuaTableParser
from dstools.core.discovery import find_klei_root, discover_environment
from dstools.core.save_reader import (
    list_save_sessions, get_save_summary, list_session_players,
)
from dstools.core.character_names import get_character_display_name, CHARACTER_NAMES
from dstools.core.character_icons import resolve_character
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

                # Test 4: Per-player character status -- this is real, on-disk
                # data (not synthetic), which is how the binary-framing bug in
                # some player slot files (leftover garbage after the real
                # closing brace) was actually caught during planning.
                players = list_session_players(sess)
                if players:
                    print(f"      Players: {len(players)}")
                    for p in players:
                        if p.parse_error:
                            print(f"        {p.player_id}: PARSE ERROR - {p.parse_error}")
                            continue
                        known = p.character in CHARACTER_NAMES
                        name = get_character_display_name(p.character)
                        tag = "matched" if known else "fallback (modded/unknown)"
                        print(f"        {p.player_id}: {name} [{tag}] "
                              f"hp={p.health} sanity={p.sanity} hunger={p.hunger}")

                        # Test 5: 头像/模组角色名解析 -- 同样是真机数据，官方
                        # 头像需要真实安装的 data/bigportraits/，模组角色名/
                        # 头像需要真实安装的模组文件夹，合成测试覆盖不到。
                        resolved_name, icon_path = resolve_character(
                            p.character, shard.mod_overrides_path)
                        icon_state = str(icon_path) if icon_path else "(no icon)"
                        print(f"          resolve_character -> {resolved_name} | {icon_state}")

print("\n=== 诊断输出结束（以上没有任何自动校验，需要人工核对内容是否合理）===")
