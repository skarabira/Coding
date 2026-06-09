# Data and Calculation Differences Analysis: Section 1.5 vs Section 1.9/1.10

## Executive Summary

The discrepancies between sections 1.5 and 1.9/1.10 are caused by **fundamental architectural differences in how the two sections derive and aggregate MCR values**. The core issue is that **the plan database contains NO MCR column values**, forcing each section to reconstruct MCRs differently:

- **Section 1.5**: Derives MCRs primarily from actual transactions and updates the MCR universe dynamically
- **Section 1.9/1.10**: Attempts to build MCR keys from plan data first, creating a key mismatch

---

## Question 1: Do they use different data sources for past vs. future months?

### ✅ YES - Section 1.5 uses `_monthly_by_key` and `_dash_vmap`; Section 1.9 uses `_actual_by_key_month` and `_org_vmap`

#### Section 1.5 Logic (Lines 1155-1200)
```python
for _m in _months:
    if _m < _current_ym:                           # PAST MONTHS
        _v = float(_monthly_by_key.get((_mcr, _task_name_key), {}).get(_m, 0.0) or 0.0)
    else:                                           # FUTURE MONTHS
        _base = float(_dash_vmap.get((_tid, _m), 0.0) or 0.0)
        _v = float(_override_map.get((_mcr, _tid, _m), _base) or 0.0)
```

- **Past months**: Uses `_monthly_by_key[(mcr, task_name)]` → actual transaction data
- **Future months**: Uses `_dash_vmap[(task_id, month)]` → plan values

#### Section 1.9/1.10 Logic (Lines 2039-2050)
```python
for _m in _org_months:
    if _m < _org_current_ym:                       # PAST MONTHS
        _eac_m = float(_actual_by_key_month.get((_org_name, _mcr, _task_name), {}).get(_m, 0.0) or 0.0)
    else:                                           # FUTURE MONTHS
        _future_val = 0.0
        for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):
            _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
            _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
```

- **Past months**: Uses `_actual_by_key_month[(org, mcr, task)]` → actual transaction data
- **Future months**: Uses `_org_vmap[(task_id, month)]` → plan values

**Key Difference**: Both use similar logic, but **Section 1.9 fails to find task IDs for actual MCRs** due to the key mismatch explained below.

---

## Question 2: Root Cause - THE KEY MISMATCH PROBLEM

### ⚠️ CRITICAL ISSUE: Plan keys use blank MCR; actual keys use specific MCRs (002, 003, 004, 005)

#### The Data Situation
```
Database Reality:
├─ Plan tasks (79 tasks)
│  └─ prj_mcr_number column: EMPTY (no values)
├─ Actuals (442 records)
│  ├─ BM-00110021_002: 137 actuals
│  ├─ BM-00110021_003: 53 actuals
│  ├─ BM-00110021_004: 123 actuals
│  └─ BM-00110021_005: 129 actuals
├─ Task Mappings: 0 entries (not being used)
└─ Overrides: 116 records with specific MCRs
```

#### Section 1.9 Key Building (Lines 1920-1946)
```python
# Plan data loop - creates keys with BLANK MCR
for _t in _org_tasks:
    _mcr = ""  # From prj_mcr_number column - EMPTY!
    _org_name = "C-Hub"
    _task_to_org.setdefault((_mcr, _tname), _org_name)
    _key = (_org_name, _mcr, _tname)  # ← Key has MCR = ""
    _task_ids_by_key.setdefault(_key, set()).add(_tid)
    _task_ids_by_mcr.setdefault(_mcr, set()).add(_tid)  # ← Blank MCR key
```

**Result**: All tasks keyed as `("C-Hub", "", task_name)`

#### Section 1.9 Actual Data Loop (Lines 1970-1984)
```python
# Actual data loop - creates keys with SPECIFIC MCRs
for _a in _org_actuals:
    _mcr_raw = canonical_project_mcr(_a.get("project_no"))  # ← Gets 002, 003, 004, 005
    _mapped_task = _org_map_exact.get((_task_raw, _mcr_raw)) or ...
    _key = (_org_name, _mcr_raw, _mapped_task)  # ← Key has MCR = 002, 003, etc.
```

**Result**: Actuals keyed as `("C-Hub", "BM-00110021_002", task_name)`

#### The Mismatch Impact (Future Months, Line 2045-2047)
```python
for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):
    # ↑ Tries to find task IDs for ("C-Hub", "BM-00110021_002", task_name)
    # But this key doesn't exist! Only ("C-Hub", "", task_name) exists
    # Result: _task_ids_by_key returns EMPTY SET
    # So loop never executes, and NO future month plan values are added!
```

---

## Question 3: Are `_dash_vmap` and `_org_vmap` the same data?

### ✅ YES - Both come from `db.get_plan_values_for_project(project_id)`

**Confirmed**: Both sections use identical underlying plan data:
- Same database table
- Same structure: `{(task_id, month): plan_value}`
- Both have ~1,171 entries
- Both represent €5,090,577 total plan across 12 months

---

## Question 4: Are there different numbers of tasks assigned to MCRs in 1.5 vs 1.9?

