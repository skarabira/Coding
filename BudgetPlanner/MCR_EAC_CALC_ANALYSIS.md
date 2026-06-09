# MCR EAC Calculation Analysis: Section 1.5 vs 1.9/1.10

## Problem Statement
- **Section 1.5**: MCR 002 = €4,019,510, MCR 003 = €243,609
- **Section 1.9/1.10**: MCR 002 = €4,067,553 (€48,043 HIGHER), MCR 003 = €260,241 (€16,632 HIGHER)
- **Difference**: €64,675 total

## Key Finding: DIFFERENT DATA SOURCES

### Section 1.5 Uses:
- **Project**: `_dash_proj` (Line 983: `_dash_vmap = db.get_plan_values_for_project(_dash_proj["id"])`)
- **Data**: Dashboard project data

### Section 1.9/1.10 Uses:
- **Project**: `_org_proj` (Line 1836: `_org_vmap = db.get_plan_values_for_project(_org_proj["id"])`)
- **Data**: Organization project data

These are **completely different projects** and may have different tasks, values, or structures!

---

## SECTION 1.5: MCR EAC Calculation Logic

### Step 1: Build `_estimated_total_by_mcr` (Lines 1140-1189)

```python
_estimated_total_by_mcr = {}
_mcr_universe = sorted(
    set(_src_df["Project MCR #"].astype(str).unique().tolist())
    | set(_actual_ytd_by_mcr.keys())
    | set(_monthly_by_mcr.keys())
    | {k[0] for k in _monthly_by_key.keys()}
)

for _mcr in _mcr_universe:
    _mdf = _src_df[_src_df["Project MCR #"] == _mcr].copy()
    _is_non_expandable = _mcr in _mcrs_without_task_match
    _task_agg = _mdf.groupby("Task Name", dropna=False, as_index=False).agg({"__task_id": "first"}).sort_values("Task Name")

    # STEP 1A: For expandable MCRs, pre-calculate task-level EAC (unused for MCR total, but built)
    if not _is_non_expandable:
        for _, _tr in _task_agg.iterrows():
            _tn = str(_tr.get("Task Name") or "").strip()
            _task_name_key = _tn or "(unmapped task)"
            _tid = int(_tr.get("__task_id") or 0)
            _task_total = 0.0
            for _m in _months:
                if _m < _current_ym:
                    _v = float(_monthly_by_key.get((_mcr, _task_name_key), {}).get(_m, 0.0) or 0.0)
                else:
                    _base = float(_dash_vmap.get((_tid, _m), 0.0) or 0.0)
                    _v = float(_override_map.get((_mcr, _tid, _m), _base) or 0.0)
                _task_total += _v
            _estimated_total_by_key[(_mcr, _task_name_key)] = _task_total

    # STEP 1B: Calculate MCR TOTAL by iterating through MONTHS, not tasks
    _mcr_total = 0.0
    for _m in _months:
        if _m < _current_ym:
            # PAST MONTHS: Use actual from either _monthly_by_mcr or sum of _monthly_by_key
            if _is_non_expandable:
                _m_total = float(_monthly_by_mcr.get(_mcr, {}).get(_m, 0.0) or 0.0)
            else:
                # Sum actual across ALL tasks in this MCR
                _m_total = 0.0
                for _, _tr in _task_agg.iterrows():
                    _tn = str(_tr.get("Task Name") or "").strip()
                    _task_name_key = _tn or "(unmapped task)"
                    _m_total += float(_monthly_by_key.get((_mcr, _task_name_key), {}).get(_m, 0.0) or 0.0)
        else:
            # FUTURE MONTHS: Check for MCR-level override FIRST
            if (_mcr, None, _m) in _override_map:
                _m_total = float(_override_map.get((_mcr, None, _m), 0.0) or 0.0)
            else:
                # Sum across ALL ROWS in _mdf (the MCR-filtered source dataframe)
                _m_total = 0.0
                for _, _sr in _mdf.iterrows():
                    _tid = int(_sr.get("__task_id") or 0)
                    _base_val = float(_dash_vmap.get((_tid, _m), 0.0) or 0.0)
                    _m_total += float(_override_map.get((_mcr, _tid, _m), _base_val) or 0.0)
        _mcr_total += _m_total
    _estimated_total_by_mcr[_mcr] = _mcr_total
```

