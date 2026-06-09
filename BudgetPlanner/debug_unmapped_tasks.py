import sys; sys.path.insert(0, '.')
import db, db_task_mappings
import datetime, pandas as pd
from collections import defaultdict

proj = db.get_projects()[0]
pid = proj['id']

tasks = db.get_plan_tasks(pid)
actuals = db.get_actuals(pid)
tvmap = db.get_plan_text_values_for_project(pid)
vmap = db.get_plan_values_for_project(pid)
cols = db.get_plan_columns(pid)

# Find MCR and Org columns
def canonical_mcr(v):
    return str(v or '').strip()

mcr_col = next((c['col_key'] for c in cols if c['col_key'] == 'custom_project_mcr'), None)
org_col = next((c['col_key'] for c in cols if c['col_key'] == 'custom_organisation'), None)

# Build plan task name set and task mappings
task_mappings = db_task_mappings.get_all_task_mappings(pid)
map_exact = {(m['actual_task_name'], m['actual_mcr']): m['plan_task_name'] for m in task_mappings if m['actual_mcr']}
map_catch = {m['actual_task_name']: m['plan_task_name'] for m in task_mappings}

plan_task_names = set()
task_to_org = {}
for t in tasks:
    tid = int(t['id'])
    tname = str(t.get('task_name') or '').strip()
    mcr = canonical_mcr(tvmap.get((tid, mcr_col), ''))
    org = canonical_mcr(tvmap.get((tid, org_col), ''))
    plan_task_names.add(tname)
    task_to_org[(mcr, tname)] = org
    task_to_org[(None, tname)] = org

# Find unmapped actuals
xc_mcr_set = {'BM-00110021_004', 'BM-00110021_005'}
unmapped = defaultdict(lambda: defaultdict(float))  # (raw_task, mcr) -> month -> amount
unmapped_total = defaultdict(float)

for a in actuals:
    dt = pd.to_datetime(a.get('date'), errors='coerce')
    if pd.isna(dt): continue
    ym = dt.strftime('%Y-%m')
    amt = float(a.get('amount', 0) or 0)
    task_raw = str(a.get('task_name') or '').strip()
    mcr_raw = canonical_mcr(a.get('project_no'))
    
    if mcr_raw in xc_mcr_set:
        continue
    
    mapped = (map_exact.get((task_raw, mcr_raw))
              or map_catch.get(task_raw)
              or task_raw)
    
    if not mapped or mapped not in plan_task_names:
        key = (task_raw, mcr_raw)
        unmapped[key][ym] += amt
        unmapped_total[key] += amt

print(f"Unmapped actual tasks (not matching any plan task name):\n")
print(f"{'Actual Task Name':<50} {'MCR':<25} {'Total Actuals (€)':>18}")
print("-" * 95)
for (tname, mcr), total in sorted(unmapped_total.items(), key=lambda x: -abs(x[1])):
    print(f"{tname:<50} {mcr:<25} {total:>18,.2f}")

print(f"\nTotal unmapped actuals: €{sum(unmapped_total.values()):,.2f}")
print(f"\nFor reference: 1.9/1.10 overcounting vs 1.5 = ~€122,325")
