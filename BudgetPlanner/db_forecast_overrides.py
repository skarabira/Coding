"""
db_forecast_overrides.py - Manage user-modified plan values in section 5.1
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "budget_planner.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_forecast_override(project_id, task_id, mcr, month_year):
    """Get a specific override value, or None if it doesn't exist."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT override_amount FROM forecast_overrides
        WHERE project_id=? AND task_id=? AND mcr=? AND month_year=?
        """,
        (project_id, task_id, mcr, month_year),
    ).fetchone()
    conn.close()
    return float(row[0]) if row else None


def upsert_forecast_override(project_id, task_id, mcr, month_year, override_amount):
    """Create or update a forecast override."""
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO forecast_overrides (project_id, task_id, mcr, month_year, override_amount)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, task_id, mcr, month_year)
            DO UPDATE SET override_amount=?, modified_at=datetime('now')
            """,
            (project_id, task_id, mcr, month_year, override_amount, override_amount),
        )
        conn.commit()
    except Exception as e:
        print(f"Error upserting forecast override: {e}")
    conn.close()


def delete_forecast_override(project_id, task_id, mcr, month_year):
    """Delete a forecast override."""
    conn = get_conn()
    conn.execute(
        """
        DELETE FROM forecast_overrides
        WHERE project_id=? AND task_id=? AND mcr=? AND month_year=?
        """,
        (project_id, task_id, mcr, month_year),
    )
    conn.commit()
    conn.close()


def get_all_forecast_overrides(project_id):
    """Get all overrides for a project."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM forecast_overrides
        WHERE project_id=?
        ORDER BY mcr, task_id, month_year
        """,
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_forecast_overrides_for_mcr(project_id, mcr):
    """Get all overrides for a specific MCR in a project."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM forecast_overrides
        WHERE project_id=? AND mcr=?
        ORDER BY task_id, month_year
        """,
        (project_id, mcr),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_forecast_overrides_for_project(project_id):
    """Delete all overrides for a project (for cleanup/reset)."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM forecast_overrides WHERE project_id=?",
        (project_id,),
    )
    conn.commit()
    conn.close()
