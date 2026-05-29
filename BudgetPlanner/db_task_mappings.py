"""
Task name mapping functions.
Mappings are keyed by (project_id, actual_task_name, actual_mcr) where actual_mcr
can be an empty string '' to create a catch-all mapping for a task name regardless of MCR.

Lookup priority:
  1. Exact (task_name + MCR) match
  2. Catch-all (task_name, mcr='') match
  3. None → no mapping found
"""

import db as db_module


def get_task_mapping(project_id, actual_task_name, actual_mcr=""):
    """Get the mapped plan task name. Checks exact (task+MCR) first, then catch-all (task only)."""
    actual_task_name = str(actual_task_name or "").strip()
    actual_mcr = str(actual_mcr or "").strip()
    conn = db_module.get_conn()
    # 1. Exact match (task + MCR)
    if actual_mcr:
        result = conn.execute(
            "SELECT plan_task_name FROM task_name_mappings WHERE project_id=? AND actual_task_name=? AND actual_mcr=?",
            (project_id, actual_task_name, actual_mcr),
        ).fetchone()
        if result:
            conn.close()
            return result[0]
    # 2. Catch-all match (task name only, mcr='')
    result = conn.execute(
        "SELECT plan_task_name FROM task_name_mappings WHERE project_id=? AND actual_task_name=? AND actual_mcr=''",
        (project_id, actual_task_name),
    ).fetchone()
    conn.close()
    return result[0] if result else None


def upsert_task_mapping(project_id, actual_task_name, plan_task_name, actual_mcr=""):
    """Create or update a mapping. Leave actual_mcr blank for a catch-all (task-name-only) mapping."""
    actual_task_name = str(actual_task_name or "").strip()
    actual_mcr = str(actual_mcr or "").strip()
    plan_task_name = str(plan_task_name or "").strip()
    if not actual_task_name or not plan_task_name:
        return False
    conn = db_module.get_conn()
    conn.execute(
        """
        INSERT INTO task_name_mappings (project_id, actual_task_name, actual_mcr, plan_task_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id, actual_task_name, actual_mcr)
        DO UPDATE SET plan_task_name=excluded.plan_task_name
        """,
        (project_id, actual_task_name, actual_mcr, plan_task_name),
    )
    conn.commit()
    conn.close()
    return True


def delete_task_mapping(project_id, actual_task_name, actual_mcr=""):
    """Delete a specific mapping."""
    actual_mcr = str(actual_mcr or "").strip()
    conn = db_module.get_conn()
    conn.execute(
        "DELETE FROM task_name_mappings WHERE project_id=? AND actual_task_name=? AND actual_mcr=?",
        (project_id, actual_task_name, actual_mcr),
    )
    conn.commit()
    conn.close()


def get_all_task_mappings(project_id):
    """Get all task name mappings for a project, ordered by task name then MCR."""
    conn = db_module.get_conn()
    rows = conn.execute(
        """SELECT actual_task_name, actual_mcr, plan_task_name
           FROM task_name_mappings WHERE project_id=?
           ORDER BY actual_task_name, actual_mcr""",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_distinct_actual_mcrs(project_id):
    """Return sorted list of distinct project_no values appearing in actuals for this project."""
    import re
    conn = db_module.get_conn()
    rows = conn.execute(
        "SELECT DISTINCT project_no FROM actuals WHERE project_id=? AND TRIM(COALESCE(project_no,''))!='' ORDER BY project_no",
        (project_id,),
    ).fetchall()
    conn.close()
    # Canonicalize to MCR key (e.g. BM-00110021_004)
    def _canon(raw):
        m = re.search(r"([A-Za-z]{2,}-\d+_\d+)", str(raw or ""))
        return m.group(1) if m else str(raw or "").strip()
    return sorted({_canon(r["project_no"]) for r in rows if r["project_no"]})


def get_unmapped_actuals_tasks(project_id):
    """
    Get all distinct (task_name, MCR) combinations from actuals that:
    - have no exact (task+MCR) mapping
    - have no catch-all (task only) mapping
    - don't directly match any plan task name
    Returns list of dicts: actual_task_name, actual_mcr, record_count, total_amount
    """
    import re
    conn = db_module.get_conn()
    rows = conn.execute(
        """
        SELECT
            a.task_name     AS actual_task_name,
            a.project_no    AS raw_mcr,
            COUNT(*)        AS record_count,
            SUM(a.amount)   AS total_amount
        FROM actuals a
        WHERE a.project_id = ?
          AND TRIM(COALESCE(a.task_name, '')) != ''
          AND LOWER(TRIM(COALESCE(a.task_name, ''))) NOT IN (
              SELECT LOWER(TRIM(COALESCE(pt.task_name, ''))) FROM plan_tasks pt WHERE pt.project_id = ?
          )
        GROUP BY a.task_name, a.project_no
        ORDER BY SUM(a.amount) DESC
        """,
        (project_id, project_id),
    ).fetchall()
    conn.close()

    # Filter out already-mapped combinations (both exact and catch-all)
    def _canon(raw):
        m = re.search(r"([A-Za-z]{2,}-\d+_\d+)", str(raw or ""))
        return m.group(1) if m else str(raw or "").strip()

    all_mappings = get_all_task_mappings(project_id)
    mapped_exact = {(m["actual_task_name"], m["actual_mcr"]) for m in all_mappings if m["actual_mcr"]}
    mapped_catchall = {m["actual_task_name"] for m in all_mappings if not m["actual_mcr"]}

    result = []
    for r in rows:
        _task = r["actual_task_name"]
        _mcr = _canon(r["raw_mcr"])
        if _task in mapped_catchall:
            continue
        if (_task, _mcr) in mapped_exact:
            continue
        result.append({
            "actual_task_name": _task,
            "actual_mcr": _mcr,
            "record_count": r["record_count"],
            "total_amount": float(r["total_amount"] or 0.0),
        })
    return result


def apply_task_mapping_to_actuals(project_id, actual_task_name, new_task_name):
    """Directly rename a task in actuals records (destructive alternative to mapping table)."""
    conn = db_module.get_conn()
    conn.execute(
        "UPDATE actuals SET task_name=? WHERE project_id=? AND task_name=?",
        (new_task_name, project_id, actual_task_name),
    )
    updated_count = conn.total_changes
    conn.commit()
    conn.close()
    return updated_count