### ✅ YES - But indirectly, due to key mismatch

**Section 1.5 Task Assignment**:
- Builds `_src_df` from 79 plan tasks
- All tasks initially assigned to MCR "(blank)"
- MCRs then derived from:
  - `_actual_ytd_by_mcr` (current year actuals)
  - `_monthly_by_mcr` (past-month actuals for 004/005)
  - `_monthly_by_key` (past-month actuals for 002/003)

**Section 1.9 Task Assignment**:
- Builds `_plan_by_key_month` from 79 plan tasks
- All tasks assigned to key `("C-Hub", "", task_name)`
- When processing actuals with MCR 002, tries to find:
  - `_task_ids_by_key[("C-Hub", "BM-00110021_002", task_name)]`
  - **KEY NOT FOUND** → Empty set → No task IDs available for future months

---

## Question 5: Does section 1.5 filter or exclude certain tasks?

### ✅ PARTIALLY - Different filtering creates discrepancies

#### Section 1.5 Filtering (Lines 1086-1091)
```python
for _a in _dash_actuals:
    _dt = pd.to_datetime(_a.get("date"), errors="coerce")
    if _dt.date() <= _today and _dt.year == _today.year:
        _actual_ytd_by_key[_key] = ...
```
- **Filters**: Current year only (calendar year)
- **Result**: Includes YTD actuals only for `_actual_ytd_by_key`

#### Section 1.5 Past Months (Lines 1109-1133)
```python
_ym = _dt.strftime("%Y-%m")
if _ym not in _month_index or _ym >= _current_ym:
    continue
```
- **Filters**: Within project date range AND before current month
- **Stores in**: `_monthly_by_key` or `_monthly_by_mcr`

#### Section 1.9 Filtering (Lines 1960-1984)
```python
if pd.isna(_dt):
    continue
_ym = _dt.strftime("%Y-%m")
if _ym not in _org_months:
    continue
```
- **Filters**: Within project date range only
- **Result**: Includes all months' actuals without calendar-year restriction

---

## Question 6: What specific code needs to be aligned?

### 🔧 THE FIX: Section 1.9 must match Section 1.5's MCR derivation approach

#### Current Problem Flow
```
Section 1.5 (✓ Works):
  Plan tasks → MCR from actuals → Creates composite EAC for full 12 months
  
Section 1.9 (✗ Broken):
  Plan tasks (blank MCR) → Can't link to actual MCRs → Missing future months
```

#### Recommended Solution

**Option A: Adopt Section 1.5's Approach** (Simpler, Recommended)
- Section 1.9 should build `_task_ids_by_key` using MCR from actuals, not plan
- Use same MCR universe derivation as section 1.5
- This mirrors how MCRs are actually used in the business

**Option B: Fix the Key Mapping in Section 1.9**
- Add logic to map blank MCR keys to actual MCR keys when looking up task IDs
- Requires matching task names between plan and actuals
- More complex but maintains current architecture

#### Specific Code Changes Needed

**In Section 1.9 (around line 1920-1946):**

Current approach:
```python
# MCR from plan - EMPTY
_mcr = str(_org_vmap.get((_tid, _org_mcr_col), "") or "").strip()
_key = (_org_name, _mcr, _tname)  # ← Creates key with blank MCR
_task_ids_by_key.setdefault(_key, set()).add(_tid)
```

Should become:
```python
# Don't pre-compute MCR from plan
# Instead, let actual data define MCRs
# Only assign tasks to MCR when we see them in actuals
# Store task IDs separately by task name
_task_ids_by_name.setdefault(_tname, set()).add(_tid)
```

Then in future month calculation (line 2045):
```python
# Current (broken):
for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):

# Should be:
for _tid in _task_ids_by_name.get(_task_name, set()):
    # MCR doesn't affect which task IDs to use for plan lookup
    # _org_vmap is keyed only by (task_id, month)
```

---

## Summary: Calculation Differences

| Aspect | Section 1.5 | Section 1.9/1.10 | Impact |
|--------|-------------|-----------------|--------|
| **Plan MCR Column** | Empty (handled) | Empty (causes mismatch) | Can't find tasks for specific MCRs |
| **Task ID Lookup** | Derived per task/month | Key-based (broken keys) | Future months missing for 002/003 |
| **MCR Assignment** | From actuals | Attempted from plan, fails | Discrepancies in displayed totals |
| **Past Months** | `_monthly_by_key` + filter | `_actual_by_key_month` | Similar results but different keys |
| **Future Months** | Plan + overrides | (Mostly missing due to lookup failure) | Huge gap in totals |
| **YTD Filter** | Calendar year only | Project date range | Different baseline for actuals |

---

## Next Steps

1. **Immediate**: Sync project selection (already done via `st.rerun()`)
2. **High Priority**: Fix Section 1.9's task ID lookup to use task name instead of (org, mcr, task) key
3. **Testing**: Verify sections 1.5 and 1.9/1.10 produce matching MCR totals after fix
4. **Validation**: Check if task mappings database should be populated to link actual→plan tasks
5. **Documentation**: Update README.md with this architectural explanation
