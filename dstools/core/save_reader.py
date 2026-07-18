"""Save file reader for DST save metadata.

Reads .meta files and save session directories to extract world information
(day count, season, clock phase, etc.) without needing to parse binary saves.
"""

from pathlib import Path

from dstools.core.lua_parser import parse_lua_file
from dstools.models import SaveMetadata, SaveSession, SaveSlot, SaveSource


def list_save_sessions(shard_path: Path) -> list[SaveSession]:
    """List all save sessions under a shard's save directory.

    A save session is a directory under save/session/ containing
    numbered save slot files (e.g., 0000000488) and their .meta files.

    Args:
        shard_path: Path to a shard directory (e.g., Cluster_3/Master/).

    Returns:
        List of SaveSession objects.
    """
    sessions = []
    session_dir = shard_path / "save" / "session"

    if not session_dir.exists():
        return sessions

    for entry in sorted(session_dir.iterdir()):
        if not entry.is_dir():
            continue

        # Skip non-session directories
        # Session IDs are typically 16-character hex strings
        session = _build_session(entry)
        if session.slots:  # Only include sessions with actual save data
            try:
                session.metadata = read_session_metadata(session)
            except Exception:
                pass  # Metadata is optional
            sessions.append(session)

    return sessions


def _read_meta_file(meta_path: Path) -> SaveMetadata | None:
    """Read and parse a .meta file to extract save metadata.

    Args:
        meta_path: Path to a .meta file.

    Returns:
        SaveMetadata or None if file doesn't exist or can't be parsed.
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
    """Build a SaveSession from a session directory path."""
    session = SaveSession(
        session_id=session_path.name,
        path=session_path,
        source=SaveSource.SERVER,
    )

    # Find save slot files (numeric names with .meta counterparts)
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
    """Read metadata from the newest .meta file in a session.

    Args:
        session: The SaveSession to read metadata from.

    Returns:
        SaveMetadata or None if no .meta files found.
    """
    meta_files = [s.meta_file for s in session.slots if s.meta_file and s.meta_file.exists()]
    if not meta_files:
        return None

    return _read_meta_file(meta_files[-1])


def get_save_summary(session: SaveSession) -> str:
    """Generate a human-readable summary of a save session.

    Args:
        session: The SaveSession to summarize.

    Returns:
        Human-readable string like "第417天, 夏季第12天, 白天"
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

    # Add slot count
    parts.append(f"[{len(session.slots)}个存档槽]")

    return ", ".join(parts)


def get_latest_save_size(session: SaveSession) -> int:
    """Get the total size of all save files in the session."""
    return sum(slot.size for slot in session.slots)
