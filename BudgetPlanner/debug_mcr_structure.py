import sys; sys.path.insert(0, '.')
import db

proj = db.get_projects()[0]
pid = proj['id']
tasks = db.get_plan_tasks(pid)
tvmap = db.get_plan_text_values_for_project(pid)
cols = db.get_plan_columns(pid)

print("All column keys:", [c['col_key'] for c in cols])
mcr_cols = [c for c in cols if any(x in (c.get('col_key') or '').lower() for x in ['mcr', 'project_no', 'project no', 'prj_mcr'])]
print("\nMCR-like columns:", mcr_cols)

# Sample first 5 tasks
for t in tasks[:5]:
    tid = int(t['id'])
    tname = t.get('task_name', '')[:40]
    text_vals = {k[1]: v for k, v in tvmap.items() if k[0] == tid}
    print(f"\nTask {tid} ({tname}):")
    print(f"  text vals: {text_vals}")
