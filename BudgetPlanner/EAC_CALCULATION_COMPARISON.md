# EAC Calculation Logic Comparison: Section 1.5 vs 1.9/1.10

## Executive Summary

Both sections calculate **Estimated At Completion (EAC)** using the same formula:
- **Past months** (< current month): Actual actuals from database
- **Future months** (≥ current month): Plan values with optional forecast overrides

**Critical Difference:** Section 1.9/1.10 properly filters overrides to future months only, while Section 1.5 previously loaded ALL overrides (now fixed).

---

## SECTION 1.5: "Estimated Actuals (projection)"
**Location:** Lines ~1064-1180 (Actuals by Project MCR, collapsed view)

### Data Structures Built

#### Override Map Initialization (FIXED)
**Before:**
```python
_all_overrides = db_forecast_overrides.get_all_forecast_overrides(_dash_proj["id"])
_override_map = {}
for _ov in _all_overrides:
    _ov_task_id = _ov.get("task_id")
    _ov_task_id = None if not _ov_task_id else int(_ov_task_id)
    _override_map[(_ov["mcr"], _ov_task_id, _ov["month_year"])] = float(_ov.get("override_amount", 0.0) or 0.0)
```
**Issue:** Loads overrides for ALL months, including past months.

**After (Fixed):**
```python
_all_overrides = db_forecast_overrides.get_all_forecast_overrides(_dash_proj["id"])
_override_map = {}
for _ov in _all_overrides:
    _ov_task_id = _ov.get("task_id")
    _ov_task_id = None if not _ov_task_id else int(_ov_task_id)
    _ov_month = _ov.get("month_year")
    if _ov_month and _ov_month < _current_ym:
        continue
    _override_map[(_ov["mcr"], _ov_task_id, _ov_month)] = float(_ov.get("override_amount", 0.0) or 0.0)
```
**Fix:** Now filters to only include future months (>= current month), matching Section 1.9/1.10 behavior.

### EAC Calculation Logic - Task Level (MCR 002/003)

For expandable MCRs (MCR 002, 003), section 1.5 processes each task individually:

```python
for _m in _months:
    if _m < _current_ym:
        # PAST MONTH: Use actual actuals from _monthly_by_key
        _v = float(_monthly_by_key.get((_mcr, _task_name_key), {}).get(_m, 0.0) or 0.0)
    else:
        # FUTURE MONTH: Use plan with override if available
        _base = float(_dash_vmap.get((_tid, _m), 0.0) or 0.0)
        _v = float(_override_map.get((_mcr, _tid, _m), _base) or 0.0)
    _task_total += _v
_estimated_total_by_key[(_mcr, _task_name_key)] = _task_total
```

**Key Points:**
- Uses task-level override key: `(_mcr, task_id, month)`
- For future months: checks `_override_map.get((_mcr, _tid, _m), plan_value)`
- No MCR-level override support for regular MCRs

### EAC Calculation Logic - MCR Level (MCR 002/003)

Aggregates task-level calculations to MCR total:

```python
_mcr_total = 0.0
for _m in _months:
    if _m < _current_ym:
        # PAST: Sum of task actuals
        _m_total = 0.0
        for _, _tr in _task_agg.iterrows():
            _tn = str(_tr.get("Task Name") or "").strip()
            _task_name_key = _tn or "(unmapped task)"
            _m_total += float(_monthly_by_key.get((_mcr, _task_name_key), {}).get(_m, 0.0) or 0.0)
    else:
        # FUTURE: Check for MCR-level override first, then sum tasks
        if (_mcr, None, _m) in _override_map:
            _m_total = float(_override_map.get((_mcr, None, _m), 0.0) or 0.0)
        else:
            _m_total = 0.0
            for _, _sr in _mdf.iterrows():
                _tid = int(_sr.get("__task_id") or 0)
                _base_val = float(_dash_vmap.get((_tid, _m), 0.0) or 0.0)
                _m_total += float(_override_map.get((_mcr, _tid, _m), _base_val) or 0.0)
    _mcr_total += _m_total
_estimated_total_by_mcr[_mcr] = _mcr_total
```

**Key Points:**
- For MCR-level override, checks key `(_mcr, None, month)` (not typically used for 002/003)
- Falls back to summing individual task overrides
- **Data Source:** `_override_map` with `task_id` as key component

---

## SECTIONS 1.9/1.10: "EAC Total (€)"
**Location:** Lines ~1960-2000 (Plan vs Actuals by Organization/Task)

### Override Map Initialization (Reference Implementation)

```python
_all_overrides = db_forecast_overrides.get_all_forecast_overrides(_org_proj["id"])
_mcr_has_task0 = {
    str(_mcr)
    for _mcr, _ids in _task_ids_by_mcr.items()
    if 0 in _ids
}
_mcr_level_override = {}
_task_override = {}
for _ov in _all_overrides:
    _ov_month = str(_ov.get("month_year") or "")
    if _ov_month not in _org_months or _ov_month < _org_current_ym:
        continue  # ← FILTERS TO FUTURE MONTHS
    _ov_mcr = _canonical_project_mcr(_ov.get("mcr")) or "(blank)"
    _ov_amount = float(_ov.get("override_amount", 0.0) or 0.0)
    _ov_task_id_raw = _ov.get("task_id")
    if _ov_task_id_raw is None:
        _ov_task_id = -1
    else:
        _ov_task_id = int(_ov_task_id_raw)
    # Backward compatibility: historical MCR-level rows may be persisted as 0
    if _ov_task_id == 0 and _ov_mcr not in _mcr_has_task0:
        _ov_task_id = -1

    if _ov_task_id == -1:
        _mcr_level_override[(_ov_mcr, _ov_month)] = _ov_amount
    else:
        _task_override[(_ov_mcr, _ov_task_id, _ov_month)] = _ov_amount
```

