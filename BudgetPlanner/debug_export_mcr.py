import db
p = db.get_projects()[0]
pid = p['id']
cols = db.get_plan_columns(pid)
print('All columns:')
for c in cols:
    print(f"  col_key={c['col_key']!r}, col_label={c['col_label']!r}, data_type={c.get('data_type')!r}")

tvmap = db.get_plan_text_values_for_project(pid)
tasks = db.get_plan_tasks(pid)
t = tasks[0]; tid = int(t['id'])
print(f"\nSample task id={tid} name={t['task_name']}")
print('Text values:', {k[1]: v for k, v in tvmap.items() if k[0] == tid})

# Simulate MCR col detection from export
_exp_mcr_col = None
_exp_rg_type_col = None
for c in cols:
    ck = (c.get('col_key') or '').lower()
    cl = (c.get('col_label') or '').lower()
    print(f"  checking col_key={ck!r} col_label={cl!r}")
    if _exp_mcr_col is None and ('custom_project_mcr' in ck or 'project mcr' in cl or 'prj mcr number' in cl):
        _exp_mcr_col = c['col_key']
        print(f"  -> MCR col matched: {_exp_mcr_col!r}")
    if _exp_rg_type_col is None and ('sp_rg_type' in ck or 'rg type' in cl or 'sp rg type' in cl):
        _exp_rg_type_col = c['col_key']
        print(f"  -> RG type col matched: {_exp_rg_type_col!r}")

print(f"\nResolved MCR col: {_exp_mcr_col!r}")
print(f"Resolved RG type col: {_exp_rg_type_col!r}")

# Test get_text for first task
def get_text(tid, col):
    if not col: return ''
    v = str(tvmap.get((tid, col), '') or '').strip()
    vmap = db.get_plan_values_for_project(pid)
    if not v:
        v = str(vmap.get((tid, col), '') or '').strip()
    return v

print(f"\nMCR for task {tid}: {get_text(tid, _exp_mcr_col)!r}")
