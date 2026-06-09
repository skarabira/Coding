#!/usr/bin/env python3
"""Debug script to compare EAC calculations across sections 1.4/1.5 vs 1.9/1.10."""

import sqlite3
import pandas as pd
from datetime import datetime, date

DB_PATH = "budget_planner.db"
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

project_id = 1
proj_cursor = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
proj = proj_cursor.fetchone()
if not proj:
    print(f"Project {project_id} not found")
    exit(1)

print(f"\n{'='*100}")
print(f"EAC CALCULATION COMPARISON: Section 1.5 vs Sections 1.9/1.10")
print(f"Project: {proj['name']}")
print(f"{'='*100}\n")

# Setup basic data
try:
    start_date = datetime.fromisoformat(proj['start_date']).date()
    end_date = datetime.fromisoformat(proj['end_date']).date()
except:
    start_date = date.today().replace(day=1)
    end_date = start_date.replace(year=start_date.year + 1)

months = []
cur = start_date.replace(day=1)
while cur <= end_date.replace(day=1):
    months.append(cur.strftime("%Y-%m"))
    if cur.month == 12:
        cur = cur.replace(year=cur.year + 1, month=1)
    else:
        cur = cur.replace(month=cur.month + 1)

current_ym = date.today().strftime("%Y-%m")
print(f"Period: {start_date} to {end_date}")
print(f"Months: {len(months)} months ({months[0]} to {months[-1]})")
print(f"Current YM: {current_ym}\n")

# Get plan values
plan_vmap = {}
for row in conn.execute("""
    SELECT task_id, col_key, value FROM plan_values
    WHERE task_id IN (SELECT id FROM plan_tasks WHERE project_id = ?)
""", (project_id,)):
    plan_vmap[(row['task_id'], row['col_key'])] = float(row['value'] or 0)

# Get text values
text_vmap = {}
for row in conn.execute("""
    SELECT task_id, col_key, text_value FROM plan_text_values
    WHERE task_id IN (SELECT id FROM plan_tasks WHERE project_id = ?)
""", (project_id,)):
    text_vmap[(row['task_id'], row['col_key'])] = row['text_value']

# Get plan columns
cols = list(conn.execute("SELECT * FROM plan_columns WHERE project_id = ?", (project_id,)))
mcr_col_key = next((c['col_key'] for c in cols if 'mcr' in c['col_key'].lower() or 'project' in c['col_key'].lower()), None)

print(f"MCR Column: {mcr_col_key}\n")

# Get tasks
tasks = list(conn.execute("SELECT id, task_name FROM plan_tasks WHERE project_id = ?", (project_id,)))
task_by_id = {t['id']: t for t in tasks}

# Get actuals
actuals = list(conn.execute("SELECT * FROM actuals WHERE project_id = ?", (project_id,)))
print(f"Total actuals records: {len(actuals)}\n")

# ============================================================================
# SECTION 1.5 APPROACH: EAC per MCR
# ============================================================================
print(f"{'='*100}")
print("SECTION 1.5 APPROACH: _estimated_total_by_mcr calculation")
print(f"{'='*100}\n")

# Build MCR -> Tasks mapping
mcr_tasks = {}
for tid, tinfo in task_by_id.items():
    mcr = text_vmap.get((tid, mcr_col_key), "")
    if not mcr:
        mcr = plan_vmap.get((tid, mcr_col_key), "")
    mcr = str(mcr).strip() if mcr else "(blank)"
    mcr_tasks.setdefault(mcr, []).append(tid)

print(f"MCRs with tasks: {len(mcr_tasks)}")
for mcr in sorted(mcr_tasks.keys()):
    print(f"  {mcr}: {len(mcr_tasks[mcr])} tasks")

# Build monthly actuals by (MCR, Task)
monthly_by_key = {}  # (MCR, task) -> month -> amount
monthly_by_mcr = {}  # MCR -> month -> amount (for non-expandable)
xc_mcrs = {"BM-00110021_004", "BM-00110021_005"}

for a in actuals:
    a = dict(a)
    dt = pd.to_datetime(a.get("date"), errors="coerce")
    if pd.isna(dt):
        continue
    ym = dt.strftime("%Y-%m")
    if ym not in months or ym >= current_ym:
        continue
    
    amt = float(a.get("amount") or 0)
    mcr = str(a.get("project_no") or "").strip()
    if not mcr:
        mcr = "(blank)"
    
    if mcr in xc_mcrs:
        monthly_by_mcr.setdefault(mcr, {}).setdefault(ym, 0.0)
        monthly_by_mcr[mcr][ym] += amt
    else:
        tname = str(a.get("task_name") or "").strip() or "(unmapped task)"
        key = (mcr, tname)
        monthly_by_key.setdefault(key, {})[ym] = monthly_by_key.get(key, {}).get(ym, 0.0) + amt

