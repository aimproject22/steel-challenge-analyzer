# db_utils.py
# -*- coding: utf-8 -*-

import json
import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd

from feature_engineering import extract_features_from_logs


DB_PATH = Path("steel_challenge.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploaded_at TEXT,
            uploader TEXT,
            file_name TEXT,
            score REAL,
            cost_per_tonne REAL,
            steel_grade TEXT,
            data_json TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            log_no INTEGER,
            time TEXT,
            event TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        )
    """)

    conn.commit()
    conn.close()


def save_to_db(uploader, file_name, data, logs):
    features = extract_features_from_logs(logs)

    merged_data = {}
    merged_data.update(data)
    merged_data.update(features)

    score = data.get("Run Information > Score")
    cost_per_tonne = data.get("Cost Breakdown > Cost Per Tonne")
    steel_grade = data.get("Simulation Settings > Steel Grade")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO runs (
            uploaded_at,
            uploader,
            file_name,
            score,
            cost_per_tonne,
            steel_grade,
            data_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        uploader,
        file_name,
        score,
        cost_per_tonne,
        steel_grade,
        json.dumps(merged_data, ensure_ascii=False)
    ))

    run_id = cur.lastrowid

    for log in logs:
        cur.execute("""
            INSERT INTO logs (
                run_id,
                log_no,
                time,
                event
            )
            VALUES (?, ?, ?, ?)
        """, (
            run_id,
            log["log_no"],
            log["time"],
            log["event"]
        ))

    conn.commit()
    conn.close()


def load_runs_df():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC", conn)
    conn.close()

    if df.empty:
        return df

    rows = []

    for _, row in df.iterrows():
        data = json.loads(row["data_json"])

        new_row = {
            "Run ID": row["id"],
            "Uploaded At": row["uploaded_at"],
            "Uploader": row["uploader"],
            "File Name": row["file_name"],
            "Score": row["score"],
            "Cost Per Tonne": row["cost_per_tonne"],
            "Steel Grade": row["steel_grade"],
        }

        new_row.update(data)
        rows.append(new_row)

    return pd.DataFrame(rows)


def load_logs_df():
    conn = sqlite3.connect(DB_PATH)

    query = """
        SELECT
            runs.id AS run_id,
            runs.uploaded_at,
            runs.uploader,
            runs.file_name,
            logs.log_no,
            logs.time,
            logs.event
        FROM logs
        JOIN runs ON logs.run_id = runs.id
        ORDER BY runs.id DESC, logs.log_no ASC
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    return df


def reset_database():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM logs")
    cur.execute("DELETE FROM runs")
    conn.commit()
    conn.close()