from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import (
    BiometricValidationResult,
    DemographicValidationResult,
    ManualDemographicInput,
)


DB_PATH = Path(__file__).resolve().parents[1] / "validex.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS demographic_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT,
                last_name TEXT,
                date_of_birth TEXT,
                age TEXT,
                phone TEXT,
                email TEXT,
                validation_score REAL NOT NULL,
                score_band TEXT NOT NULL,
                issues_json TEXT NOT NULL,
                duplicate_flag INTEGER NOT NULL DEFAULT 0,
                duplicate_match_json TEXT,
                source_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS biometric_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                modality TEXT NOT NULL,
                filename TEXT NOT NULL,
                score REAL NOT NULL,
                status TEXT NOT NULL,
                issues_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                raw_output_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def insert_demographic_record(
    payload: ManualDemographicInput,
    result: DemographicValidationResult,
    source_type: str,
) -> int:
    init_database()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO demographic_records (
                first_name,
                last_name,
                date_of_birth,
                age,
                phone,
                email,
                validation_score,
                score_band,
                issues_json,
                duplicate_flag,
                duplicate_match_json,
                source_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.first_name,
                payload.last_name,
                payload.date_of_birth,
                payload.age,
                payload.phone,
                payload.email,
                round(result.validation_score, 2),
                result.score_band,
                json.dumps([field.to_dict() for field in result.fields]),
                1 if result.duplicate_match else 0,
                json.dumps(result.duplicate_match.to_dict())
                if result.duplicate_match
                else None,
                source_type,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def fetch_demographic_records() -> list[dict[str, Any]]:
    init_database()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                first_name,
                last_name,
                date_of_birth,
                age,
                phone,
                email,
                validation_score,
                score_band,
                duplicate_flag,
                duplicate_match_json,
                source_type,
                created_at
            FROM demographic_records
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def insert_biometric_record(result: BiometricValidationResult) -> None:
    init_database()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO biometric_records (
                modality,
                filename,
                score,
                status,
                issues_json,
                metrics_json,
                raw_output_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.modality,
                result.source_filename,
                round(result.overall_score, 2),
                result.status,
                json.dumps(result.issue_list),
                json.dumps(result.metrics),
                json.dumps(result.raw_output),
            ),
        )
        conn.commit()
