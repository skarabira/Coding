import sys; sys.path.insert(0, r"c:\Users\kas2sf\OneDrive - Bosch Group\1. Projects\Coding\BudgetPlanner")
import db
import db_task_mappings

projects = db.get_projects()
for p in projects:
    rows = db_task_mappings.get_all_task_mappings(p['id'])
    print(f"Project {p['id']} - {p['name']}: {len(rows)} mappings")
    for r in rows[:10]:
        print(f"  actual={r['actual_task_name']!r}, mcr={r['actual_mcr']!r}, plan={r['plan_task_name']!r}")
    if len(rows) > 10:
        print(f"  ... {len(rows)-10} more")
