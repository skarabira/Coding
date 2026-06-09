# Analysis Summary: Section 1.5 vs 1.9/1.10 EAC Discrepancies

**Investigation Date**: June 8, 2026

---

## Executive Summary

The discrepancies between sections 1.5 and 1.9/1.10 are NOT due to project selection mismatches (which have been fixed via `st.rerun()`). Instead, they stem from a **fundamental architectural issue: Section 1.9/1.10 uses incorrect task lookup keys when computing future month EAC values**.

**The Problem**: 
- Plan database has NO MCR column values
- Section 1.9 keys tasks with blank MCR: `("C-Hub", "", task_name)`
- When actuals come in with MCRs, they key as: `("C-Hub", "002", task_name)`  
- These keys don't match → task IDs not found → future month plan values missing

**Result**: 
- Section 1.5 shows correct full EAC (actuals + plan)
- Sections 1.9/1.10 show only actuals (plan values for future months are missing)

---

## Answers to Your Investigation Questions

### 1. Does section 1.5 use `_monthly_by_key` for past and `_dash_vmap` for future?
**✅ YES** (Confirmed, Line 1168-1200)

Section 1.5 calculation:
```python
for _m in _months:
    if _m < _current_ym:
        # Past: Use actual transaction data
        _v = float(_monthly_by_key.get((_mcr, _task_name_key), {}).get(_m, 0.0) or 0.0)
    else:
        # Future: Use plan values + overrides
        _base = float(_dash_vmap.get((_tid, _m), 0.0) or 0.0)
        _v = float(_override_map.get((_mcr, _tid, _m), _base) or 0.0)
```

### 2. Does section 1.9/1.10 use `_actual_by_key_month` for past and `_org_vmap` for future?
**✅ YES** (Confirmed, Line 2039-2050)

Section 1.9 calculation:
```python
for _m in _org_months:
    if _m < _org_current_ym:
        # Past: Use actual transaction data
        _eac_m = float(_actual_by_key_month.get((_org_name, _mcr, _task_name), {}).get(_m, 0.0) or 0.0)
    else:
        # Future: Should use plan values + overrides (BUT THIS FAILS)
        for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):
            _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
            _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
```

**⚠️ PROBLEM**: The lookup for `_task_ids_by_key[(_org_name, _mcr, _task_name)]` FAILS because:
- `_task_ids_by_key` was built with blank MCR: `_task_ids_by_key[("C-Hub", "", task_name)]`
- But we're looking it up with specific MCR: `_task_ids_by_key[("C-Hub", "BM-00110021_002", task_name)]`
- These keys don't match → returns empty set → loop never executes

### 3. Are `_dash_vmap` and `_org_vmap` the same data?
**✅ YES** (Confirmed, Both use `db.get_plan_values_for_project(project_id)`)

Both sections get plan values from the same database table:
- Same structure: `{(task_id, month): plan_value}`
- Same data: €5,090,577 total across 79 tasks × 12 months
- Same mapping: 1,171 entries in the dictionary

### 4. Are there different numbers of tasks assigned to MCRs in 1.5 vs 1.9?
**~PARTIALLY** (Different key structures cause functional difference)

- **Section 1.5**: All 79 tasks initially keyed with MCR "(blank)", then MCRs added from actuals
- **Section 1.9**: All 79 tasks keyed with MCR "", but then lookup fails for specific MCRs
- Result: Section 1.9 can find MCRs for ACTUALS but NOT for PLAN values

### 5. Does section 1.5 filter or exclude certain tasks?
**✅ YES** (Different filtering rules)

| Aspect | Section 1.5 | Section 1.9 |
|--------|-------------|------------|
| **Current YTD** | `if _dt.date() <= _today and _dt.year == _today.year` | Not used; full history included |
| **Past month filter** | `if _ym not in _month_index or _ym >= _current_ym` | `if _ym not in _org_months` |
| **Future override filter** | `if _ov_month and _ov_month < _current_ym: continue` | `if _ov_month < _org_current_ym: continue` |
| **Result** | YTD actuals only for display; past-month aggregation for calc | All actuals included |

