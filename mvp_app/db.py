from __future__ import annotations
import sqlite3
from contextlib import closing
from config import DB_PATH, UPLOAD_DIR
import os

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def migrate() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with closing(connect()) as conn, conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY,
            condition TEXT NOT NULL,
            created INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS results(
            task_id TEXT PRIMARY KEY,
            json TEXT NOT NULL,
            updated INTEGER NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS submissions(
            task_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL CHECK(mode IN ('file','text')),
            text TEXT,
            file_path TEXT,
            file_name TEXT,
            uploaded_at INTEGER NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS teacher_reviews(
            task_id TEXT PRIMARY KEY,
            json TEXT NOT NULL,
            total INTEGER NOT NULL,
            updated INTEGER NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS review_jobs(
            submission_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            external_id TEXT,
            result_json TEXT,
            created INTEGER NOT NULL,
            updated INTEGER NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        """)
