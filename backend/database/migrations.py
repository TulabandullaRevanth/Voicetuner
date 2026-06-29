"""Column-level migrations for the VoiceTuner PostgreSQL database.

Each helper checks column/table existence before acting (idempotent) and
logs a short message when it does real work. Runs in <100 ms on startup.

Adding a new migration:
    1. Append a new ``_migrate_*`` helper at the bottom of this file.
    2. Call it from ``run_migrations()`` in the appropriate spot.
    3. The helper must be idempotent (check existence before ALTER).
"""

import json
import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def run_migrations(engine) -> None:
    """Run all schema migrations.  Safe to call on every startup."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    _migrate_story_items(engine, inspector, tables)
    _migrate_profiles(engine, inspector, tables)
    _migrate_generations(engine, inspector, tables)
    _migrate_effect_presets(engine, inspector, tables)
    _migrate_generation_versions(engine, inspector, tables)
    _migrate_mcp_bindings(engine, inspector, tables)
    _migrate_language_codes(engine, inspector, tables)
    _migrate_speaker_id(engine, inspector, tables)
    _normalize_storage_paths(engine, tables)
    _migrate_audio_data(engine, inspector, tables)


# -- helpers ---------------------------------------------------------------

def _get_columns(inspector, table: str) -> set[str]:
    return {col["name"] for col in inspector.get_columns(table)}


def _add_column(engine, table: str, column_sql: str, label: str) -> None:
    """Add a column if it doesn't already exist."""
    with engine.connect() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_sql}"))
        conn.commit()
    logger.info("Added %s column to %s", label, table)


# -- per-table migrations --------------------------------------------------

def _migrate_story_items(engine, inspector, tables: set[str]) -> None:
    if "story_items" not in tables:
        return

    columns = _get_columns(inspector, "story_items")

    # Replace position-based ordering with absolute timecodes
    if "position" in columns:
        logger.info("Migrating story_items: removing position column, using start_time_ms")
        with engine.connect() as conn:
            if "start_time_ms" not in columns:
                conn.execute(text(
                    "ALTER TABLE story_items ADD COLUMN start_time_ms INTEGER DEFAULT 0"
                ))
                result = conn.execute(text("""
                    SELECT si.id, si.story_id, si.position, g.duration
                    FROM story_items si
                    JOIN generations g ON si.generation_id = g.id
                    ORDER BY si.story_id, si.position
                """))
                current_story_id = None
                current_time_ms = 0
                for item_id, story_id, _position, duration in result.fetchall():
                    if story_id != current_story_id:
                        current_story_id = story_id
                        current_time_ms = 0
                    conn.execute(
                        text("UPDATE story_items SET start_time_ms = :time WHERE id = :id"),
                        {"time": current_time_ms, "id": item_id},
                    )
                    current_time_ms += int((duration or 0) * 1000) + 200
                conn.commit()

            conn.execute(text("ALTER TABLE story_items DROP COLUMN IF EXISTS position"))
            conn.commit()

        columns = _get_columns(inspector, "story_items")

    if "track" not in columns:
        _add_column(engine, "story_items", "track INTEGER NOT NULL DEFAULT 0", "track")
    # Re-read so subsequent checks see new columns
    columns = _get_columns(inspector, "story_items")
    if "trim_start_ms" not in columns:
        _add_column(engine, "story_items", "trim_start_ms INTEGER NOT NULL DEFAULT 0", "trim_start_ms")
    if "trim_end_ms" not in columns:
        _add_column(engine, "story_items", "trim_end_ms INTEGER NOT NULL DEFAULT 0", "trim_end_ms")
    if "version_id" not in columns:
        _add_column(engine, "story_items", "version_id VARCHAR", "version_id")
    if "volume" not in columns:
        _add_column(engine, "story_items", "volume FLOAT NOT NULL DEFAULT 1.0", "volume")


def _migrate_profiles(engine, inspector, tables: set[str]) -> None:
    if "profiles" not in tables:
        return
    columns = _get_columns(inspector, "profiles")
    if "avatar_path" not in columns:
        _add_column(engine, "profiles", "avatar_path VARCHAR", "avatar_path")
    if "effects_chain" not in columns:
        _add_column(engine, "profiles", "effects_chain TEXT", "effects_chain")
    # Voice type system — v0.3.x
    if "voice_type" not in columns:
        _add_column(engine, "profiles", "voice_type VARCHAR DEFAULT 'cloned'", "voice_type")
    if "preset_engine" not in columns:
        _add_column(engine, "profiles", "preset_engine VARCHAR", "preset_engine")
    if "preset_voice_id" not in columns:
        _add_column(engine, "profiles", "preset_voice_id VARCHAR", "preset_voice_id")
    if "design_prompt" not in columns:
        _add_column(engine, "profiles", "design_prompt TEXT", "design_prompt")
    if "default_engine" not in columns:
        _add_column(engine, "profiles", "default_engine VARCHAR", "default_engine")
    if "personality" not in columns:
        _add_column(engine, "profiles", "personality TEXT", "personality")


def _migrate_generations(engine, inspector, tables: set[str]) -> None:
    if "generations" not in tables:
        return
    columns = _get_columns(inspector, "generations")
    if "status" not in columns:
        _add_column(engine, "generations", "status VARCHAR DEFAULT 'completed'", "status")
    if "error" not in columns:
        _add_column(engine, "generations", "error TEXT", "error")
    if "engine" not in columns:
        _add_column(engine, "generations", "engine VARCHAR DEFAULT 'qwen'", "engine")
    # Re-read after engine column (variable name shadows outer scope in old code)
    columns = _get_columns(inspector, "generations")
    if "model_size" not in columns:
        _add_column(engine, "generations", "model_size VARCHAR", "model_size")
    if "is_favorited" not in columns:
        _add_column(engine, "generations", "is_favorited BOOLEAN DEFAULT 0", "is_favorited")
    if "source" not in columns:
        _add_column(
            engine,
            "generations",
            "source VARCHAR NOT NULL DEFAULT 'manual'",
            "source",
        )


