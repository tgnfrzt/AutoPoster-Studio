import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from core.config import settings

def get_db():
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                source_config TEXT NOT NULL,
                target_platforms TEXT NOT NULL,
                processor_configs TEXT NOT NULL,
                publisher_configs TEXT NOT NULL,
                schedule_mode TEXT DEFAULT 'auto_smart',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT,
                source_url TEXT,
                canonical_url TEXT,
                status TEXT NOT NULL,
                target_platform TEXT NOT NULL,
                current_step TEXT NOT NULL,
                step_progress REAL DEFAULT 0.0,
                series_id TEXT,
                series_order INTEGER DEFAULT 0,
                error_message TEXT,
                error_code TEXT,
                raw_media_path TEXT,
                processed_media_path TEXT,
                metadata_json TEXT,
                receipt_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id)
            );
        """)

        # Migration check: Ensure series_id and series_order exist
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(jobs);")
        columns = [row[1] for row in cursor.fetchall()]
        if "series_id" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN series_id TEXT;")
        if "series_order" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN series_order INTEGER DEFAULT 0;")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                account_name TEXT NOT NULL,
                credentials_json TEXT NOT NULL,
                is_valid INTEGER DEFAULT 1,
                last_verified TEXT,
                created_at TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS link_cache (
                canonical_url TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_status TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS post_history (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                platform TEXT NOT NULL,
                post_url TEXT,
                published_at TEXT NOT NULL
            );
        """)
    conn.close()

# Auto initialize database on module import
init_db()