### Step 2: Display MCR Row (Lines 1244-1251)
```python
_out_rows.append(
    {
        "Project MCR #": _mcr,
        "Task Name": "",
        "RG type": "",
        "Resource group": "",
        "Plan Total (€)": float(pd.to_numeric(_task_agg["Plan Total (€)"], errors="coerce").fillna(0.0).sum()),
        "Actuals (€)": float(_actual_ytd_by_mcr.get(_mcr, 0.0) or 0.0),
        "Estimated Actuals (projection)": float(_estimated_total_by_mcr.get(_mcr, 0.0) or 0.0),  # ← USES PRECALC
        "Variance (€) (Planned vs. Estimated Actuals)": float(_estimated_total_by_mcr.get(_mcr, 0.0) or 0.0) - float(pd.to_numeric(_task_agg["Plan Total (€)"], errors="coerce").fillna(0.0).sum()),
    }
)
```

### Key Characteristics of Section 1.5:
1. **MCR total = sum of months**, where each month sums:
   - **Past months**: All task actuals from `_monthly_by_key` (or `_monthly_by_mcr` if non-expandable)
   - **Future months**: All rows from `_mdf` (MCR-filtered source dataframe), applying overrides
2. **Iterations**:
   - **Past**: Sums across _task_agg (grouped tasks)
   - **Future**: Sums across _mdf (raw source rows, potentially duplicate tasks if listed multiple times)
3. **Override Precedence**: MCR-level override (\_mcr, None, \_m) takes priority over task-level

---

## SECTION 1.9/1.10: MCR EAC Calculation Logic

