#!/usr/bin/env python3
"""
Import offline-stub state.json → PostgreSQL.

Merges profiles, generations (history), and profile samples into the live
database without touching rows that already exist (safe to re-run).

Usage:
  python3 scripts/import-stub-to-pg.py
  python3 scripts/import-stub-to-pg.py --pg-url postgresql://voicetuner:voicetuner_dev@127.0.0.1:5433/voicetuner
"""
import argparse, json, os, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
STATE_FILE = ROOT / "data" / "offline-stub" / "state.json"
SAMPLES_DIR = ROOT / "data" / "offline-stub" / "samples"
GENERATIONS_DIR = ROOT / "data" / "generations"

DEFAULT_PG_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://voicetuner:voicetuner_dev@127.0.0.1:5433/voicetuner",
)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg-url", default=DEFAULT_PG_URL)
    args = ap.parse_args()

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("Installing psycopg2-binary…")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"])
        import psycopg2
        import psycopg2.extras

    if not STATE_FILE.exists():
        sys.exit(f"state.json not found at {STATE_FILE}")

    state = json.loads(STATE_FILE.read_text())
    profiles  = state.get("profiles", [])
    history   = state.get("history",  [])
    stories   = state.get("stories",  [])

    print(f"Connecting to {args.pg_url} …")
    conn = psycopg2.connect(args.pg_url)
    conn.autocommit = False
    cur  = conn.cursor()

    # ── Build stub-id → db-id mapping for profiles (match by name) ───────────
    cur.execute("SELECT id, name FROM profiles")
    db_profiles = {row[1]: row[0] for row in cur.fetchall()}   # name → db_id
    # stub_id → db_id  (same id when already matching, name-mapped otherwise)
    profile_id_map = {}
    for p in profiles:
        db_id = db_profiles.get(p["name"])
        if db_id:
            profile_id_map[p["id"]] = db_id   # map stub id → existing db id
        else:
            profile_id_map[p["id"]] = p["id"] # will be inserted fresh

    # ── Profiles ─────────────────────────────────────────────────────────────
    import hashlib
    inserted_profiles = 0
    for p in profiles:
        if p["name"] in db_profiles:
            continue   # already exists by name
        cur.execute(
            """
            INSERT INTO profiles
              (id, name, description, language, voice_type, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                p["id"],
                p.get("name", ""),
                p.get("description") or "",
                p.get("language", "en"),
                p.get("voice_type", "cloned"),
                p.get("created_at") or now_iso(),
                p.get("updated_at") or now_iso(),
            ),
        )
        inserted_profiles += 1
        db_profiles[p["name"]] = p["id"]
        profile_id_map[p["id"]] = p["id"]
        print(f"  + profile  {p['name']} ({p['id'][:8]})")

        # ── Profile samples ───────────────────────────────────────────────
        pid = p["id"]
        sample_dir = SAMPLES_DIR / pid
        if sample_dir.is_dir():
            for wav_file in sorted(sample_dir.glob("*.wav")):
                meta_file = wav_file.with_suffix(".json")
                ref_text  = ""
                if meta_file.exists():
                    try:
                        ref_text = json.loads(meta_file.read_text()).get("reference_text", "")
                    except Exception:
                        pass
                audio_data = wav_file.read_bytes()
                rel_path   = f"offline-stub/samples/{pid}/{wav_file.name}"
                sid = hashlib.sha256(f"{pid}/{wav_file.name}".encode()).hexdigest()[:32]
                cur.execute(
                    """
                    INSERT INTO profile_samples (id, profile_id, audio_path, audio_data, reference_text)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (sid, pid, rel_path, psycopg2.Binary(audio_data), ref_text),
                )
                print(f"    + sample  {wav_file.name}")

    # ── Generations / history ─────────────────────────────────────────────────
    inserted_gens = 0
    for h in history:
        cur.execute("SELECT id FROM generations WHERE id = %s", (h["id"],))
        if cur.fetchone():
            continue   # already exists
        # Resolve profile_id via name-based mapping
        db_profile_id = profile_id_map.get(h.get("profile_id", ""), h.get("profile_id", ""))
        # If profile_id still not in DB (orphaned gen), skip
        cur.execute("SELECT id FROM profiles WHERE id = %s", (db_profile_id,))
        if not cur.fetchone():
            print(f"  ! skipping generation {h['id'][:8]} — profile not found in DB")
            continue
        audio_path = f"generations/{h['id']}.wav"
        cur.execute(
            """
            INSERT INTO generations
              (id, profile_id, text, language, audio_path, duration,
               seed, instruct, engine, model_size, status, source, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                h["id"],
                db_profile_id,
                h.get("text", ""),
                h.get("language", "en"),
                audio_path,
                h.get("duration"),
                h.get("seed"),
                h.get("instruct"),
                h.get("engine", "qwen"),
                h.get("model_size") or "1.7B",
                "completed",
                h.get("source", "manual"),
                h.get("created_at") or now_iso(),
            ),
        )
        inserted_gens += 1
        dur = h.get("duration", 0) or 0
        hh, r = divmod(int(dur), 3600); mm, ss = divmod(r, 60)
        print(f"  + generation  {h.get('profile_name','?'):15} {hh:02d}:{mm:02d}:{ss:02d}  ({h['id'][:8]})")

    # ── Stories ───────────────────────────────────────────────────────────────
    inserted_stories = 0
    for s in stories:
        cur.execute("SELECT id FROM stories WHERE id = %s", (s["id"],))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO stories (id, name, description, created_at, updated_at) VALUES (%s,%s,%s,%s,%s)",
            (s["id"], s.get("name","Untitled"), s.get("description"),
             s.get("created_at") or now_iso(), s.get("updated_at") or now_iso()),
        )
        for item in s.get("items", []):
            cur.execute("SELECT id FROM story_items WHERE id = %s", (item["id"],))
            if not cur.fetchone():
                cur.execute(
                    """INSERT INTO story_items
                       (id, story_id, generation_id, start_time_ms, track, trim_start_ms, trim_end_ms)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (item["id"], s["id"], item["generation_id"],
                     item.get("start_time_ms", 0), item.get("track", 0),
                     item.get("trim_start_ms", 0), item.get("trim_end_ms", 0)),
                )
        inserted_stories += 1
        print(f"  + story  {s['name']} ({s['id'][:8]})")

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone. Inserted: {inserted_profiles} profiles, {inserted_gens} generations, {inserted_stories} stories.")
    print("Run `scripts/backup-db.sh` to snapshot the updated database.")

if __name__ == "__main__":
    main()