# Calculate EAC per MCR (simplified - future months use plan only, no overrides)
eac_by_mcr_1_5 = {}
for mcr in sorted(mcr_tasks.keys()):
    mcr_eac = 0.0
    is_non_expandable = mcr in xc_mcrs
    
    for m in months:
        if m < current_ym:
            # Past: use actuals
            if is_non_expandable:
                m_total = monthly_by_mcr.get(mcr, {}).get(m, 0.0)
            else:
                m_total = 0.0
                for tid in mcr_tasks[mcr]:
                    tname = task_by_id[tid]['task_name']
                    m_total += monthly_by_key.get((mcr, tname), {}).get(m, 0.0)
        else:
            # Future: use plan
            m_total = 0.0
            for tid in mcr_tasks[mcr]:
                m_total += plan_vmap.get((tid, m), 0.0)
        
        mcr_eac += m_total
    
    eac_by_mcr_1_5[mcr] = mcr_eac

total_eac_1_5 = sum(eac_by_mcr_1_5.values())
print(f"\nSection 1.5 EAC per MCR:")
for mcr in sorted(eac_by_mcr_1_5.keys()):
    print(f"  {mcr}: €{eac_by_mcr_1_5[mcr]:,.2f}")
print(f"\nSection 1.5 TOTAL EAC: €{total_eac_1_5:,.2f}\n")

# ============================================================================
# SECTION 1.9 APPROACH: EAC per (Org, MCR, Task)
# ============================================================================
print(f"{'='*100}")
print("SECTION 1.9 APPROACH: Parent rows EAC calculation (excluding 004/005 from base)")
print(f"{'='*100}\n")

# Build (Org, MCR, Task) rows
org_col_key = next((c['col_key'] for c in cols if 'org' in c['col_key'].lower()), None)
print(f"Org Column: {org_col_key}\n")

rows_1_9 = {}  # (org, mcr, task) -> eac
xc_eac = 0.0

for tid in task_by_id.keys():
    tname = task_by_id[tid]['task_name']
    mcr = text_vmap.get((tid, mcr_col_key), "")
    if not mcr:
        mcr = plan_vmap.get((tid, mcr_col_key), "")
    mcr = str(mcr).strip() if mcr else "(blank)"
    
    org = text_vmap.get((tid, org_col_key), "") if org_col_key else ""
    if not org:
        org = plan_vmap.get((tid, org_col_key), "") if org_col_key else ""
    org = str(org).strip() if org else "Unknown Org"
    
    # Skip 004/005 from base rows (they go into XC)
    if mcr in xc_mcrs:
        # Add to XC total
        task_eac = 0.0
        for m in months:
            if m < current_ym:
                task_eac += monthly_by_key.get((mcr, tname), {}).get(m, 0.0)
            else:
                task_eac += plan_vmap.get((tid, m), 0.0)
        xc_eac += task_eac
    else:
        # Add to regular row
        task_eac = 0.0
        for m in months:
            if m < current_ym:
                task_eac += monthly_by_key.get((mcr, tname), {}).get(m, 0.0)
            else:
                task_eac += plan_vmap.get((tid, m), 0.0)
        key = (org, mcr, tname)
        rows_1_9[key] = task_eac

total_eac_1_9_base = sum(rows_1_9.values())
total_eac_1_9 = total_eac_1_9_base + xc_eac

print(f"Section 1.9 Base rows (excluding 004/005): €{total_eac_1_9_base:,.2f}")
print(f"Section 1.9 XC synthetic row (004/005 only): €{xc_eac:,.2f}")
print(f"Section 1.9 TOTAL EAC: €{total_eac_1_9:,.2f}\n")

# ============================================================================
# COMPARISON
# ============================================================================
print(f"{'='*100}")
print("COMPARISON")
print(f"{'='*100}\n")

print(f"Section 1.5 (all MCRs in base): €{total_eac_1_5:,.2f}")
print(f"Section 1.9 (excl 004/005 from base + XC row): €{total_eac_1_9:,.2f}")
print(f"Difference: €{abs(total_eac_1_9 - total_eac_1_5):,.2f}")
print(f"Percentage: {abs(total_eac_1_9 - total_eac_1_5) / total_eac_1_5 * 100:.2f}%\n")

# ============================================================================
# DETAILED BREAKDOWN
# ============================================================================
print(f"{'='*100}")
print("DETAILED BREAKDOWN")
print(f"{'='*100}\n")

print("MCR-by-MCR comparison:")
print(f"{'MCR':<25} {'Section 1.5':>20} {'Section 1.9':>20} {'Difference':>20}")
print("-" * 90)

for mcr in sorted(set(list(eac_by_mcr_1_5.keys()))):
    v1 = eac_by_mcr_1_5.get(mcr, 0.0)
    if mcr in xc_mcrs:
        v2_msg = f"(in XC: €{xc_eac:,.2f})"
    else:
        v2 = sum(v for (o, m, t), v in rows_1_9.items() if m == mcr)
        v2_msg = f"€{v2:,.2f}"
    
    diff = v1 - float(v2_msg.split("€")[-1].replace(",", "").split(")")[0]) if "€" in str(v2_msg) else 0
    print(f"{mcr:<25} €{v1:>18,.2f} {v2_msg:>20} €{diff:>18,.2f}")

conn.close()
print(f"\n{'='*100}\n")
