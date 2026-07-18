"""CLI interface for DST save and mod management tool."""

import sys
from pathlib import Path
from typing import Optional

import click

from dstools import __version__
from dstools.core.config_manager import (
    get_cluster_option,
    get_shard_option,
    load_cluster_config,
    load_shard_config,
    save_cluster_config,
    save_shard_config,
    set_cluster_option,
    set_shard_option,
)
from dstools.core.discovery import discover_environment
from dstools.core.mod_manager import (
    disable_mod,
    enable_mod,
    get_mod,
    get_mod_config,
    list_mods,
    load_mod_overrides,
    remove_mod,
    save_mod_overrides,
    set_mod_config,
    sync_mods,
)
from dstools.core.save_reader import get_save_summary, list_save_sessions
from dstools.models import Cluster, Shard


def _find_cluster(env, name: Optional[str]) -> Cluster:
    """Find a cluster by name, or return the first/only one."""
    if not env.clusters:
        raise click.UsageError("No DST clusters found. Is DST installed and configured?")

    if name:
        for c in env.clusters:
            if c.name == name:
                return c
        raise click.UsageError(f"Cluster '{name}' not found. Available: "
                               f"{', '.join(c.name for c in env.clusters)}")

    if len(env.clusters) == 1:
        return env.clusters[0]

    raise click.UsageError(f"Multiple clusters found. Specify one with --cluster: "
                           f"{', '.join(c.name for c in env.clusters)}")


def _find_shard(cluster: Cluster, name: Optional[str]) -> Shard:
    """Find a shard by name, or return the first/only one."""
    if not cluster.shards:
        raise click.UsageError(f"No shards found in cluster '{cluster.name}'.")

    if name:
        for s in cluster.shards:
            if s.name == name:
                return s
        raise click.UsageError(f"Shard '{name}' not found. Available: "
                               f"{', '.join(s.name for s in cluster.shards)}")

    if len(cluster.shards) == 1:
        return cluster.shards[0]

    # Default to Master if available
    for s in cluster.shards:
        if s.name == "Master":
            return s

    raise click.UsageError(f"Multiple shards found. Specify one with --shard: "
                           f"{', '.join(s.name for s in cluster.shards)}")


# ── Root Command ───────────────────────────────────────────────────────

@click.group()
@click.option("--path", "-p", "klei_path", envvar="DST_KLEI_PATH",
              type=click.Path(exists=True, file_okay=False),
              help="Path to Klei DoNotStarveTogether directory.")
@click.version_option(__version__, "-V", "--version")
@click.pass_context
def cli(ctx, klei_path: Optional[str]):
    """dst - Don't Starve Together save file and mod manager.

    Manage DST save files, mod configurations, and server settings
    from the command line.

    \b
    Examples:
      dst save list
      dst mod list --cluster Cluster_3
      dst cluster config get GAMEPLAY max_players
    """
    ctx.ensure_object(dict)
    env = discover_environment(Path(klei_path) if klei_path else None)
    ctx.obj["env"] = env


# ── Save Commands ─────────────────────────────────────────────────────

@cli.group()
def save():
    """Browse and inspect save files."""


@save.command("list")
@click.option("--cluster", "-c", default=None, help="Cluster name (e.g., Cluster_3).")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.pass_context
def save_list(ctx, cluster: Optional[str], shard: Optional[str]):
    """List all save sessions and their metadata."""
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    s = _find_shard(c, shard)

    sessions = list_save_sessions(s.path)

    if not sessions:
        click.echo(f"No save sessions found in {c.name}/{s.name}.")
        return

    click.echo(f"\n{c.name}/{s.name} - {len(sessions)} save session(s):\n")
    click.echo(f"{'Session ID':<20} {'Summary':<50} {'Slots'}")
    click.echo("-" * 85)

    for session in sessions:
        summary = get_save_summary(session)
        click.echo(f"{session.session_id:<20} {summary:<50} {len(session.slots)}")