### 6. Check if section 1.5 uses `_src_df` and 1.9 uses different task list?
**✅ YES** (Different task aggregation approaches)

- **Section 1.5**: `_src_df` built with 79 plan tasks, MCRs derived from actuals
- **Section 1.9**: `_plan_by_key_month` built with 79 plan tasks, but MCRs not properly linked

Both use the same 79 tasks, but organize them differently.

---

## Root Cause Technical Deep Dive

### The Data Reality
```
Database Tables:
├─ plan_tasks (79 rows)
│  └─ task_name column: populated ("AHR enabler", "BD Support", etc.)
├─ plan_values (~1,171 rows)
│  └─ (task_id, month): plan amounts
├─ plan_columns (includes 'prj_mcr_number')
│  └─ But NO values stored for this column in plan_values
├─ actuals (442 rows)
│  ├─ project_no: "BM-00110021_002", "BM-00110021_003", etc.
│  └─ task_name: matches plan task names or requires mapping
├─ labour_task_mappings (0 rows)
│  └─ Designed to map actual task names to plan task names
│  └─ Currently unused
└─ forecast_overrides (116 rows)
   ├─ mcr: "BM-00110021_002" etc.
   └─ month_year: only future months (>= 2026-06)
```

### Section 1.9 Key Building (Lines 1920-1946)

**Plan loop - trying to derive MCR from plan:**
```python
for _t in _org_tasks:
    _tid = int(_t.get("id"))
    _tname = str(_t.get("task_name")).strip()
    
    # Try to get MCR from plan - FAILS because column is empty
    _mcr = str(_org_vmap.get((_tid, _org_mcr_col), "") or "").strip()
    if not _mcr:
        _mcr = str(_org_tvmap.get((_tid, _org_mcr_col), "") or "").strip()
    # Result: _mcr = "" (blank)
    
    _org_name = "C-Hub"  # Derived from plan data
    _key = (_org_name, _mcr, _tname)  # KEY = ("C-Hub", "", "BD Support")
    _task_ids_by_key.setdefault(_key, set()).add(_tid)
```

**Result**: 79 tasks keyed as:
- ("C-Hub", "", "AHR enabler") → task_id 4738
- ("C-Hub", "", "BD Support") → task_id 4739
- etc.

### Section 1.9 Actual Loop (Lines 1970-1984)

**Actual loop - MCRs come from actuals:**
```python
for _a in _org_actuals:
    _task_raw = str(_a.get("task_name")).strip()
    _mcr_raw = canonical_project_mcr(_a.get("project_no"))
    # Result: _mcr_raw = "BM-00110021_002" (from actual data!)
    
    _mapped_task = _org_map_exact.get((_task_raw, _mcr_raw)) or ...
    # _org_map_exact is empty (no task mappings)
    # So _mapped_task = _task_raw (original actual task name)
    
    _org_name = _task_to_org.get((_mcr_raw, _mapped_task)) or "C-Hub"
    _key = (_org_name, _mcr_raw, _mapped_task)  # KEY = ("C-Hub", "BM-00110021_002", task_name)
    
    _actual_by_key_month.setdefault(_key, {})[_ym] = _actual_by_key_month[_key].get(_ym, 0.0) + _amt
```

**Result**: Actuals keyed as:
- ("C-Hub", "BM-00110021_002", "BD Support") → €1,566,813 (past months)
- ("C-Hub", "BM-00110021_003", "...") → €397,871 (past months)
- etc.

### The Lookup Failure (Line 2045-2047)

When calculating EAC for MCR 002 in future months:

