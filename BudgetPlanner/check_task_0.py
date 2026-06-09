#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('budget_planner.db')

# Check for task_id = 0 entries
count_task_0 = conn.execute("SELECT COUNT(*) FROM plan_values WHERE task_id = 0").fetchone()[0]
print(f"Plan values with task_id = 0: {count_task_0}")

# Check for task_id = 0 entries in project 1 specifically
count_proj_1 = conn.execute("""
    SELECT COUNT(*) FROM plan_values pv
    WHERE task_id = 0 
""").fetchone()[0]
print(f"Plan values with task_id = 0 (any project): {count_proj_1}")

# Show what task_id values exist in project 1
task_ids = conn.execute("""
    SELECT DISTINCT task_id FROM plan_values 
    WHERE task_id IN (SELECT id FROM plan_tasks WHERE project_id = 1) OR task_id = 0
    ORDER BY task_id
""").fetchall()
print(f"\nUnique task_ids in project 1: {len(task_ids)}")
print(f"Includes task_id=0: {any(t[0] == 0 for t in task_ids)}")

# Show task_id 0 value distribution
if count_proj_1 > 0:
    print(f"\nSample task_id=0 entries:")
    for row in conn.execute("SELECT col_key, SUM(value) FROM plan_values WHERE task_id = 0 GROUP BY col_key LIMIT 10"):
        print(f"  {row[0]}: €{row[1]:,.2f}")

conn.close()
