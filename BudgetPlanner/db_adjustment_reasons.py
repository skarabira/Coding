"""
Persistence layer for Section 5.1 Forecast Adjustment Reasons.
Reasons are stored at Task level: (project_id, mcr, task_id, task_name_snapshot).
One active reason per unique (project_id, mcr, task_id) combination.
"""

from db import get_conn


# ── Read ───────────────────────────────────────────────────────────────────────

def get_all_reasons(project_id: int) -> list[dict]:
    """Return all active reasons for a project as a list of dicts."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, project_id, mcr, task_id, task_name_snapshot,
               reason_category, reason_text, status, created_at, modified_at
        FROM forecast_adjustment_reasons
        WHERE project_id = ? AND status = 'active'
        ORDER BY mcr, task_name_snapshot
        """,
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_reason(project_id: int, mcr: str, task_id: int) -> dict | None:
    """Return the active reason for a specific (project, mcr, task_id), or None."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, project_id, mcr, task_id, task_name_snapshot,
               reason_category, reason_text, status, created_at, modified_at
        FROM forecast_adjustment_reasons
        WHERE project_id = ? AND mcr = ? AND task_id = ? AND status = 'active'
        """,
        (project_id, mcr, int(task_id)),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_reason_map(project_id: int) -> dict:
    """
    Return dict keyed by (mcr, task_id) -> reason_text for fast lookup in UI.
    Only returns active reasons.
    """
    rows = get_all_reasons(project_id)
    return {(r["mcr"], r["task_id"]): r["reason_text"] for r in rows}


# ── Write ──────────────────────────────────────────────────────────────────────

def upsert_reason(
    project_id: int,
    mcr: str,
    task_id: int,
    task_name_snapshot: str,
    reason_text: str,
    reason_category: str = "",
) -> None:
    """
    Create or update the active reason for (project_id, mcr, task_id).
    Uses UPDATE-then-INSERT pattern for reliable upsert (no UNIQUE on NULL issues).
    """
    conn = get_conn()
    task_id = int(task_id)

    rows_updated = conn.execute(
        """
        UPDATE forecast_adjustment_reasons
        SET reason_text = ?,
            reason_category = ?,
            task_name_snapshot = ?,
            modified_at = datetime('now'),
            status = 'active'
        WHERE project_id = ? AND mcr = ? AND task_id = ?
        """,
        (reason_text, reason_category, task_name_snapshot, project_id, mcr, task_id),
    ).rowcount

    if rows_updated == 0:
        conn.execute(
            """
            INSERT INTO forecast_adjustment_reasons
                (project_id, mcr, task_id, task_name_snapshot,
                 reason_category, reason_text, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (project_id, mcr, task_id, task_name_snapshot, reason_category, reason_text),
        )

    conn.commit()
    conn.close()


def delete_reason(project_id: int, mcr: str, task_id: int) -> None:
    """Hard-delete the reason for (project_id, mcr, task_id)."""
    conn = get_conn()
    conn.execute(
        """
        DELETE FROM forecast_adjustment_reasons
        WHERE project_id = ? AND mcr = ? AND task_id = ?
        """,
        (project_id, mcr, int(task_id)),
    )
    conn.commit()
    conn.close()