@save.command("info")
@click.argument("session_id", required=False)
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.pass_context
def save_info(ctx, session_id: Optional[str], cluster: Optional[str], shard: Optional[str]):
    """Show detailed information about a save session.

    SESSION_ID: The save session ID (hex string). If omitted, shows all sessions.
    """
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    s = _find_shard(c, shard)

    sessions = list_save_sessions(s.path)
    if not sessions:
        click.echo("No save sessions found.")
        return

    if session_id:
        # Find specific session
        sessions = [sess for sess in sessions if sess.session_id == session_id]
        if not sessions:
            click.echo(f"Session '{session_id}' not found.")
            return

    for session in sessions:
        click.echo(f"\n{'='*60}")
        click.echo(f"Session: {session.session_id}")
        click.echo(f"Path: {session.path}")
        click.echo(f"Summary: {get_save_summary(session)}")

        if session.metadata:
            meta = session.metadata
            click.echo(f"\n  Day: {meta.day}")
            click.echo(f"  Season: {meta.season} (day {int(meta.days_in_season)} of season)")
            click.echo(f"  Phase: {meta.phase}")

        click.echo(f"\n  Save slots: {len(session.slots)}")
        for slot in session.slots[-5:]:  # Show last 5
            size_mb = slot.size / (1024 * 1024)
            click.echo(f"    Slot {slot.slot_number:>10}: {size_mb:.1f} MB")


# ── Mod Commands ───────────────────────────────────────────────────────

@cli.group()
def mod():
    """Manage mod configurations."""


@mod.command("list")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.option("--enabled/--disabled", default=None, help="Filter by enabled state.")
@click.option("--search", default=None, help="Filter by workshop ID substring.")
@click.pass_context
def mod_list(ctx, cluster, shard, enabled, search):
    """List mods and their status."""
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    s = _find_shard(c, shard)

    if not s.mod_overrides_path:
        click.echo(f"No modoverrides.lua found in {c.name}/{s.name}.")
        return

    overrides = load_mod_overrides(s.mod_overrides_path)
    mods = list_mods(overrides)

    # Apply filters
    if enabled is not None:
        mods = [m for m in mods if m.enabled == enabled]
    if search:
        mods = [m for m in mods if search.lower() in m.workshop_id.lower()]

    if not mods:
        click.echo("No mods found.")
        return

    click.echo(f"\n{c.name}/{s.name} - {len(mods)} mod(s):\n")
    click.echo(f"{'Workshop ID':<30} {'Enabled':<10} {'Config Options'}")
    click.echo("-" * 70)

    for m in mods:
        status = click.style("[ON]", fg="green") if m.enabled else click.style("[OFF]", fg="red")
        opt_count = len(m.configuration_options)
        click.echo(f"{m.workshop_id:<30} {status:<10} {opt_count}")


@mod.command("info")
@click.argument("workshop_id")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.pass_context
def mod_info(ctx, workshop_id, cluster, shard):
    """Show detailed mod configuration.

    WORKSHOP_ID: The workshop mod ID (e.g., workshop-378160973).
    """
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    s = _find_shard(c, shard)

    if not s.mod_overrides_path:
        click.echo("No modoverrides.lua found.")
        return

    overrides = load_mod_overrides(s.mod_overrides_path)
    entry = get_mod(overrides, workshop_id)

    if not entry:
        click.echo(f"Mod '{workshop_id}' not found in {c.name}/{s.name}.")
        return

    status = "Enabled" if entry.enabled else "Disabled"
    click.echo(f"\nWorkshop ID: {entry.workshop_id}")
    click.echo(f"Status: {status}")
    click.echo(f"Config options: {len(entry.configuration_options)}")

    if entry.configuration_options:
        click.echo(f"\n{'Option':<40} {'Value'}")
        click.echo("-" * 70)
        for key, value in entry.configuration_options.items():
            click.echo(f"{key:<40} {value}")


@mod.command("enable")
@click.argument("workshop_id")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.option("--all-shards", is_flag=True, help="Apply to all shards in the cluster.")
@click.pass_context
def mod_enable(ctx, workshop_id, cluster, shard, all_shards):
    """Enable a mod.

    WORKSHOP_ID: The workshop mod ID to enable.
    """
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)

    if all_shards:
        targets = c.shards
    else:
        targets = [_find_shard(c, shard)]

    for s in targets:
        if not s.mod_overrides_path:
            click.echo(f"  {s.name}: No modoverrides.lua, skipping.")
            continue
        overrides = load_mod_overrides(s.mod_overrides_path)
        enable_mod(overrides, workshop_id)
        save_mod_overrides(overrides)
        click.echo(f"  {s.name}: Enabled {workshop_id}")


@mod.command("disable")
@click.argument("workshop_id")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.option("--all-shards", is_flag=True, help="Apply to all shards.")
@click.pass_context
def mod_disable(ctx, workshop_id, cluster, shard, all_shards):
    """Disable a mod.

    WORKSHOP_ID: The workshop mod ID to disable.
    """
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)

    if all_shards:
        targets = c.shards
    else:
        targets = [_find_shard(c, shard)]

    for s in targets:
        if not s.mod_overrides_path:
            click.echo(f"  {s.name}: No modoverrides.lua, skipping.")
            continue
        overrides = load_mod_overrides(s.mod_overrides_path)
        disable_mod(overrides, workshop_id)
        save_mod_overrides(overrides)
        click.echo(f"  {s.name}: Disabled {workshop_id}")


