# Section 5.1 Migration Plan: Streamlit st.data_editor → AgGrid Single Table

## Executive Summary
Unify the current dual-table approach (editable grid + color-coded preview) into a single AgGrid table with native editing + per-cell styling. Estimated effort: **2-3 days development + 1 day testing**.

---

## 1. Current State Analysis

### Architecture (Before Migration)
- **Input Grid**: `st.data_editor()` (numeric-only, months editable)
- **Preview Grid**: `st.dataframe(styled)` with orange cell highlighting
- **State Management**: Versioned editor key (`forecast_editor_version_<project_id>`)
- **Data Flow**: 
  1. Load pivot + overrides
  2. Apply overrides to pivot
  3. Display editor + preview in parallel
  4. Save/revert via button logic

### Pain Points
- Visual separation: users see two tables
- State sync: revert requires version-key increment + rerun
- Per-cell styling: Streamlit styling doesn't work on editable cells (workaround: separate preview)

---

## 2. Target State: AgGrid Single Table

### Architecture (After Migration)
- **Single Grid**: AgGrid with:
  - Editable month columns (future months only)
  - Read-only metadata + actuals columns
  - Per-cell background color for overridden cells
  - Currency formatting applied at column level
  - Optional column freezing (MCR + Task Name)

### Benefits
- ✅ Single source of truth for visual state
- ✅ Immediate color feedback on edit
- ✅ Reduced rerun/state complexity
- ✅ Better UX continuity

### Downsides
- ⚠️ New library dependency (streamlit-aggrid)
- ⚠️ Slightly different data binding model
- ⚠️ Browser JavaScript layer adds complexity

---

## 3. Dependencies & Setup

### New Package Required
```
st-aggrid>=1.0.0
```

### Installation Step
```bash
pip install st-aggrid
```

### Import Changes
```python
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
```

### Compatibility
- Works with Streamlit 1.22+
- Tested with Python 3.9+
- Supported on all major browsers

---

## 4. Data Flow Changes

### Phase 1: Data Preparation (unchanged)
```
Load pivot_df → Load overrides → Build override_map → Apply overrides
```

### Phase 2: GridBuilder (NEW)
```
Convert pivot_df to AgGrid format:
  - Add columnDefs with type, editable, cellStyle configurations
  - Mark months >= current_ym as editable
  - Define custom cellStyle callback for override highlighting
  - Set formatter to EUR {:,.0f}
```

### Phase 3: Render Single Grid
```python
grid_response = AgGrid(
    pivot_df,
    gridOptions=builder.build(),
    data_return_mode=GridUpdateMode.FILTERING_AND_SORTING,
    update_mode=GridUpdateMode.VALUE_CHANGED,
    key=f"forecast_grid_{project_id}"
)
edited_df = grid_response['data']
```

### Phase 4: Change Detection & Save
```
On button click "Save Plan Overrides":
  - Compare edited_df (from AgGrid) vs pivot_df_original
  - Detect changes per cell (month >= current_ym only)
  - Upsert/delete overrides via db_forecast_overrides
  - Show success/info message (revert happens on next rerun)
```

---

## 5. Component Structure

### New Module: `grid_5_1.py` (optional, recommended)
```python
# grid_5_1.py - AgGrid builder + styling logic for section 5.1

def build_forecast_grid_options(
    pivot_df, 
    months, 
    current_ym, 
    override_map, 
    mcr_expandable
):
    """Build AgGrid GridOptions for section 5.1 table."""
    builder = GridOptionsBuilder.from_dataframe(pivot_df)
    
    # Configure columns: metadata (locked), months (editable + styled), EAC (locked)
    for col in ["Project MCR #", "Task Name", "RG type", "Resource group"]:
        builder.configure_column(col, lockPosition="left")
    
    for month in months:
        is_future = month >= current_ym
        builder.configure_column(
            month,
            type="numericColumn",
            editable=is_future,
            cellStyle=_cell_style_callback(month, override_map),
            valueFormatter="€ {:,.0f}",
        )
    
    builder.configure_column(
        "Estimated at Completion (EAC)",
        editable=False,
        cellStyle=_cell_style_callback("EAC", override_map)
    )
    
    return builder.build()

def _cell_style_callback(col, override_map):
    """Return cellStyle function that highlights overridden cells."""
    def style(params):
        row_mcr = params.get("data", {}).get("__mcr_key")
        row_task_id = params.get("data", {}).get("__task_id")
        if (row_mcr, row_task_id, col) in override_map:
            return {"backgroundColor": "#FFE6CC", "fontWeight": "bold"}
        return {}
    return style
```

