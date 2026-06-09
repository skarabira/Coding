#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('budget_planner.db')
cur = conn.execute('''
    SELECT DISTINCT text_value FROM plan_text_values 
    WHERE col_key = 'custom_project_mcr' 
    AND task_id IN (SELECT id FROM plan_tasks WHERE project_id = 1)
    ORDER BY text_value
''')
rows = [row[0] for row in cur]
print(f'Found {len(rows)} MCRs:')
for mcr in rows:
    print(f'  {mcr}')

conn.close()
