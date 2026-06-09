import sys; sys.path.insert(0, r"c:\Users\kas2sf\OneDrive - Bosch Group\1. Projects\Coding\BudgetPlanner")
import db
import db_task_mappings
import pandas as pd

proj = db.get_projects()[0]
pid = proj['id']

print(f"Project: {proj['name']} (id={pid})")
print("\nMappings in DB:")
rows = db_task_mappings.get_all_task_mappings(pid)
for r in rows:
    print(f"  actual_task_name={r['actual_task_name']!r}, actual_mcr={r['actual_mcr']!r}, plan_task_name={r['plan_task_name']!r}")

# check only rows relevant to prior unmapped findings
print("\nRelevant mapping checks:")
for needle in ["Security Manager", ""]:
    found = [r for r in rows if r['actual_task_name'] == needle]
    print(f"  {needle!r}: {len(found)} rows")
    for r in found:
        print(f"    -> mcr={r['actual_mcr']!r}, plan={r['plan_task_name']!r}")

# list actual task names that are blank or security manager
actuals = db.get_actuals(pid)
acc = {}
for a in actuals:
    tn = str(a.get('task_name') or '').strip()
    m = str(a.get('project_no') or '').strip()
    amt = float(a.get('amount',0) or 0)
    key=(tn,m)
    acc[key]=acc.get(key,0.0)+amt

print("\nActuals for suspicious tasks:")
for (tn,m),amt in sorted(acc.items(), key=lambda kv: -abs(kv[1])):
    if tn in ("", "Security Manager"):
        print(f"  task={tn!r}, mcr={m!r}, total={amt:,.2f}")
