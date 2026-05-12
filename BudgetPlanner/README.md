# Budget Planner

A Streamlit-based project budgeting solution for planning, actuals tracking, forecasting, labour mapping, baseline snapshots, and variance analysis.

## Purpose

This application supports end-to-end project cost management:
- Build and update annual/monthly budget plans by task.
- Track actual costs (manual and imported files).
- Map labour bookings to tasks.
- Freeze baselines (for business plan / current forecast checkpoints).
- Keep an audit trail of budget planning changes.
- Compare plan vs actual vs forecast.

## Current Core Features

### 1. Project Management
- Create, update, and delete projects.
- Project metadata: name, description, start date, end date, status.

### 2. Budget Planning
- Task-based planning table with inline editing.
- Required planning metadata:
  - Task Name
  - Cost Type (Labor cost / Direct cost)
  - Resource Group
  - Employees (for labor-related planning)
- Dynamic columns:
  - Month columns generated from project date range.
  - Custom columns can be added/removed.
- Annual distribution options:
  - Enter monthly values directly.
  - Enter annual values and distribute equally across months.
- Automatic calculation support:
  - If Rate/hour and Annual hours columns are present and filled, annual cost is auto-calculated and distributed equally across months.
- Baselines:
  - Freeze snapshots such as BP26, CF02, CF05, etc.
  - Download snapshots for manual transfer to external tools (for example MCR).
- Audit logging:
  - Tracks who changed what and when.

### 3. Actuals Management
- Manual entry of actual costs.
- File import for:
  - Cost actuals
  - Labour actuals
- Import profiles (column mapping templates).
- Raw actual entry review.

### 4. Labour Mapping
- Dedicated mapping tab for employee-to-task mapping.
- Inline mapping table edit/delete workflow.
- Mapping import support.
- Auto-apply mapping to existing unmapped labour actuals.
- Unmapped labour actuals visibility section.

### 5. Forecast and Variance
- Estimate to Complete (ETC) by category.
- EAC and variance views combining plan, actuals, and forecasts.

## Technology Stack
- Python
- Streamlit
- SQLite
- Pandas
- Plotly
- OpenPyXL

## Repository Structure
- app.py: Streamlit UI and workflow logic
- db.py: SQLite schema, migrations, and database access helpers
- budget_planner.db: Local SQLite database
- Start Budget Planner.bat: Windows launcher

## Local Setup

### Prerequisites
- Python environment (Conda environment recommended)
- Packages:
  - streamlit
  - pandas
  - plotly
  - openpyxl

### Install (example)
```powershell
pip install streamlit pandas plotly openpyxl
```

### Run
```powershell
streamlit run app.py
```

Or start via:
- Start Budget Planner.bat

## Data Model (high level)

Main tables include:
- projects
- budget_items
- actuals
- forecasts
- import_profiles
- import_runs
- labour_task_mappings
- plan_tasks
- plan_values
- plan_columns
- plan_baselines
- plan_audit_log

## Operational Notes
- Baseline transfer to external planning systems is currently manual by design.
- The app performs lightweight schema migration in db.init_db() for backward compatibility.
- The database is local SQLite; back up budget_planner.db regularly.

## Maintenance Policy

This README is the living documentation for the solution.

When new functionality is implemented, update at minimum:
1. Current Core Features
2. Repository Structure (if files/folders changed)
3. Data Model (if schema changed)
4. Operational Notes (if workflow changed)
5. Change Log

## Change Log

### 2026-04-24
- Added initial project README with architecture, setup, feature map, and maintenance policy.
- Documented Budget Planning enhancements:
  - Task-based planning with baselines and audit trail
  - Dynamic columns
  - Annual distribution support
  - Rate/Hours driven annual auto-calculation and month distribution
- Documented Actuals and labour mapping workflow updates.

## Next Documentation Updates (template)

When updating this solution, append a new entry here:

```text
### YYYY-MM-DD
- Summary of functional change
- UI changes
- Database/schema changes
- Migration or operational impacts
```