```python
for _m in _org_months:
    if _m >= _org_current_ym:  # Future month (e.g., 2026-07)
        _future_val = 0.0
        
        # Try to find task IDs for this actual's MCR
        _mcr = "BM-00110021_002"  # From actual data
        _task_name = "BD Support"
        
        for _tid in _task_ids_by_key.get(("C-Hub", "BM-00110021_002", "BD Support"), set()):
            #                         ↑ Key doesn't exist!
            #                         ↑ Only ("C-Hub", "", "BD Support") exists
            #                         ↑ KEY MISMATCH!
            
            # This loop never executes because set is empty
            _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
            _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
        
        _eac_m = _future_val  # Result: 0.0 (no plan values added!)
```

---

## Data Verification

### Database Contents Confirmed
- **Plan tasks**: 79 (no MCR column data)
- **Actuals**: 442 records
  - BM-00110021_002: 137 records (€1,566,813 total)
  - BM-00110021_003: 53 records (€397,871 total)
  - BM-00110021_004: 123 records (€276,757 total)
  - BM-00110021_005: 129 records (€202,519 total)
- **Task mappings**: 0 entries (unused feature)
- **Overrides**: 116 entries (future months only)
  - MCR 002 future: €2,626,093
  - MCR 003 future: -€688,890 (negative adjustment)

### Total Plan by Task
- €5,090,577 distributed across 79 tasks × 12 months
- All tasks initially keyed with MCR = "" (blank) due to empty plan column

---

## Why the User Sees Discrepancies

### Section 1.5 Display (Correct ✓)
- **Past months**: Shows actual transaction totals from `_monthly_by_key`
- **Future months**: Adds plan values from `_dash_vmap` + overrides
- **Result**: Full 12-month EAC including both historical actuals AND projected future spending

Example for MCR 002:
- Past 5 months actuals: €1,566,813
- Future 7 months plan + overrides: €3,420,032
- **Total EAC: €4,986,845**

### Section 1.9/1.10 Display (Broken ✗)
- **Past months**: Shows actual transaction totals from `_actual_by_key_month`
- **Future months**: Tries to add plan values but KEY LOOKUP FAILS
- **Result**: Only shows past actuals, missing all future months

Example for MCR 002:
- Past 5 months actuals: €1,566,813
- Future 7 months plan: €0 (MISSING - lookup failed)
- **Total EAC: €1,566,813 (INCORRECT)**

**Discrepancy**: €3,420,032 (~68% missing)

---

## The Fix Required

### Problem Statement
Section 1.9/1.10 needs to associate plan task IDs with the MCRs found in actual data when calculating future months.

### Solution Approach
Modify Section 1.9's future month calculation to NOT require MCR-specific keys:

**Current (Broken)**:
```python
# Lookup requires key to match actuals' MCR
for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):
    # Fails: key doesn't exist for specific MCR
```

**Should Be**:
```python
# Store task IDs keyed only by task name (independent of MCR)
# Plan lookup _org_vmap[(task_id, month)] doesn't need MCR anyway
for _tid in _task_ids_by_name.get(_task_name, set()):
    _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
    _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
```

### Specific Code Changes

**In Section 1.9, around line 1920:**

Change from building `_task_ids_by_key` indexed by (org, mcr, task):
```python
_task_ids_by_key.setdefault(_key, set()).add(_tid)
```

To building `_task_ids_by_name` indexed by task only:
```python
_task_ids_by_name.setdefault(_tname, set()).add(_tid)
```

Then in future month calculation (line ~2045):
```python
# Change from:
for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):

# To:
for _tid in _task_ids_by_name.get(_task_name, set()):
```

---

## Verification Needed

After implementing the fix, verify:

1. ✅ Section 1.9/1.10 shows same MCR totals as Section 1.5
2. ✅ Future months include both plan values AND overrides
3. ✅ Past months match actuals exactly
4. ✅ XC (MCR 004/005) displayed correctly as synthetic row
5. ✅ No regression in any other section calculations

---

## Related Documentation

See also:
- `ANALYSIS_SECTIONS_1.5_VS_1.9_DISCREPANCIES.md` – Detailed technical analysis
- Session memory: `/memories/session/sections_1_5_vs_1_9_analysis.md`
- Updated README.md with known issues section
