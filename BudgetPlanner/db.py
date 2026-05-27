"""
db.py - Database layer for Budget Planner
All SQLite operations live here.
"""

import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), "budget_planner.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            start_date  TEXT,
            end_date    TEXT,
            status      TEXT DEFAULT 'Active',
            created_at  TEXT DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS budget_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            category    TEXT NOT NULL,
            description TEXT,
            planned     REAL DEFAULT 0,
            month       TEXT,           -- YYYY-MM or NULL for lump sum
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS actuals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            category    TEXT NOT NULL,
            description TEXT,
            amount      REAL DEFAULT 0,
            date        TEXT,
            source      TEXT DEFAULT 'Manual',  -- 'Manual' or 'MCR Import'
            task_name   TEXT,
            employee_name TEXT,
            resource_group TEXT,
            project_no  TEXT,
            import_file TEXT,
            wbs_number  TEXT,
            financial_document TEXT,
            financial_document_posting_date TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS import_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id   INTEGER NOT NULL,
            import_type  TEXT NOT NULL,
            file_name    TEXT,
            row_count    INTEGER DEFAULT 0,
            imported_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS forecasts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            category    TEXT NOT NULL,
            etc         REAL DEFAULT 0,   -- Estimate to Complete
            note        TEXT,
            updated_at  TEXT DEFAULT (date('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS import_profiles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_name  TEXT NOT NULL UNIQUE,
            profile_type  TEXT DEFAULT 'actuals',
            settings_json TEXT NOT NULL,
            updated_at    TEXT DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS labour_task_mappings (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            project_no       TEXT,
            employee_name    TEXT NOT NULL,
            resource_group   TEXT,
            task_name        TEXT NOT NULL,
            task_description TEXT,
            is_active        INTEGER DEFAULT 1,
            updated_at       TEXT DEFAULT (date('now')),
            UNIQUE(project_no, employee_name, resource_group)
        );

        CREATE TABLE IF NOT EXISTS plan_tasks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id      INTEGER NOT NULL,
            task_name       TEXT NOT NULL,
            cost_type       TEXT NOT NULL DEFAULT 'Direct cost',
            resource_group  TEXT DEFAULT '',
            employees       TEXT DEFAULT '',
            sort_order      INTEGER DEFAULT 0,
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_values (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     INTEGER NOT NULL,
            col_key     TEXT NOT NULL,
            value       REAL DEFAULT 0,
            updated_at  TEXT DEFAULT (datetime('now')),
            updated_by  TEXT DEFAULT '',
            UNIQUE(task_id, col_key),
            FOREIGN KEY (task_id) REFERENCES plan_tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_text_values (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     INTEGER NOT NULL,
            col_key     TEXT NOT NULL,
            text_value  TEXT DEFAULT '',
            updated_at  TEXT DEFAULT (datetime('now')),
            updated_by  TEXT DEFAULT '',
            UNIQUE(task_id, col_key),
            FOREIGN KEY (task_id) REFERENCES plan_tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_columns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            col_key     TEXT NOT NULL,
            col_label   TEXT NOT NULL,
            col_type    TEXT DEFAULT 'custom',
            data_type   TEXT DEFAULT 'decimal',
            sort_order  INTEGER DEFAULT 100,
            UNIQUE(project_id, col_key),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_baselines (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id      INTEGER NOT NULL,
            baseline_name   TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now')),
            created_by      TEXT DEFAULT '',
            snapshot_json   TEXT NOT NULL,
            UNIQUE(project_id, baseline_name),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            task_id     INTEGER,
            task_name   TEXT,
            col_key     TEXT,
            old_value   TEXT,
            new_value   TEXT,
            action      TEXT DEFAULT 'update',
            changed_at  TEXT DEFAULT (datetime('now')),
            changed_by  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS plan_user_preferences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            user_name   TEXT NOT NULL,
            pref_key    TEXT NOT NULL,
            pref_value  TEXT NOT NULL,
            updated_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, user_name, pref_key),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
    """)

    # Lightweight migration for older DBs created before project_no existed.
    existing_cols = [r["name"] for r in c.execute("PRAGMA table_info(labour_task_mappings)").fetchall()]
    if "project_no" not in existing_cols:
        c.execute("ALTER TABLE labour_task_mappings ADD COLUMN project_no TEXT")

    # Lightweight migration for older DBs created before labour metadata existed.
    actuals_cols = [r["name"] for r in c.execute("PRAGMA table_info(actuals)").fetchall()]
    if "task_name" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN task_name TEXT")
    if "employee_name" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN employee_name TEXT")
    if "resource_group" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN resource_group TEXT")
    if "project_no" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN project_no TEXT")
    if "import_file" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN import_file TEXT")
    if "wbs_number" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN wbs_number TEXT")
    if "financial_document" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN financial_document TEXT")
    if "financial_document_posting_date" not in actuals_cols:
        c.execute("ALTER TABLE actuals ADD COLUMN financial_document_posting_date TEXT")

    # Lightweight migration for older DBs before plan_columns.data_type existed.
    plan_col_cols = [r["name"] for r in c.execute("PRAGMA table_info(plan_columns)").fetchall()]
    if "data_type" not in plan_col_cols:
        c.execute("ALTER TABLE plan_columns ADD COLUMN data_type TEXT DEFAULT 'decimal'")

    conn.commit()
    conn.close()


def ensure_default_import_profiles():
    """Seed requested default profile names if they do not exist."""
    defaults = [
        "1 for cost actuals",
        "2 for labour cost actuals",
    ]
    conn = get_conn()
    for name in defaults:
        existing = conn.execute(
            "SELECT id FROM import_profiles WHERE profile_name=?",
            (name,),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO import_profiles (profile_name, profile_type, settings_json) VALUES (?,?,?)",
                (name, "actuals", json.dumps({})),
            )
    conn.commit()
    conn.close()


# ── Projects ────────────────────────────────────────────────────────────────

def get_projects():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_project(name, description, start_date, end_date, status="Active"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO projects (name, description, start_date, end_date, status) VALUES (?,?,?,?,?)",
        (name, description, start_date, end_date, status),
    )
    conn.commit()
    conn.close()


def update_project(project_id, name, description, start_date, end_date, status):
    conn = get_conn()
    conn.execute(
        "UPDATE projects SET name=?, description=?, start_date=?, end_date=?, status=? WHERE id=?",
        (name, description, start_date, end_date, status, project_id),
    )
    conn.commit()
    conn.close()


def delete_project(project_id):
    conn = get_conn()
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()
    conn.close()


# ── Budget Items ─────────────────────────────────────────────────────────────

def get_budget_items(project_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM budget_items WHERE project_id=? ORDER BY category, month",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_budget_item(project_id, category, description, planned, month=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO budget_items (project_id, category, description, planned, month) VALUES (?,?,?,?,?)",
        (project_id, category, description, planned, month),
    )
    conn.commit()
    conn.close()


def update_budget_item(item_id, category, description, planned, month):
    conn = get_conn()
    conn.execute(
        "UPDATE budget_items SET category=?, description=?, planned=?, month=? WHERE id=?",
        (category, description, planned, month, item_id),
    )
    conn.commit()
    conn.close()


def delete_budget_item(item_id):
    conn = get_conn()
    conn.execute("DELETE FROM budget_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


# ── Actuals ──────────────────────────────────────────────────────────────────

def get_actuals(project_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM actuals WHERE project_id=? ORDER BY date DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_actual(
    project_id,
    category,
    description,
    amount,
    date,
    source="Manual",
    task_name=None,
    employee_name=None,
    resource_group=None,
    project_no=None,
    import_file=None,
    wbs_number=None,
    financial_document=None,
    financial_document_posting_date=None,
):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO actuals (
            project_id, category, description, amount, date, source,
            task_name, employee_name, resource_group, project_no, import_file,
            wbs_number, financial_document, financial_document_posting_date
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            project_id,
            category,
            description,
            amount,
            date,
            source,
            task_name,
            employee_name,
            resource_group,
            project_no,
            import_file,
            wbs_number,
            financial_document,
            financial_document_posting_date,
        ),
    )
    conn.commit()
    conn.close()


def delete_actual(actual_id):
    conn = get_conn()
    conn.execute("DELETE FROM actuals WHERE id=?", (actual_id,))
    conn.commit()
    conn.close()


def delete_actuals_by_source(project_id, source):
    conn = get_conn()
    conn.execute("DELETE FROM actuals WHERE project_id=? AND source=?", (project_id, source))
    conn.commit()
    conn.close()


def log_import_run(project_id, import_type, file_name, row_count):
    conn = get_conn()
    conn.execute(
        "INSERT INTO import_runs (project_id, import_type, file_name, row_count) VALUES (?,?,?,?)",
        (project_id, import_type, file_name, row_count),
    )
    conn.commit()
    conn.close()


def get_last_import_run(project_id, import_type):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT project_id, import_type, file_name, row_count, imported_at
        FROM import_runs
        WHERE project_id=? AND import_type=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (project_id, import_type),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Import Mapping Profiles ──────────────────────────────────────────────────

def get_import_profiles(profile_type="actuals"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT profile_name, settings_json, updated_at FROM import_profiles WHERE profile_type=? ORDER BY profile_name",
        (profile_type,),
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        settings = {}
        try:
            settings = json.loads(r["settings_json"] or "{}")
        except Exception:
            settings = {}
        result.append(
            {
                "profile_name": r["profile_name"],
                "settings": settings,
                "updated_at": r["updated_at"],
            }
        )
    return result


def save_import_profile(profile_name, settings, profile_type="actuals"):
    payload = json.dumps(settings or {})
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM import_profiles WHERE profile_name=? AND profile_type=?",
        (profile_name, profile_type),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE import_profiles SET settings_json=?, updated_at=date('now') WHERE id=?",
            (payload, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO import_profiles (profile_name, profile_type, settings_json) VALUES (?,?,?)",
            (profile_name, profile_type, payload),
        )
    conn.commit()
    conn.close()


def delete_import_profile(profile_name, profile_type="actuals"):
    conn = get_conn()
    conn.execute(
        "DELETE FROM import_profiles WHERE profile_name=? AND profile_type=?",
        (profile_name, profile_type),
    )
    conn.commit()
    conn.close()


# ── Labour Task Mappings ─────────────────────────────────────────────────────

def get_labour_task_mappings(active_only=False):
    conn = get_conn()
    if active_only:
        rows = conn.execute(
            """
            SELECT *
            FROM labour_task_mappings
            WHERE is_active=1
            ORDER BY project_no, employee_name, resource_group
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM labour_task_mappings
            ORDER BY project_no, employee_name, resource_group
            """
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_labour_task_mapping(project_no, employee_name, resource_group, task_name, task_description="", is_active=1):
    project_no = (project_no or "").strip()
    employee_name = (employee_name or "").strip()
    resource_group = (resource_group or "").strip()
    task_name = (task_name or "").strip()
    task_description = (task_description or "").strip()
    if not employee_name or not task_name:
        raise ValueError("employee_name and task_name are required")

    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM labour_task_mappings WHERE project_no=? AND employee_name=? AND resource_group=? AND task_name=?",
        (project_no, employee_name, resource_group, task_name),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE labour_task_mappings
            SET task_description=?, is_active=?, updated_at=date('now')
            WHERE id=?
            """,
            (task_description, 1 if is_active else 0, existing["id"]),
        )
        result = "updated"
    else:
        conn.execute(
            """
            INSERT INTO labour_task_mappings
            (project_no, employee_name, resource_group, task_name, task_description, is_active)
            VALUES (?,?,?,?,?,?)
            """,
            (project_no, employee_name, resource_group, task_name, task_description, 1 if is_active else 0),
        )
        result = "inserted"
    conn.commit()
    conn.close()
    return result


def delete_labour_task_mapping(mapping_id):
    conn = get_conn()
    conn.execute("DELETE FROM labour_task_mappings WHERE id=?", (mapping_id,))
    conn.commit()
    conn.close()


def update_labour_task_mapping(mapping_id, project_no, employee_name, resource_group, task_name, task_description="", is_active=1):
    project_no = (project_no or "").strip()
    employee_name = (employee_name or "").strip()
    resource_group = (resource_group or "").strip()
    task_name = (task_name or "").strip()
    task_description = (task_description or "").strip()
    if not employee_name or not task_name:
        raise ValueError("employee_name and task_name are required")

    conn = get_conn()
    conn.execute(
        """
        UPDATE labour_task_mappings
        SET project_no=?, employee_name=?, resource_group=?, task_name=?, task_description=?, is_active=?, updated_at=date('now')
        WHERE id=?
        """,
        (project_no, employee_name, resource_group, task_name, task_description, 1 if is_active else 0, mapping_id),
    )
    conn.commit()
    conn.close()


def apply_labour_mapping_to_actuals(project_no, employee_name, resource_group, task_name, previous_task_name=None):
    """Apply mapping to labour actuals and return updated row count.

    By default, updates only unmapped rows (blank task_name).
    If previous_task_name is provided, it also updates rows currently mapped to that previous task.
    """
    project_no = (project_no or "").strip()
    employee_name = (employee_name or "").strip()
    resource_group = (resource_group or "").strip()
    task_name = (task_name or "").strip()
    if not employee_name or not task_name:
        return 0

    conn = get_conn()
    prev = (previous_task_name or "").strip()
    if prev:
        cur = conn.execute(
            """
            UPDATE actuals
            SET task_name=?
            WHERE lower(trim(COALESCE(category,'')))='labour'
              AND lower(trim(COALESCE(employee_name,'')))=lower(trim(?))
              AND (?='' OR lower(trim(COALESCE(project_no,'')))=lower(trim(?)))
              AND (?='' OR lower(trim(COALESCE(resource_group,'')))=lower(trim(?)))
              AND (
                    trim(COALESCE(task_name,''))=''
                    OR lower(trim(COALESCE(task_name,'')))=lower(trim(?))
                  )
            """,
            (task_name, employee_name, project_no, project_no, resource_group, resource_group, prev),
        )
    else:
        cur = conn.execute(
            """
            UPDATE actuals
            SET task_name=?
            WHERE lower(trim(COALESCE(category,'')))='labour'
              AND lower(trim(COALESCE(employee_name,'')))=lower(trim(?))
              AND (?='' OR lower(trim(COALESCE(project_no,'')))=lower(trim(?)))
              AND (?='' OR lower(trim(COALESCE(resource_group,'')))=lower(trim(?)))
              AND trim(COALESCE(task_name,''))=''
            """,
            (task_name, employee_name, project_no, project_no, resource_group, resource_group),
        )
    updated = cur.rowcount or 0
    conn.commit()
    conn.close()
    return int(updated)


def resolve_labour_task_mapping(project_no, employee_name, resource_group):
    """
    Resolve mapping using Project no. as primary discriminator.
    Fallback order:
    1) project_no + employee + resource_group
    2) project_no + employee (blank resource_group)
    3) employee + resource_group (cross-project)
    4) employee only (cross-project)
    """
    project_no = (project_no or "").strip()
    employee_name = (employee_name or "").strip()
    resource_group = (resource_group or "").strip()
    if not employee_name:
        return None

    conn = get_conn()
    # 1) Exact match: project + employee + resource_group
    row = conn.execute(
        """
        SELECT *
        FROM labour_task_mappings
        WHERE is_active=1
          AND lower(trim(COALESCE(project_no,'')))=lower(trim(?))
          AND lower(trim(employee_name))=lower(trim(?))
          AND lower(trim(COALESCE(resource_group,'')))=lower(trim(?))
        LIMIT 1
        """,
        (project_no, employee_name, resource_group),
    ).fetchone()

    if not row:
        # 2) Project + employee fallback (blank resource_group)
        row = conn.execute(
            """
            SELECT *
            FROM labour_task_mappings
            WHERE is_active=1
              AND lower(trim(COALESCE(project_no,'')))=lower(trim(?))
              AND lower(trim(employee_name))=lower(trim(?))
              AND trim(COALESCE(resource_group,''))=''
            LIMIT 1
            """,
            (project_no, employee_name),
        ).fetchone()

    if not row:
        # 3) Cross-project fallback: employee + resource_group
        row = conn.execute(
            """
            SELECT *
            FROM labour_task_mappings
            WHERE is_active=1
              AND lower(trim(employee_name))=lower(trim(?))
              AND lower(trim(COALESCE(resource_group,'')))=lower(trim(?))
            LIMIT 1
            """,
            (employee_name, resource_group),
        ).fetchone()

    if not row:
        # 4) Cross-project fallback: employee only
        row = conn.execute(
            """
            SELECT *
            FROM labour_task_mappings
            WHERE is_active=1
              AND lower(trim(employee_name))=lower(trim(?))
              AND trim(COALESCE(resource_group,''))=''
            LIMIT 1
            """,
            (employee_name,),
        ).fetchone()

    conn.close()
    return dict(row) if row else None


# ── Forecasts ─────────────────────────────────────────────────────────────────

def get_forecasts(project_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM forecasts WHERE project_id=? ORDER BY category",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_forecast(project_id, category, etc, note):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM forecasts WHERE project_id=? AND category=?",
        (project_id, category),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE forecasts SET etc=?, note=?, updated_at=date('now') WHERE id=?",
            (etc, note, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO forecasts (project_id, category, etc, note) VALUES (?,?,?,?)",
            (project_id, category, etc, note),
        )
    conn.commit()
    conn.close()


# ── Summary helpers ───────────────────────────────────────────────────────────

def get_project_summary(project_id):
    """Returns total planned, actual, forecast (EAC) for a project."""
    conn = get_conn()
    # Prefer Budget Planning totals (sum of month columns in plan_values) when plan tasks exist.
    # Fallback to legacy budget_items if no plan tasks are defined for the project.
    has_plan_tasks = conn.execute(
        "SELECT 1 FROM plan_tasks WHERE project_id=? AND is_active=1 LIMIT 1",
        (project_id,),
    ).fetchone()

    if has_plan_tasks:
        planned = conn.execute(
            """
            SELECT COALESCE(SUM(pv.value),0)
            FROM plan_values pv
            JOIN plan_tasks pt ON pt.id = pv.task_id
            WHERE pt.project_id=?
              AND pt.is_active=1
              AND pv.col_key LIKE '____-__'
            """,
            (project_id,),
        ).fetchone()[0]
    else:
        planned = conn.execute(
            "SELECT COALESCE(SUM(planned),0) FROM budget_items WHERE project_id=?",
            (project_id,),
        ).fetchone()[0]
    actual = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM actuals WHERE project_id=?",
        (project_id,),
    ).fetchone()[0]
    etc = conn.execute(
        "SELECT COALESCE(SUM(etc),0) FROM forecasts WHERE project_id=?",
        (project_id,),
    ).fetchone()[0]
    conn.close()
    eac = actual + etc  # Estimate at Completion
    variance = planned - eac
    return {"planned": planned, "actual": actual, "etc": etc, "eac": eac, "variance": variance}


# ── Budget Plan Tasks ────────────────────────────────────────────────────────

def get_plan_tasks(project_id, active_only=True):
    conn = get_conn()
    if active_only:
        rows = conn.execute(
            "SELECT * FROM plan_tasks WHERE project_id=? AND is_active=1 ORDER BY sort_order, id",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM plan_tasks WHERE project_id=? ORDER BY sort_order, id",
            (project_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_plan_task(project_id, task_name, cost_type, resource_group, employees, sort_order=0):
    conn = get_conn()
    conn.execute(
        "INSERT INTO plan_tasks (project_id, task_name, cost_type, resource_group, employees, sort_order) VALUES (?,?,?,?,?,?)",
        (project_id, task_name, cost_type or "Direct cost", resource_group or "", employees or "", sort_order),
    )
    task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return task_id


def update_plan_task(task_id, task_name, cost_type, resource_group, employees):
    conn = get_conn()
    conn.execute(
        "UPDATE plan_tasks SET task_name=?, cost_type=?, resource_group=?, employees=? WHERE id=?",
        (task_name, cost_type or "Direct cost", resource_group or "", employees or "", task_id),
    )
    conn.commit()
    conn.close()


def delete_plan_task(task_id):
    conn = get_conn()
    conn.execute("DELETE FROM plan_values WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM plan_text_values WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM plan_tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()


# ── Budget Plan Values ───────────────────────────────────────────────────────

def get_plan_values_for_project(project_id):
    """Return dict: (task_id, col_key) -> value for all tasks in project."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT pv.task_id, pv.col_key, pv.value
        FROM plan_values pv
        JOIN plan_tasks pt ON pt.id = pv.task_id
        WHERE pt.project_id=?
        """,
        (project_id,),
    ).fetchall()
    conn.close()
    return {(r["task_id"], r["col_key"]): r["value"] for r in rows}


def upsert_plan_value(task_id, col_key, value, updated_by=""):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO plan_values (task_id, col_key, value, updated_at, updated_by)
        VALUES (?, ?, ?, datetime('now'), ?)
        ON CONFLICT(task_id, col_key) DO UPDATE
        SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by
        """,
        (task_id, col_key, value, updated_by),
    )
    conn.commit()
    conn.close()


def get_plan_text_values_for_project(project_id):
    """Return dict: (task_id, col_key) -> text_value for all tasks in project."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT ptv.task_id, ptv.col_key, ptv.text_value
        FROM plan_text_values ptv
        JOIN plan_tasks pt ON pt.id = ptv.task_id
        WHERE pt.project_id=?
        """,
        (project_id,),
    ).fetchall()
    conn.close()
    return {(r["task_id"], r["col_key"]): r["text_value"] for r in rows}


def upsert_plan_text_value(task_id, col_key, text_value, updated_by=""):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO plan_text_values (task_id, col_key, text_value, updated_at, updated_by)
        VALUES (?, ?, ?, datetime('now'), ?)
        ON CONFLICT(task_id, col_key) DO UPDATE
        SET text_value=excluded.text_value, updated_at=excluded.updated_at, updated_by=excluded.updated_by
        """,
        (task_id, col_key, str(text_value or ""), updated_by),
    )
    conn.commit()
    conn.close()


# ── Budget Plan Custom Columns ───────────────────────────────────────────────

def get_plan_columns(project_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM plan_columns WHERE project_id=? ORDER BY sort_order, id",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_plan_column(project_id, col_key, col_label, col_type="custom", data_type="decimal", sort_order=100):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO plan_columns (project_id, col_key, col_label, col_type, data_type, sort_order) VALUES (?,?,?,?,?,?)",
            (project_id, col_key, col_label, col_type, data_type or "decimal", sort_order),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # column key already exists for this project
    conn.close()


def update_plan_column_data_type(col_id, data_type):
    conn = get_conn()
    conn.execute(
        "UPDATE plan_columns SET data_type=? WHERE id=?",
        (data_type or "decimal", col_id),
    )
    conn.commit()
    conn.close()


def delete_plan_column(col_id):
    conn = get_conn()
    row = conn.execute("SELECT col_key, project_id FROM plan_columns WHERE id=?", (col_id,)).fetchone()
    if row:
        conn.execute(
            "DELETE FROM plan_values WHERE col_key=? AND task_id IN (SELECT id FROM plan_tasks WHERE project_id=?)",
            (row["col_key"], row["project_id"]),
        )
        conn.execute(
            "DELETE FROM plan_text_values WHERE col_key=? AND task_id IN (SELECT id FROM plan_tasks WHERE project_id=?)",
            (row["col_key"], row["project_id"]),
        )
        conn.execute("DELETE FROM plan_columns WHERE id=?", (col_id,))
    conn.commit()
    conn.close()


def get_plan_user_preference(project_id, user_name, pref_key, default=None):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT pref_value
        FROM plan_user_preferences
        WHERE project_id=? AND user_name=? AND pref_key=?
        """,
        (project_id, (user_name or "").strip(), pref_key),
    ).fetchone()
    conn.close()
    if not row:
        return default
    try:
        return json.loads(row["pref_value"])
    except Exception:
        return default


def set_plan_user_preference(project_id, user_name, pref_key, pref_value):
    user_name = (user_name or "").strip()
    if not user_name:
        return
    payload = json.dumps(pref_value)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO plan_user_preferences (project_id, user_name, pref_key, pref_value, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(project_id, user_name, pref_key) DO UPDATE
        SET pref_value=excluded.pref_value, updated_at=excluded.updated_at
        """,
        (project_id, user_name, pref_key, payload),
    )
    conn.commit()
    conn.close()


# ── Budget Plan Baselines ────────────────────────────────────────────────────

def get_plan_baselines(project_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, baseline_name, created_at, created_by FROM plan_baselines WHERE project_id=? ORDER BY created_at DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_plan_baseline(project_id, baseline_name, snapshot_json, created_by=""):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM plan_baselines WHERE project_id=? AND baseline_name=?",
        (project_id, baseline_name),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE plan_baselines SET snapshot_json=?, created_at=datetime('now'), created_by=? WHERE id=?",
            (snapshot_json, created_by, existing["id"]),
        )
        result = "updated"
    else:
        conn.execute(
            "INSERT INTO plan_baselines (project_id, baseline_name, snapshot_json, created_by) VALUES (?,?,?,?)",
            (project_id, baseline_name, snapshot_json, created_by),
        )
        result = "created"
    conn.commit()
    conn.close()
    return result


def get_plan_baseline(project_id, baseline_name):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM plan_baselines WHERE project_id=? AND baseline_name=?",
        (project_id, baseline_name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_plan_baseline(baseline_id):
    conn = get_conn()
    conn.execute("DELETE FROM plan_baselines WHERE id=?", (baseline_id,))
    conn.commit()
    conn.close()


# ── Budget Plan Audit Log ────────────────────────────────────────────────────

def add_plan_audit(project_id, task_id, task_name, col_key, old_value, new_value, action="update", changed_by=""):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO plan_audit_log
        (project_id, task_id, task_name, col_key, old_value, new_value, action, changed_by)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            project_id,
            task_id,
            task_name or "",
            col_key or "",
            str(old_value) if old_value is not None else "",
            str(new_value) if new_value is not None else "",
            action,
            changed_by or "",
        ),
    )
    conn.commit()
    conn.close()


def get_plan_audit_log(project_id, limit=500):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM plan_audit_log WHERE project_id=? ORDER BY id DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