**Key Differences from Section 1.5:**
1. ✅ **Filters to future months:** `if _ov_month < _org_current_ym: continue`
2. ✅ **Splits override maps:** Creates two dictionaries:
   - `_mcr_level_override`: `{(mcr, month): amount}` for MCR-level overrides
   - `_task_override`: `{(mcr, task_id, month): amount}` for task-level overrides
3. **Task ID handling:** Converts `None` task_id to `-1` to distinguish from actual task ID 0

### EAC Calculation Logic - Task Level (MCR 002/003)

For regular MCRs (not XC), uses only task-level overrides:

```python
_eac_total = 0.0
for _m in _org_months:
    if _m < _org_current_ym:
        # PAST MONTH: Use actual actuals
        _eac_m = float(_actual_by_key_month.get((_org_name, _mcr, _task_name), {}).get(_m, 0.0) or 0.0)
    else:
        # FUTURE MONTH: Sum task-level overrides
        _future_val = 0.0
        for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):
            _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
            _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
        _eac_m = _future_val

    _row[f"{_m} EAC (€)"] = _eac_m
    _eac_total += _eac_m
```

**Key Points:**
- Uses `_task_override` map (task-level only for regular MCRs)
- No MCR-level override checking for MCR 002/003
- **Data Source:** `_task_override` with `(mcr, task_id, month)` key

### EAC Calculation Logic - MCR 004/005 Special Case (XC)

MCR 004/005 receive special handling with MCR-level override support:

```python
_xc_eac_by_month = {m: 0.0 for m in _org_months}
for _m in _org_months:
    if _m < _org_current_ym:
        _xc_eac_by_month[_m] = float(_xc_actual_by_month.get(_m, 0.0) or 0.0)
    else:
        _mcr_month_sum = 0.0
        for _xc_mcr in _xc_mcr_set:
            if (_xc_mcr, _m) in _mcr_level_override:
                # PRIORITIZE MCR-level override
                _mcr_month_sum += float(_mcr_level_override.get((_xc_mcr, _m), 0.0) or 0.0)
            else:
                # Fall back to summing tasks
                for _tid in _task_ids_by_mcr.get(_xc_mcr, set()):
                    _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
                    _mcr_month_sum += float(_task_override.get((_xc_mcr, _tid, _m), _base) or 0.0)
        _xc_eac_by_month[_m] = _mcr_month_sum
```

**Key Points:**
- Explicitly checks `_mcr_level_override` FIRST
- Falls back to task-level aggregation if no MCR override exists
- **Data Sources:** Both `_mcr_level_override` and `_task_override`

---

## KEY DIFFERENCES CAUSING MCR 002/003 DISCREPANCIES

| Aspect | Section 1.5 | Section 1.9/1.10 |
|--------|-------------|-----------------|
| **Override Filtering** | ⚠️ Was loading ALL months | ✅ Filters to future months only |
| **MCR-level Override Support** | ❌ Ignored for 002/003 | ❌ Ignored for 002/003 (only XC) |
| **Task-level Override Key** | `(mcr, task_id, month)` | `(mcr, task_id, month)` |
| **Split Override Maps** | ❌ Single `_override_map` | ✅ Two maps: `_mcr_level_override`, `_task_override` |
| **Data Aggregation Dimension** | Task + MCR only | Task + MCR + Organization |
| **MCR-level Lookup** | Checks `(mcr, None, month)` | Converts None → -1 for clarity |

### Why This Matters

1. **Section 1.5 Bug (Now Fixed):** Previously loaded overrides from ALL months. If an override existed for a past month, it remained in `_override_map` and could theoretically cause issues (though the month check in calculation loop prevented actual use).

2. **Organization Dimension:** Section 1.9 includes organization in the actual aggregation key:
   - Section 1.5: `_actual_by_key_month[(_mcr, task_name)][month]`
   - Section 1.9: `_actual_by_key_month[(_org_name, _mcr, task_name)][month]`
   
   This means if a task spans multiple organizations, section 1.5 might roll them up differently than section 1.9 sees them in detail.

3. **Override Split Logic:** Section 1.9's explicit split into `_mcr_level_override` and `_task_override` is cleaner and more maintainable, but for MCR 002/003 both approaches should yield identical results if properly filtered.

---

## FIX APPLIED

✅ **Fixed Section 1.5 override initialization** to filter overrides to future months only:

```python
_ov_month = _ov.get("month_year")
if _ov_month and _ov_month < _current_ym:
    continue
_override_map[(_ov["mcr"], _ov_task_id, _ov_month)] = float(_ov.get("override_amount", 0.0) or 0.0)
```

This ensures Section 1.5 now matches Section 1.9/1.10 behavior for override handling.

---

## Recommendations

1. **Consider splitting override maps in Section 1.5** to match the clarity of Section 1.9/1.10
2. **Add organization dimension to Section 1.5** if task-to-organization mapping matters for your use case
3. **Document MCR 004/005 special handling** more explicitly in code comments
4. **Add unit tests** comparing EAC calculations across sections for sample MCRs