@mod.command("remove")
@click.argument("workshop_id")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.option("--all-shards", is_flag=True, help="Apply to all shards.")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation.")
@click.pass_context
def mod_remove(ctx, workshop_id, cluster, shard, all_shards, force):
    """Remove a mod from overrides.

    WORKSHOP_ID: The workshop mod ID to remove.
    """
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)

    if not force:
        if not click.confirm(f"Remove {workshop_id} from configuration?"):
            return

    if all_shards:
        targets = c.shards
    else:
        targets = [_find_shard(c, shard)]

    for s in targets:
        if not s.mod_overrides_path:
            continue
        overrides = load_mod_overrides(s.mod_overrides_path)
        if remove_mod(overrides, workshop_id):
            save_mod_overrides(overrides)
            click.echo(f"  {s.name}: Removed {workshop_id}")


@mod.group("config")
def mod_config():
    """Get or set mod configuration options."""


@mod_config.command("get")
@click.argument("workshop_id")
@click.argument("key")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.pass_context
def mod_config_get(ctx, workshop_id, key, cluster, shard):
    """Get a mod configuration option value."""
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    s = _find_shard(c, shard)

    if not s.mod_overrides_path:
        click.echo("No modoverrides.lua found.")
        return

    overrides = load_mod_overrides(s.mod_overrides_path)
    value = get_mod_config(overrides, workshop_id, key)
    if value is None:
        click.echo(f"Option '{key}' not found for {workshop_id}.")
    else:
        click.echo(f"{key} = {value}")


@mod_config.command("set")
@click.argument("workshop_id")
@click.argument("key")
@click.argument("value")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name (default: Master).")
@click.option("--all-shards", is_flag=True, help="Apply to all shards.")
@click.pass_context
def mod_config_set(ctx, workshop_id, key, value, cluster, shard, all_shards):
    """Set a mod configuration option value."""
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)

    if all_shards:
        targets = c.shards
    else:
        targets = [_find_shard(c, shard)]

    for s in targets:
        if not s.mod_overrides_path:
            click.echo(f"  {s.name}: No modoverrides.lua, skipping.")
            continue
        overrides = load_mod_overrides(s.mod_overrides_path)
        set_mod_config(overrides, workshop_id, key, value)
        save_mod_overrides(overrides)
        click.echo(f"  {s.name}: Set {key} = {value} for {workshop_id}")


@mod.command("sync")
@click.option("--from-cluster", "-f", "from_cluster", required=True, help="Source cluster name.")
@click.option("--to-cluster", "-t", "to_cluster", required=True, help="Target cluster name.")
@click.option("--from-shard", default="Master", help="Source shard (default: Master).")
@click.option("--to-shard", default="Master", help="Target shard (default: Master).")
@click.pass_context
def mod_sync(ctx, from_cluster, to_cluster, from_shard, to_shard):
    """Sync mod configuration from one cluster/shard to another."""
    env = ctx.obj["env"]
    src_cluster = _find_cluster(env, from_cluster)
    dst_cluster = _find_cluster(env, to_cluster)
    src_shard = _find_shard(src_cluster, from_shard)
    dst_shard = _find_shard(dst_cluster, to_shard)

    if not src_shard.mod_overrides_path:
        raise click.UsageError(f"Source {from_cluster}/{from_shard} has no modoverrides.lua.")
    if not dst_shard.mod_overrides_path:
        raise click.UsageError(f"Target {to_cluster}/{to_shard} has no modoverrides.lua.")

    src_overrides = load_mod_overrides(src_shard.mod_overrides_path)
    dst_overrides = load_mod_overrides(dst_shard.mod_overrides_path)

    sync_mods(src_overrides, dst_overrides)
    save_mod_overrides(dst_overrides)

    click.echo(f"Synced {len(src_overrides.mods)} mods from "
               f"{from_cluster}/{from_shard} to {to_cluster}/{to_shard}.")


# ── Cluster Commands ──────────────────────────────────────────────────

@cli.group()
def cluster():
    """Manage server cluster configurations."""