def _migrate_effect_presets(engine, inspector, tables: set[str]) -> None:
    if "effect_presets" not in tables:
        return
    columns = _get_columns(inspector, "effect_presets")
    if "sort_order" not in columns:
        _add_column(engine, "effect_presets", "sort_order INTEGER DEFAULT 100", "sort_order")


def _migrate_generation_versions(engine, inspector, tables: set[str]) -> None:
    if "generation_versions" not in tables:
        return
    columns = _get_columns(inspector, "generation_versions")
    if "source_version_id" not in columns:
        _add_column(engine, "generation_versions", "source_version_id VARCHAR", "source_version_id")


def _migrate_mcp_bindings(engine, inspector, tables: set[str]) -> None:
    """Drop the legacy ``default_intent`` column and add ``default_personality``.

    The intent tri-state (respond / rewrite / compose) has been collapsed
    to a boolean: when true, ``voicetuner.speak`` rewrites input through the
    profile's personality LLM before TTS.
    """
    if "mcp_client_bindings" not in tables:
        return
    columns = _get_columns(inspector, "mcp_client_bindings")
    if "default_personality" not in columns:
        _add_column(
            engine,
            "mcp_client_bindings",
            "default_personality BOOLEAN NOT NULL DEFAULT 0",
            "default_personality",
        )
    if "default_intent" in columns:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE mcp_client_bindings DROP COLUMN IF EXISTS default_intent"))
            conn.commit()
        logger.info("Dropped legacy default_intent column from mcp_client_bindings")


def _migrate_language_codes(engine, inspector, tables: set[str]) -> None:
    """Coerce stored language codes to the supported set (en/hi/te).

    VoiceTuner restricts languages to ``SUPPORTED_LANGUAGES``. A legacy row
    holding a now-unsupported code would render as a broken option in the UI.
    Maps profiles and generations to 'en'. Idempotent.
    """
    from ..languages import SUPPORTED_LANGUAGES

    safe = [c for c in SUPPORTED_LANGUAGES if c.isalpha() and len(c) <= 5]
    if not safe:
        return
    supported_csv = ",".join(f"'{c}'" for c in safe)

    total = 0
    with engine.connect() as conn:
        for table in ("profiles", "generations"):
            if table not in tables:
                continue
            res = conn.execute(text(
                f"UPDATE {table} SET language = 'en' "
                f"WHERE language IS NOT NULL AND language NOT IN ({supported_csv})"
            ))
            total += res.rowcount or 0
        if total > 0:
            conn.commit()
            logger.info("Coerced %d row(s) to supported languages %s", total, safe)


def _migrate_speaker_id(engine, inspector, tables: set[str]) -> None:
    """Add speaker-identification columns (idempotent)."""
    if "profiles" in tables:
        cols = _get_columns(inspector, "profiles")
        if "speaker_embedding" not in cols:
            _add_column(engine, "profiles", "speaker_embedding TEXT", "speaker_embedding")

    if "captures" in tables:
        cols = _get_columns(inspector, "captures")
        if "identified_profile_id" not in cols:
            _add_column(engine, "captures", "identified_profile_id VARCHAR", "identified_profile_id")
        if "identified_profile_name" not in cols:
            _add_column(engine, "captures", "identified_profile_name VARCHAR", "identified_profile_name")
        if "speaker_confidence" not in cols:
            _add_column(engine, "captures", "speaker_confidence FLOAT", "speaker_confidence")


def _normalize_storage_paths(engine, tables: set[str]) -> None:
    """Normalize stored file paths to be relative to the configured data dir."""
    from pathlib import Path

    from ..config import get_data_dir, to_storage_path, resolve_storage_path

    data_dir = get_data_dir()

    path_columns = [
        ("generations", "audio_path"),
        ("generation_versions", "audio_path"),
        ("profile_samples", "audio_path"),
        ("profiles", "avatar_path"),
    ]

    total_fixed = 0
    with engine.connect() as conn:
        for table, column in path_columns:
            if table not in tables:
                continue
            rows = conn.execute(
                text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")
            ).fetchall()
            for row_id, path_val in rows:
                if not path_val:
                    continue
                p = Path(path_val)
                resolved = resolve_storage_path(p)
                if resolved is None:
                    continue

                normalized = to_storage_path(resolved)

                if normalized != path_val:
                    conn.execute(
                        text(f"UPDATE {table} SET {column} = :path WHERE id = :id"),
                        {"path": normalized, "id": row_id},
                    )
                    total_fixed += 1
        if total_fixed > 0:
            conn.commit()
            logger.info("Normalized %d stored file paths", total_fixed)


def _migrate_audio_data(engine, inspector, tables: set[str]) -> None:
    """Add audio_data BYTEA columns to profile_samples and captures.

    These columns store the raw WAV bytes so audio survives even if the
    ./data directory is lost (Docker volume wipe, new machine, etc.).
    """
    for table in ("profile_samples", "captures"):
        if table not in tables:
            continue
        cols = _get_columns(inspector, table)
        if "audio_data" not in cols:
            _add_column(engine, table, "audio_data BYTEA", "audio_data")
