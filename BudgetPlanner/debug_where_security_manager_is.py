import os
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "budget_planner.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("task_name_mappings rows:", cur.execute("select count(*) c from task_name_mappings").fetchone()["c"])
print("labour_task_mappings rows:", cur.execute("select count(*) c from labour_task_mappings").fetchone()["c"])

print("\nSecurity Manager in task_name_mappings:")
rows = cur.execute(
    "select project_id, actual_task_name, actual_mcr, plan_task_name from task_name_mappings where lower(trim(actual_task_name))='security manager'"
).fetchall()
if not rows:
    print("  none")
for r in rows:
    print(dict(r))

print("\nSecurity Manager in labour_task_mappings:")
rows = cur.execute(
    "select id, project_no, employee_name, resource_group, task_name, is_active, updated_at from labour_task_mappings where lower(trim(task_name))='security manager'"
).fetchall()
if not rows:
    print("  none")
for r in rows:
    print(dict(r))

con.close()