@cluster.command("list")
@click.pass_context
def cluster_list(ctx):
    """List all DST clusters."""
    env = ctx.obj["env"]

    if not env.clusters:
        click.echo("No clusters found.")
        return

    click.echo(f"\nDST Clusters (Klei: {env.klei_root}):\n")
    click.echo(f"{'Name':<25} {'Mode':<15} {'Players':<10} {'Shards'}")
    click.echo("-" * 80)

    for c in env.clusters:
        config = load_cluster_config(c.path)
        mode = config.gameplay.get("game_mode", "?")
        players = config.gameplay.get("max_players", "?")
        shard_names = ", ".join(s.name for s in c.shards)
        click.echo(f"{c.name:<25} {mode:<15} {players:<10} {shard_names}")


@cluster.command("info")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.pass_context
def cluster_info(ctx, cluster):
    """Show detailed cluster information."""
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    config = load_cluster_config(c.path)

    click.echo(f"\nCluster: {c.name}")
    click.echo(f"Path: {c.path}")

    click.echo(f"\n[GAMEPLAY]")
    for key, value in config.gameplay.items():
        click.echo(f"  {key} = {value}")

    click.echo(f"\n[NETWORK]")
    for key, value in config.network.items():
        if key == "cluster_password":
            value = "***"  # Mask password
        click.echo(f"  {key} = {value}")

    click.echo(f"\n[MISC]")
    for key, value in config.misc.items():
        click.echo(f"  {key} = {value}")

    click.echo(f"\n[SHARD]")
    for key, value in config.shard.items():
        click.echo(f"  {key} = {value}")

    click.echo(f"\nShards:")
    for s in c.shards:
        shard_config = load_shard_config(s.path)
        is_master = shard_config.shard.get("is_master", False)
        port = shard_config.network.get("server_port", "?")
        click.echo(f"  {s.name}: is_master={is_master}, port={port}")


@cluster.group("config")
def cluster_config():
    """Get or set cluster.ini options."""


@cluster_config.command("get")
@click.argument("section")
@click.argument("key")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.pass_context
def cluster_config_get(ctx, section, key, cluster):
    """Get a cluster.ini configuration value."""
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    config = load_cluster_config(c.path)

    value = get_cluster_option(config, section, key)
    if value is None:
        click.echo(f"Option '{key}' not found in [{section}].")
    else:
        click.echo(f"[{section}] {key} = {value}")


@cluster_config.command("set")
@click.argument("section")
@click.argument("key")
@click.argument("value")
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.pass_context
def cluster_config_set(ctx, section, key, value, cluster):
    """Set a cluster.ini configuration value."""
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    config = load_cluster_config(c.path)

    set_cluster_option(config, section, key, value)
    save_cluster_config(config, c.path)

    click.echo(f"Set [{section}] {key} = {value} in {c.name}/cluster.ini")


@cluster.group("shard")
def cluster_shard():
    """Manage shard server.ini configurations."""


@cluster_shard.command("config")
@click.argument("section")
@click.argument("key")
@click.argument("value", required=False)
@click.option("--cluster", "-c", default=None, help="Cluster name.")
@click.option("--shard", "-s", default=None, help="Shard name.")
@click.pass_context
def shard_config_cmd(ctx, section, key, value, cluster, shard):
    """Get or set a server.ini configuration value.

    If VALUE is omitted, the current value is shown.
    If VALUE is provided, the value is set.
    """
    env = ctx.obj["env"]
    c = _find_cluster(env, cluster)
    s = _find_shard(c, shard)

    if value is None:
        # Get
        config = load_shard_config(s.path)
        val = get_shard_option(config, section, key)
        if val is None:
            click.echo(f"Option '{key}' not found in [{section}].")
        else:
            click.echo(f"[{section}] {key} = {val}")
    else:
        # Set
        config = load_shard_config(s.path)
        set_shard_option(config, section, key, value)
        save_shard_config(config, s.path)
        click.echo(f"Set [{section}] {key} = {value} in {c.name}/{s.name}/server.ini")


# ── Environment Commands ──────────────────────────────────────────────

@cli.group()
def env():
    """DST environment information and settings."""


@env.command("info")
@click.pass_context
def env_info(ctx):
    """Show DST environment information."""
    environment = ctx.obj["env"]

    click.echo(f"\nDST Environment:")
    click.echo(f"  Klei Root: {environment.klei_root}")
    click.echo(f"  User ID: {environment.user_id or 'Not found'}")
    click.echo(f"  Client Config: {environment.client_config or 'Not found'}")
    click.echo(f"  Clusters: {len(environment.clusters)}")

    for c in environment.clusters:
        click.echo(f"    - {c.name}: {len(c.shards)} shard(s) "
                   f"({' ,'.join(s.name for s in c.shards)})")


# ── Entry Point ───────────────────────────────────────────────────────

def main():
    """Main entry point for the CLI."""
    try:
        cli()
    except click.UsageError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