### Integration in `app.py` (section 5.1)
Replace the dual-grid block with:
```python
# Build + render single grid
from grid_5_1 import build_forecast_grid_options

grid_opts = build_forecast_grid_options(pivot_df, _months, _current_ym, _override_map, _mcr_expandable)
grid_response = AgGrid(
    pivot_df.drop(columns=["__mcr_key", "__task_id"], errors="ignore"),
    gridOptions=grid_opts,
    key=f"forecast_5_1__{project_id}",
)

edited_df = pd.DataFrame(grid_response['data'])
# ... rest of save/revert logic unchanged ...
```

---

## 6. Detailed Implementation Phases

### Phase 1: Setup & Dependency Management (Day 1, 2 hours)
**Steps:**
1. Add `st-aggrid>=1.0.0` to `requirements.txt` or `environment.yml`
2. Install in dev environment
3. Create `grid_5_1.py` module with builder functions
4. Write unit test for builder (test column config generation)

**Rollback:** Delete `grid_5_1.py`, remove import from `app.py`, revert dependency files.

---

### Phase 2: Grid Rendering (Day 1, 3 hours)
**Steps:**
1. Import AgGrid components into `app.py` section 5.1
2. Replace dual-grid render block with single AgGrid call
3. Test:
   - Grid displays data correctly
   - Month columns show correct currency format
   - Editable columns are editable (future months)
   - Read-only columns are locked
4. Debug any styling/formatting issues

**Rollback:** Revert `app.py` section 5.1 to dual-grid, keep dependency installed (no harm).

---

### Phase 3: Color Coding & Styling (Day 2, 2 hours)
**Steps:**
1. Implement `_cell_style_callback()` to highlight overridden cells
2. Add column-level formatting (EUR, thousands separator)
3. Test:
   - Edit a cell → check if orange highlight appears immediately
   - Revert a cell → check if highlight disappears
   - Verify column freezing on left (MCR/Task Name stay visible when scrolling)
4. Adjust orange color (`#FFE6CC`) if needed for contrast

**Rollback:** Remove styling callbacks, leave grid functional but undecorated.

---

### Phase 4: State Management & Save Logic (Day 2, 2 hours)
**Steps:**
1. Adapt change-detection logic to AgGrid output format
   - AgGrid returns `{'data': [...], 'selected_rows': [...], ...}`
   - Extract `grid_response['data']` as edited_df
2. Update save button logic:
   - Compare edited_df vs pivot_df_original
   - Same month-guard (only save if month >= current_ym)
   - Call db_forecast_overrides upsert/delete as before
3. Update revert button logic:
   - Revert selector still lists overridden cells
   - On click, delete from DB + reload grid
4. Test:
   - Edit cell → Save → Value persists
   - Edit cell, revert from selector → Value reverts to default
   - Cascade revert: edit task cell → MCR total updates → save → MCR total persists

**Rollback:** Keep old dual-grid save/revert logic, just render with AgGrid instead.

---

### Phase 5: Integration & Testing (Day 3, 4 hours)
**Steps:**
1. Full end-to-end testing:
   - Create override on MCR total
   - Revert via button
   - Edit task cell → check MCR auto-recalc + override persistence
   - Test with expanded/collapsed MCRs
2. Edge cases:
   - Empty project (no plan tasks)
   - Large table (100+ rows) — check performance
   - Special chars in task names (quotes, emojis in task data)
3. Browser compatibility (Chrome, Firefox, Safari)
4. Mobile responsiveness (if applicable)
5. Update docstrings/comments in `grid_5_1.py` and `app.py`

**Rollback:** Revert all changes, restore dual-grid version.

---