### Structure: Organization-centric, NOT MCR-centric
**Important**: Section 1.9/1.10 does NOT show intermediate MCR rows. Instead:
- Shows **Organization** as parent rows
- Drills down to **individual Task rows** (with MCR # as a column)
- MCR totals must be calculated by **summing Task rows with same MCR**

### Step 1: Build Task-Level EAC (Lines 1899-2035)

```python
_plan_by_key_month = {}
_task_ids_by_key = {}
_task_ids_by_mcr = {}

# Load plan values
for _t in _org_tasks:
    _tid = int(_t.get("id") or 0)
    _tname = str(_t.get("task_name") or "").strip()
    if not _tname:
        continue

    # Extract MCR from task row
    _mcr = ""
    if _org_mcr_col:
        _mcr = str(_org_tvmap.get((_tid, _org_mcr_col), "") or "").strip()
        if not _mcr:
            _mcr = str(_org_vmap.get((_tid, _org_mcr_col), "") or "").strip()
    _mcr = _canonical_project_mcr(_mcr) or "(blank)"

    _key = (_org_name, _mcr, _tname)
    _plan_by_key_month.setdefault(_key, {})
    _task_ids_by_key.setdefault(_key, set()).add(_tid)
    _task_ids_by_mcr.setdefault(_mcr, set()).add(_tid)

    for _m in _org_months:
        _amt = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
        _plan_by_key_month[_key][_m] = _plan_by_key_month[_key].get(_m, 0.0) + _amt

# Load overrides for future months
_mcr_level_override = {}
_task_override = {}
for _ov in _all_overrides:
    _ov_month = str(_ov.get("month_year") or "")
    if _ov_month not in _org_months or _ov_month < _org_current_ym:
        continue
    _ov_mcr = _canonical_project_mcr(_ov.get("mcr")) or "(blank)"
    _ov_amount = float(_ov.get("override_amount", 0.0) or 0.0)
    _ov_task_id_raw = _ov.get("task_id")
    if _ov_task_id_raw is None:
        _ov_task_id = -1
    else:
        _ov_task_id = int(_ov_task_id_raw)

    if _ov_task_id == 0 and _ov_mcr not in _mcr_has_task0:
        _ov_task_id = -1

    if _ov_task_id == -1:
        _mcr_level_override[(_ov_mcr, _ov_month)] = _ov_amount
    else:
        _task_override[(_ov_mcr, _ov_task_id, _ov_month)] = _ov_amount
```

### Step 2: Calculate Task-Level EAC (Lines 2024-2035)

```python
for _, _task_name_org_mcr_combo in _base_rows_task_combos:
    _org_name, _mcr, _task_name = _task_name_org_mcr_combo
    if _mcr in _xc_mcr_set:
        continue

    _row = {
        "Organisation": _org_name or "Unknown Org",
        "Project MCR #": _mcr or "(blank)",
        "Task Name": _task_name or "(unmapped task)",
    }

    # Build EAC for this specific task
    _eac_total = 0.0
    for _m in _org_months:
        if _m < _org_current_ym:
            # PAST MONTHS: Use actual
            _eac_m = float(_actual_by_key_month.get((_org_name, _mcr, _task_name), {}).get(_m, 0.0) or 0.0)
        else:
            # FUTURE MONTHS: Use overrides or base forecast
            _future_val = 0.0
            for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):
                _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
                _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
            _eac_m = _future_val

        _row[f"{_m} EAC (€)"] = _eac_m
        _eac_total += _eac_m

    _row["EAC Total (€)"] = _eac_total
    _base_rows.append(_row)
```

### Step 3: MCR Totals Derived from Task Sum (Lines 2095-2157)

**There is NO explicit MCR-level row in the output.** Instead, MCR totals are **implicitly calculated** by users/viewers summing task rows. 

However, when drilling down under Organization:
```python
if bool(st.session_state[_pivot_expand_key].get(_org_name, False)):
    _odf = _odf.sort_values(["Project MCR #", "Task Name"]).reset_index(drop=True)
    for _, _dr in _odf.iterrows():
        # Each row here has a Project MCR # and Task Name
        # MCR total would be SUM of all rows with same MCR #
```

### Key Characteristics of Section 1.9/1.10:
1. **MCR total = derived by user summing task rows** with the same MCR #
2. **Each task row EAC**:
   - **Past months**: Actual from `_actual_by_key_month`
   - **Future months**: Sums across ALL task IDs in `_task_ids_by_key.get((_org_name, _mcr, _task_name), set())`
3. **Override Precedence**:
   - MCR-level override (`_mcr_level_override`) only used for XC MCRs (line 2055)
   - Non-XC MCRs use task-level overrides (`_task_override`)
4. **Data Key**: `(_org_name, _mcr, _task_name)` - includes organization, adding another dimension

---

## CRITICAL DIFFERENCES FOUND

### 1. **Data Source**: Different Projects
- Section 1.5: `_dash_proj` (Dashboard project)
- Section 1.9/1.10: `_org_proj` (Organization project)

**This is the PRIMARY difference.** They may have:
- Different task mappings
- Different plan values
- Different MCR assignments

### 2. **Future Month Aggregation: Different Loop Scope**

**Section 1.5 - Future months** (Line 1186-1189):
```python
_m_total = 0.0
for _, _sr in _mdf.iterrows():  # ← Loop through MCR-filtered SOURCE dataframe
    _tid = int(_sr.get("__task_id") or 0)
    _base_val = float(_dash_vmap.get((_tid, _m), 0.0) or 0.0)
    _m_total += float(_override_map.get((_mcr, _tid, _m), _base_val) or 0.0)
```

**Section 1.9/1.10 - Future months per task** (Line 2024-2027):
```python
_future_val = 0.0
for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):  # ← Loop through task IDs for THIS SPECIFIC task-name
    _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
    _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
```

**Implication**:
- Section 1.5 loops through `_mdf` (ALL rows in source for this MCR)
- Section 1.9/1.10 loops through task IDs only for THAT SPECIFIC task-name combo
- If a task appears multiple times with different task_names, it might be counted differently

### 3. **Override Precedence Differs**

**Section 1.5** (Line 1184):
- Checks MCR-level override first: `if (_mcr, None, _m) in _override_map`
- Falls back to per-row aggregation

**Section 1.9/1.10** (For non-XC):
- Always uses task-level overrides: `_task_override.get((_mcr, _tid, _m), _base)`
- Never uses MCR-level override (except for XC MCRs line 2055)

### 4. **Task Filtering: Different Inclusion Criteria**

**Section 1.5**:
- Includes all tasks from source dataframe `_mdf` (after filtering by MCR)
- No explicit zero-value filtering

**Section 1.9/1.10**:
- Iterates through `_base_rows_task_combos` which is built from tasks in `_org_tasks`
- Skips XC MCRs (line 2002): `if _mcr in _xc_mcr_set: continue`

---

## HYPOTHESIS: Why MCR 002 & 003 Are Higher in 1.9/1.10

1. **Different project data** (_dash_proj vs _org_proj)
   - _org_proj might include additional tasks or overrides not in _dash_proj
   - Or _org_proj has different plan values per task

2. **Task-level override precedence**
   - If _org_proj has task-level overrides set to higher values
   - Section 1.9/1.10 applies them directly
   - Section 1.5 might skip them if MCR-level override exists

3. **Different task groupings**
   - _org_proj might map MCR 002 to additional tasks
   - _dash_proj might have a subset of tasks for MCR 002

---

## NEXT STEPS TO VERIFY

1. **Export MCR 002 & 003 task lists from both projects**
   - Compare which tasks are assigned to each MCR in _dash_proj vs _org_proj

2. **Check overrides for MCR 002 & 003**
   - Are there MCR-level or task-level overrides set in forecast_overrides table?
   - Do they differ between projects?

3. **Verify plan values per task**
   - Select all tasks for MCR 002 from both projects
   - Compare _dash_vmap vs _org_vmap values for those task IDs