## 7. Risk Mitigation Strategy

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|-----------|
| AgGrid rendering breaks on large tables | Medium | High | Phase 5: test with 100+ rows; add pagination if needed |
| Cell styling doesn't update on edit (stale styles) | Medium | Medium | Phase 3: test rerun behavior; may need explicit style refresh callback |
| State sync issues (edited_df doesn't match DB) | Low | High | Phase 4: add verbose logging to compare edited vs original before/after save |
| Browser compatibility issues | Low | Medium | Phase 5: test on latest Chrome, Firefox, Safari, Edge |
| Breaking change in st-aggrid future versions | Very Low | Medium | Pin version to `st-aggrid>=1.0.0,<2.0.0` in requirements |
| Users confused by new grid UX | Low | Low | Add UI help text explaining editable columns + orange = modified |

---

## 8. Rollback Path

### Quick Rollback (if major issues found)
**Time required:** 30 minutes

1. Revert `app.py` to last known good commit (dual-grid version)
2. Leave `grid_5_1.py` in place (harmless, won't be imported)
3. Remove st-aggrid from requirements (optional)
4. Restart app
5. Verify dual-grid renders correctly

### Full Rollback (if phased approach needed)
- After Phase 2 (rendering): Keep AgGrid but disable color coding (revert Phase 3 changes)
- After Phase 4: Keep AgGrid but use old save logic (revert custom change detection)
- Only after Phase 5 passes all tests → commit as final

---

## 9. Testing Checklist

### Unit Tests (Phase 1-2)
- [ ] `build_forecast_grid_options()` generates valid column defs
- [ ] Currency formatter applied to all month columns
- [ ] Editable columns marked correctly (future months only)

### Integration Tests (Phase 3-4)
- [ ] Grid renders without errors
- [ ] Editable cells accept numeric input
- [ ] Orange highlighting appears on override + persists after save
- [ ] Revert button deletes override + removes highlight
- [ ] Save button upserts only edited rows (no spurious saves)
- [ ] MCR total recalculates when task edited
- [ ] EAC column auto-updates

### E2E Tests (Phase 5)
- [ ] Create override on MCR → save → preview shows orange → revert → orange gone
- [ ] Edit task → MCR updates → save → both persist
- [ ] Expand/collapse MCR → grid re-renders → overrides still highlighted
- [ ] Large table (100+ rows) renders in <2 seconds
- [ ] Works on Chrome, Firefox, Safari
- [ ] Mobile: horizontal scroll reveals edit cells

### User Acceptance Tests
- [ ] Users understand which cells are editable (visual feedback)
- [ ] Revert is discoverable (dropdown + button)
- [ ] Performance acceptable (no lag on edit)

---

## 10. Success Criteria

✅ **Migration is successful if:**
1. Single AgGrid table renders all data from pivot_df
2. Future-month cells are editable with numeric input
3. Overridden cells show orange background
4. Save/revert buttons work without errors
5. All overrides persist to DB correctly
6. MCR totals auto-recalc on task edit
7. Page loads in <3 seconds (includes AgGrid init)
8. No Streamlit warnings/errors in console

✅ **No performance regression:**
- Same latency as dual-grid version (±200ms)
- Same memory footprint

---

## 11. Contingency: Hybrid Approach (if AgGrid too heavy)

If AgGrid causes unexpected complexity or performance issues, **fallback plan:**
- Keep native Streamlit `st.data_editor()` for editing
- Replace preview with a simple helper column: "Overridden? [Yes/No]"
- Users see status but not colored cells
- Minimal code change, easier rollback
- Trade-off: less visual elegance, but fully stable

---

## 12. Timeline Summary

| Phase | Task | Duration | Start | End |
|-------|------|----------|-------|-----|
| 1 | Setup deps + module | 2h | Day 1 | Day 1 PM |
| 2 | Grid render + basic test | 3h | Day 1 PM | Day 2 AM |
| 3 | Color coding + styling | 2h | Day 2 AM | Day 2 PM |
| 4 | Save/revert logic | 2h | Day 2 PM | Day 2 PM |
| 5 | Full E2E testing + fixes | 4h | Day 3 | Day 3 |
| **Total** | | **13h** | | |

**Calendar:** ~2.5 days elapsed time (with parallel work potential)

---

## 13. Questions to Resolve Before Starting

1. **Column Freezing:** Should Task Name freeze on left? (Recommended: yes, improves UX on wide tables)
2. **Pagination:** Should large tables paginate (e.g., 50 rows/page)? (Recommendation: no, keep all rows visible but optimize rendering)
3. **Search/Filter:** Want row-level search in AgGrid? (Nice-to-have, can defer to Phase 5.5)
4. **Mobile:** Is mobile responsiveness required? (Affects column layout strategy)

---

## Next Steps (When Ready to Implement)

1. Get approval on this plan
2. Confirm dependencies available in your Python env
3. Create feature branch: `feature/section-5.1-aggrid`
4. Start Phase 1 (setup)
5. Daily check-ins after each phase completes
6. If any blocker, immediately escalate for decision

---

**Plan Version:** 1.0  
**Last Updated:** May 29, 2026  
**Status:** Ready for approval
