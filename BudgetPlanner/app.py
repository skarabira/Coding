"""
app.py - Budget Management Assistant Main Application
Run with: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
import datetime
import json
import re

import db

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Budget Management Assistant",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialise DB on first run
db.init_db()
db.ensure_default_import_profiles()

# ── Sidebar navigation ───────────────────────────────────────────────────────
st.sidebar.title("💰 Budget Management Assistant")
st.sidebar.markdown("---")
page = st.sidebar.radio(
    "Navigate",
    ["🏠 Dashboard", "📁 Projects", "📋 Budget Planning", "📥 Actuals", "🔮 Forecast", "🔗 Task Mappings", "📤 Export"],
)

# Helper: project selector shown on most pages
def select_project(label="Select project", state_key="selected_project_name"):
    projects = db.get_projects()
    if not projects:
        st.warning("No projects yet. Go to **📁 Projects** to create one.")
        return None, None
    names = [p["name"] for p in projects]

    # Keep project choice stable when navigating between pages/tabs.
    if state_key not in st.session_state or st.session_state[state_key] not in names:
        st.session_state[state_key] = names[0]

    _idx = names.index(st.session_state[state_key])
    chosen = st.selectbox(label, names, index=_idx, key=f"{state_key}__widget")
    st.session_state[state_key] = chosen

    proj = next(p for p in projects if p["name"] == chosen)
    return proj["id"], proj

CATEGORIES = ["Labour", "Materials", "Travel", "External Services", "Software", "Hardware", "Overhead", "Other"]


def guess_column(df_columns, candidates):
    """Return the first matching column name using case-insensitive contains checks."""
    lowered = {str(c).strip().lower(): c for c in df_columns}
    for key in candidates:
        # exact match first
        if key in lowered:
            return lowered[key]
    for key in candidates:
        for raw in df_columns:
            if key in str(raw).strip().lower():
                return raw
    return "(none)"


def parse_amount(value):
    """Parse amounts coming from Excel/CSV with commas, spaces, or currency symbols."""
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("EUR", "").replace("€", "").replace(" ", "")
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def default_plan_column_key(column_label, explicit_key=""):
    explicit_key = str(explicit_key or "").strip()
    if explicit_key:
        base = explicit_key.lower()
    else:
        norm = " ".join(re.sub(r"[^a-z0-9]+", " ", str(column_label or "").strip().lower()).split())
        helper_map = {
            "rate hour": "rate_per_hour",
            "rate per hour": "rate_per_hour",
            "hourly rate": "rate_per_hour",
            "rate": "rate_per_hour",
            "hours annually": "hours_annually",
            "annual hours": "hours_annually",
            "hours annual": "hours_annually",
            "hours year": "hours_annually",
            "yearly hours": "hours_annually",
            "total": "total",
            "total annual": "total",
            "annual total": "total",
            "total annual cost": "total",
            "annual cost": "total",
        }
        if norm in helper_map:
            return helper_map[norm]
        base = "custom_" + re.sub(r"[^a-z0-9]+", "_", str(column_label or "").strip().lower()).strip("_")
    return re.sub(r"_+", "_", base).strip("_")


def parse_date(value):
    """Normalize various date inputs to YYYY-MM-DD; fall back to today's date."""
    if pd.isna(value):
        return str(datetime.date.today())
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return str(datetime.date.today())
    return str(ts.date())


def normalize_category(raw_value):
    """Map imported category names into the app's standard category list."""
    if pd.isna(raw_value):
        return "Other"
    text = str(raw_value).strip()
    if not text:
        return "Other"

    # direct match first
    for cat in CATEGORIES:
        if text.lower() == cat.lower():
            return cat

    t = text.lower()
    if any(k in t for k in ["labor", "labour", "person", "salary", "fte", "headcount"]):
        return "Labour"
    if any(k in t for k in ["material", "component", "part", "bom"]):
        return "Materials"
    if any(k in t for k in ["travel", "trip", "hotel", "flight"]):
        return "Travel"
    if any(k in t for k in ["service", "consult", "supplier", "subcontract"]):
        return "External Services"
    if any(k in t for k in ["software", "license", "licence", "subscription", "saas"]):
        return "Software"
    if any(k in t for k in ["hardware", "equipment", "device", "machine"]):
        return "Hardware"
    if any(k in t for k in ["overhead", "admin", "indirect"]):
        return "Overhead"
    return "Other"


def _norm_key_tokens(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).split())


def _project_mcr_preference_score(col_key, col_label):
    rank_map = {
        "custom project mcr": 6,
        "project mcr": 5,
        "project no": 4,
        "project number": 3,
        "prj mcr number": 2,
        "mcr number": 1,
    }
    nk = _norm_key_tokens(col_key)
    nl = _norm_key_tokens(col_label)
    return max(rank_map.get(nk, 0), rank_map.get(nl, 0))


def _is_project_mcr_like_column(col_meta):
    norm_key = _norm_key_tokens(col_meta.get("col_key"))
    norm_label = _norm_key_tokens(col_meta.get("col_label"))
    if _project_mcr_preference_score(col_meta.get("col_key"), col_meta.get("col_label")) > 0:
        return True
    return (
        ("mcr" in norm_key and "number" in norm_key)
        or ("mcr" in norm_label and "number" in norm_label)
        or ("project" in norm_key and "number" in norm_key)
        or ("project" in norm_label and "number" in norm_label)
        or norm_key.endswith("project no")
        or norm_label.endswith("project no")
        or ("project" in norm_key and "mcr" in norm_key)
        or ("project" in norm_label and "mcr" in norm_label)
    )


def _canonical_project_mcr(raw_value):
    txt = str(raw_value or "").strip()
    if not txt:
        return ""
    match = re.search(r"([A-Za-z]{2,}-\d+_\d+)", txt)
    return match.group(1) if match else txt


def _select_best_project_mcr_col(plan_cols, plan_tasks, text_values_map, value_map):
    candidates = [c for c in plan_cols if _is_project_mcr_like_column(c)]
    if not candidates:
        return None

    best_key = None
    best_score = None
    for idx, col in enumerate(candidates):
        col_key = col.get("col_key")
        non_empty = 0
        mcr_like = 0
        for task in plan_tasks:
            task_id = int(task["id"])
            raw = str(text_values_map.get((task_id, col_key), "") or "").strip()
            if not raw:
                raw = str(value_map.get((task_id, col_key), "") or "").strip()
            if not raw:
                continue
            non_empty += 1
            if re.search(r"([A-Za-z]{2,}-\d+_\d+)", raw):
                mcr_like += 1

        score = (
            mcr_like,
            non_empty,
            _project_mcr_preference_score(col.get("col_key"), col.get("col_label")),
            -idx,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_key = col_key

    return best_key


def render_table_view(df, key_prefix, filter_columns=None, total_columns=None, show_totals=True, hide_index=True, currency_columns=None):
    """Render dataframe with optional filters, numeric column totals, and Euro currency formatting."""
    if df is None or df.empty:
        st.info("No rows to display.")
        return

    view_df = df.copy()

    if filter_columns:
        with st.expander("Filter table", expanded=False):
            for col in filter_columns:
                if col not in view_df.columns:
                    continue
                values = sorted(view_df[col].dropna().astype(str).unique().tolist())
                if len(values) <= 1 or len(values) > 100:
                    continue
                selected = st.multiselect(
                    f"{col}",
                    values,
                    default=values,
                    key=f"{key_prefix}_filter_{col}",
                )
                view_df = view_df[view_df[col].astype(str).isin(selected)]

    if total_columns is None:
        total_columns = [c for c in view_df.columns if pd.api.types.is_numeric_dtype(view_df[c])]
    else:
        total_columns = [c for c in total_columns if c in view_df.columns]

    display_df = view_df.copy()
    if show_totals and total_columns and not view_df.empty:
        total_row = {c: "" for c in view_df.columns}
        label_col = view_df.columns[0]
        total_row[label_col] = "TOTAL"
        for c in total_columns:
            total_row[c] = pd.to_numeric(view_df[c], errors="coerce").fillna(0).sum()
        display_df = pd.concat([view_df, pd.DataFrame([total_row])], ignore_index=True)

    # Apply Euro currency formatting to specified columns
    column_config = {}
    if currency_columns:
        for col in currency_columns:
            if col in display_df.columns:
                column_config[col] = st.column_config.NumberColumn(col, format="€ %,.0f")

    st.dataframe(display_df, use_container_width=True, hide_index=hide_index, column_config=column_config if column_config else None)

# ════════════════════════════════════════════════════════════════════════════
# 1. DASHBOARD
# ════════════════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.title("🏠 1. Dashboard")
    st.caption("Reference: 1.1 Project Summary | 1.2 Budget vs Actuals vs EAC | 1.3 Monthly Actuals: Labour vs Other | 1.4 Plan vs Actual by Project No. | 1.5 Actuals by Project MCR | 1.6 Cross-check Task Names | 1.7 Financial Risks Summary | 1.8 Task Deviation Pareto | 1.9 Plan vs. Actuals by Organization | 1.10 Plan vs. Actuals by Task")
    st.markdown("Overview by project and by month, with Labour vs Other split.")

    projects = db.get_projects()
    if not projects:
        st.info("No projects yet. Head to **📁 Projects** to get started.")
    else:
        rows = []
        _missing_eac_cache_projects = []
        for p in projects:
            s = db.get_project_summary(p["id"])
            # Read exact cached total from section 5.1 display logic when available.
            eac_5_1 = db.get_section_5_1_eac_cache(p["id"])
            if eac_5_1 is None:
                _missing_eac_cache_projects.append(p["name"])
                eac_5_1 = 0.0
            rag = "🟢" if (eac_5_1 - s["actual"]) <= 0 else "🔴"
            rows.append({
                "RAG": rag,
                "Project": p["name"],
                "Status": p["status"],
                "Budget (€)": s["planned"],
                "Actuals (€)": s["actual"],
                "EAC (€)": eac_5_1,  # Use section 5.1 EAC instead of db.get_project_summary() EAC
                "Variance (€)": eac_5_1 - s["planned"],  # Positive means over budget, negative means underspend
            })

        df = pd.DataFrame(rows)
        if _missing_eac_cache_projects:
            st.warning(
                "EAC sync pending for: "
                + ", ".join(_missing_eac_cache_projects)
                + ". Open section 5.1 once to compute and sync exact EAC totals."
            )

        with st.expander("📊 1.1 Project Summary", expanded=True):
            # KPI cards
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Total Projects", len(projects))
            col2.metric("Total Budget", f"€ {df['Budget (€)'].sum():,.0f}")
            col3.metric("Total Actuals", f"€ {df['Actuals (€)'].sum():,.0f}")
            col4.metric("Total EAC", f"€ {df['EAC (€)'].sum():,.0f}")
            variance_total = df["Variance (€)"].sum()
            col5.metric(
                "Total Variance",
                f"€ {variance_total:,.0f}",
                delta=f"€ {variance_total:,.0f}",
                delta_color="inverse",
            )

            render_table_view(
                df,
                key_prefix="dashboard_projects",
                filter_columns=["Project", "Status", "RAG"],
                total_columns=["Budget (€)", "Actuals (€)", "EAC (€)", "Variance (€)"],
                show_totals=True,
                hide_index=True,
                currency_columns=["Budget (€)", "Actuals (€)", "EAC (€)", "Variance (€)"],
            )

        st.markdown("---")
        with st.expander("📉 1.2 Budget vs Actuals vs EAC", expanded=True):
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Budget", x=df["Project"], y=df["Budget (€)"], marker_color="#4C78A8"))
            fig.add_trace(go.Bar(name="Actuals", x=df["Project"], y=df["Actuals (€)"], marker_color="#F58518"))
            fig.add_trace(go.Bar(name="EAC", x=df["Project"], y=df["EAC (€)"], marker_color="#E45756"))
            fig.update_layout(barmode="group", title="Budget vs Actuals vs EAC", height=400)
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        with st.expander("📅 1.3 Monthly Actuals: Labour vs Other", expanded=True):
            monthly_rows = []
            for p in projects:
                actuals = db.get_actuals(p["id"])
                for a in actuals:
                    try:
                        month = pd.to_datetime(a.get("date"), errors="coerce")
                        if pd.isna(month):
                            continue
                        month_str = month.strftime("%Y-%m")
                    except Exception:
                        continue
                    cat = normalize_category(a.get("category", "Other"))
                    cost_type = "Labour" if cat == "Labour" else "Other"
                    monthly_rows.append({
                        "Project": p["name"],
                        "Month": month_str,
                        "Cost Type": cost_type,
                        "Amount": float(a.get("amount", 0) or 0),
                    })

            if monthly_rows:
                monthly_df = pd.DataFrame(monthly_rows)
                monthly_agg = (
                    monthly_df.groupby(["Month", "Cost Type"], as_index=False)["Amount"]
                    .sum()
                    .sort_values("Month")
                )

                labour_total = monthly_agg[monthly_agg["Cost Type"] == "Labour"]["Amount"].sum()
                other_total = monthly_agg[monthly_agg["Cost Type"] == "Other"]["Amount"].sum()
                c1, c2 = st.columns(2)
                c1.metric("Labour Actuals (all months)", f"€ {labour_total:,.0f}")
                c2.metric("Other Actuals (all months)", f"€ {other_total:,.0f}")

                fig_month = px.bar(
                    monthly_agg,
                    x="Month",
                    y="Amount",
                    color="Cost Type",
                    barmode="stack",
                    title="Monthly Actuals: Labour vs Other",
                    color_discrete_map={"Labour": "#4C78A8", "Other": "#F58518"},
                )
                _month_ticks = monthly_agg["Month"].drop_duplicates().tolist()
                fig_month.update_layout(
                    xaxis={
                        "tickmode": "array",
                        "tickvals": _month_ticks,
                        "ticktext": _month_ticks,
                        "tickangle": -45,
                    }
                )
                st.plotly_chart(fig_month, use_container_width=True)

                monthly_pivot = (
                    monthly_agg.pivot_table(index="Month", columns="Cost Type", values="Amount", aggfunc="sum", fill_value=0)
                    .reset_index()
                    .sort_values("Month")
                )
                if "Labour" not in monthly_pivot.columns:
                    monthly_pivot["Labour"] = 0.0
                if "Other" not in monthly_pivot.columns:
                    monthly_pivot["Other"] = 0.0
                monthly_pivot["Total"] = monthly_pivot["Labour"] + monthly_pivot["Other"]

                st.markdown("**1.3.1 Monthly Actuals Table (€):**")
                render_table_view(
                    monthly_pivot.rename(columns={"Labour": "Labour (€)", "Other": "Other (€)", "Total": "Total (€)"}),
                    key_prefix="dashboard_monthly",
                    filter_columns=["Month"],
                    total_columns=["Labour (€)", "Other (€)", "Total (€)"],
                    show_totals=True,
                    hide_index=True,
                    currency_columns=["Labour (€)", "Other (€)", "Total (€)"],
                )
            else:
                st.info("No actuals available yet for monthly dashboard view.")

        st.markdown("---")
        with st.expander("📈 1.4 Plan vs Actual by Project No. (monthly)", expanded=False):
            st.caption(
                "Shows monthly actuals stacked by Project No. and compares them against total monthly plan on the same axis. "
                "Project No. corresponds to the MCR identifier (for example BM-00110021_002)."
            )

            _cmp_pmap = {p["name"]: p for p in projects}
            _cmp_proj_name = st.selectbox(
                "Project",
                list(_cmp_pmap.keys()),
                key="dash_plan_actual_project_sel",
            )
            _cmp_proj = _cmp_pmap[_cmp_proj_name]

            _cmp_tasks = db.get_plan_tasks(_cmp_proj["id"])
            _cmp_actuals = db.get_actuals(_cmp_proj["id"])
            if not _cmp_tasks and not _cmp_actuals:
                st.info("No plan or actual data found for this project.")
            else:
                _cmp_vmap = db.get_plan_values_for_project(_cmp_proj["id"])
                _cmp_tvmap = db.get_plan_text_values_for_project(_cmp_proj["id"])
                _cmp_cols = db.get_plan_columns(_cmp_proj["id"])

                def _cmp_canonical_project_no(_raw):
                    _txt = _canonical_project_mcr(_raw)
                    return _txt if _txt else "Unmapped Project No."

                def _cmp_task_label(_raw):
                    _txt = str(_raw or "").strip()
                    return _txt if _txt else "(Unmapped Task)"

                _cmp_mcr_col = _select_best_project_mcr_col(
                    _cmp_cols,
                    _cmp_tasks,
                    _cmp_tvmap,
                    _cmp_vmap,
                )

                _cmp_all_tasks = sorted(
                    {
                        _cmp_task_label(_t.get("task_name")) for _t in _cmp_tasks
                    }
                    | {
                        _cmp_task_label(_a.get("task_name")) for _a in _cmp_actuals
                    }
                )
                _sel_tasks = st.multiselect(
                    "Filter Tasks",
                    _cmp_all_tasks,
                    default=_cmp_all_tasks,
                    key=f"dash_plan_actual_task_filter__{_cmp_proj['id']}",
                )

                try:
                    _cs = datetime.date.fromisoformat(_cmp_proj.get("start_date") or "")
                    _ce = datetime.date.fromisoformat(_cmp_proj.get("end_date") or "")
                except Exception:
                    _cs = datetime.date.today().replace(day=1)
                    _ce = _cs.replace(year=_cs.year + 1)

                _cmp_months = []
                _cur = _cs.replace(day=1)
                while _cur <= _ce.replace(day=1):
                    _cmp_months.append(_cur.strftime("%Y-%m"))
                    if _cur.month == 12:
                        _cur = _cur.replace(year=_cur.year + 1, month=1)
                    else:
                        _cur = _cur.replace(month=_cur.month + 1)

                _plan_rows = []
                for _t in _cmp_tasks:
                    _tid = int(_t["id"])
                    _task_name = _cmp_task_label(_t.get("task_name"))
                    _pno = ""
                    if _cmp_mcr_col:
                        _pno = str(_cmp_tvmap.get((_tid, _cmp_mcr_col), "") or "").strip()
                        if not _pno:
                            _pno = str(_cmp_vmap.get((_tid, _cmp_mcr_col), "") or "").strip()
                    _pno = _cmp_canonical_project_no(_pno)
                    for _m in _cmp_months:
                        _plan_rows.append(
                            {
                                "Task": _task_name,
                                "Task ID": _tid,
                                "Project No.": _pno,
                                "Month": _m,
                                "Plan (\u20ac)": float(_cmp_vmap.get((_tid, _m), 0.0) or 0.0),
                            }
                        )

                _act_rows = []
                for _a in _cmp_actuals:
                    _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                    if pd.isna(_dt):
                        continue
                    _m = _dt.strftime("%Y-%m")
                    if _m not in _cmp_months:
                        continue
                    _task_name = _cmp_task_label(_a.get("task_name"))
                    _pno = _cmp_canonical_project_no(_a.get("project_no"))
                    _act_rows.append(
                        {
                            "Task": _task_name,
                            "Project No.": _pno,
                            "Month": _m,
                            "Actual (\u20ac)": float(_a.get("amount", 0.0) or 0.0),
                        }
                    )

                _plan_df = pd.DataFrame(_plan_rows)
                _act_df = pd.DataFrame(_act_rows)

                if not _sel_tasks:
                    _plan_df = _plan_df.iloc[0:0].copy()
                    _act_df = _act_df.iloc[0:0].copy()
                else:
                    _sel_tasks_set = set(_sel_tasks)
                    if not _plan_df.empty:
                        _plan_df = _plan_df[_plan_df["Task"].isin(_sel_tasks_set)].copy()
                    if not _act_df.empty:
                        _act_df = _act_df[_act_df["Task"].isin(_sel_tasks_set)].copy()

                _cmp_all_pnos = sorted(
                    set(_plan_df["Project No."].astype(str).unique().tolist()) if not _plan_df.empty else set()
                    | set(_act_df["Project No."].astype(str).unique().tolist()) if not _act_df.empty else set()
                )
                _sel_pnos = st.multiselect(
                    "Filter Project No.",
                    _cmp_all_pnos,
                    default=_cmp_all_pnos,
                    key=f"dash_plan_actual_project_no_filter__{_cmp_proj['id']}",
                )
                if not _sel_pnos:
                    _plan_df = _plan_df.iloc[0:0].copy()
                    _act_df = _act_df.iloc[0:0].copy()
                else:
                    _sel_pnos_set = set(_sel_pnos)
                    if not _plan_df.empty:
                        _plan_df = _plan_df[_plan_df["Project No."].isin(_sel_pnos_set)].copy()
                    if not _act_df.empty:
                        _act_df = _act_df[_act_df["Project No."].isin(_sel_pnos_set)].copy()

                if _plan_df.empty:
                    _plan_task_agg = pd.DataFrame(columns=["Task", "Project No.", "Month", "Plan (\u20ac)"])
                else:
                    _plan_task_agg = _plan_df.groupby(["Task", "Project No.", "Month"], as_index=False)["Plan (\u20ac)"].sum()

                if _act_df.empty:
                    _act_task_agg = pd.DataFrame(columns=["Task", "Project No.", "Month", "Actual (\u20ac)"])
                else:
                    _act_task_agg = _act_df.groupby(["Task", "Project No.", "Month"], as_index=False)["Actual (\u20ac)"].sum()

                _cmp_table_df = _plan_task_agg.merge(
                    _act_task_agg,
                    on=["Task", "Project No.", "Month"],
                    how="outer",
                ).fillna(0.0)
                _cmp_df = _cmp_table_df.groupby(["Project No.", "Month"], as_index=False)[["Plan (\u20ac)", "Actual (\u20ac)"]].sum()
                if _cmp_df.empty:
                    st.info("No comparable monthly plan/actual data found for the selected task filter.")
                else:
                    _cmp_df["Variance (\u20ac)"] = _cmp_df["Plan (\u20ac)"] - _cmp_df["Actual (\u20ac)"]
                    _cmp_df = _cmp_df.sort_values(["Project No.", "Month"]).reset_index(drop=True)
                    _cmp_table_df["Variance (\u20ac)"] = _cmp_table_df["Plan (\u20ac)"] - _cmp_table_df["Actual (\u20ac)"]
                    _cmp_table_df = _cmp_table_df.sort_values(["Project No.", "Task", "Month"]).reset_index(drop=True)

                    _view_df = _cmp_df.copy()
                    _view_table_df = _cmp_table_df.copy()

                    if _view_df.empty:
                        st.info("No rows for selected Project No. filter.")
                    else:
                        _month_order = _cmp_months  # always show all project months on x-axis
                        _actual_stacked = (
                            _view_df.groupby(["Month", "Project No."], as_index=False)["Actual (\u20ac)"]
                            .sum()
                            .rename(columns={"Actual (\u20ac)": "Amount"})
                        )
                        _actual_visible_pnos = set(
                            _actual_stacked.loc[_actual_stacked["Amount"] > 0, "Project No."].astype(str).tolist()
                        )
                        _actual_stacked = _actual_stacked[
                            _actual_stacked["Project No."].astype(str).isin(_actual_visible_pnos)
                        ].copy()
                        _actual_stacked["Series"] = "Actual \u2013 " + _actual_stacked["Project No."].astype(str)

                        _plan_total = (
                            _view_df.groupby("Month", as_index=False)["Plan (\u20ac)"]
                            .sum()
                            .rename(columns={"Plan (\u20ac)": "Amount"})
                        )
                        _plan_total["Project No."] = "(total plan)"
                        _plan_total["Series"] = "Total Plan"

                        # Use full filtered actuals for monthly totals (including zero/negative corrections),
                        # not only the visibly stacked subsets.
                        _actual_total = (
                            _act_df.groupby("Month", as_index=False)["Actual (\u20ac)"]
                            .sum()
                            .rename(columns={"Actual (\u20ac)": "Actual Total (\u20ac)"})
                        )

                        _cum_df = (
                            _plan_total[["Month", "Amount"]]
                            .rename(columns={"Amount": "Plan Total (\u20ac)"})
                            .merge(_actual_total, on="Month", how="outer")
                            .fillna(0.0)
                        )
                        _cum_df["Month"] = pd.Categorical(_cum_df["Month"], categories=_month_order, ordered=True)
                        _cum_df = _cum_df.sort_values("Month").reset_index(drop=True)
                        _cum_df["Plan Cum (\u20ac)"] = _cum_df["Plan Total (\u20ac)"].cumsum()
                        _cum_df["Actual Cum (\u20ac)"] = _cum_df["Actual Total (\u20ac)"].cumsum()

                        _current_ym = datetime.date.today().strftime("%Y-%m")

                        # Build EAC monthly values using the same section 5.1 foundations:
                        # task-name mappings, special non-expandable MCR handling, and override precedence.
                        import db_task_mappings
                        import db_forecast_overrides

                        _eac_mcr_scope = {str(_m) for _m in _sel_pnos_set}
                        _mcrs_without_task_match = {"BM-00110021_004", "BM-00110021_005"}

                        _task_mappings = db_task_mappings.get_all_task_mappings(_cmp_proj["id"])
                        _task_mapping_exact = {
                            (m["actual_task_name"], m["actual_mcr"]): m["plan_task_name"]
                            for m in _task_mappings if m["actual_mcr"]
                        }
                        _task_mapping_catchall = {
                            m["actual_task_name"]: m["plan_task_name"]
                            for m in _task_mappings if not m["actual_mcr"]
                        }

                        _task_mcr_lookup = {}
                        _plan_task_ids_by_mcr = {}
                        _task_names_by_mcr = {}
                        _task_plan_by_mcr_task_rg = {}
                        _task_plan_by_task_rg = {}
                        for _t in _cmp_tasks:
                            _tid = int(_t["id"])
                            _tn = _cmp_task_label(_t.get("task_name"))
                            _rg_plan = str(_t.get("resource_group") or "").strip()
                            _mcr_val = ""
                            if _cmp_mcr_col:
                                _mcr_val = str(_cmp_tvmap.get((_tid, _cmp_mcr_col), "") or "").strip()
                                if not _mcr_val:
                                    _mcr_val = str(_cmp_vmap.get((_tid, _cmp_mcr_col), "") or "").strip()
                            _mcr_val = _cmp_canonical_project_no(_mcr_val)
                            if not _mcr_val:
                                _mcr_val = "(blank)"

                            if _tn and _tn not in _task_mcr_lookup:
                                _task_mcr_lookup[_tn] = _mcr_val
                            _plan_task_ids_by_mcr.setdefault(_mcr_val, set()).add(_tid)
                            _task_names_by_mcr.setdefault(_mcr_val, set()).add(_tn)
                            if _tn:
                                _task_plan_by_mcr_task_rg.setdefault((_mcr_val, _tn.lower(), _rg_plan.lower()), _tn)
                                _task_plan_by_task_rg.setdefault((_tn.lower(), _rg_plan.lower()), _tn)

                        _monthly_by_key = {}  # (MCR, task) -> month -> amount
                        _monthly_by_mcr = {}  # MCR -> month -> amount
                        for _a in _cmp_actuals:
                            _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                            if pd.isna(_dt):
                                continue
                            _ym = _dt.strftime("%Y-%m")
                            if _ym not in _cmp_months or _ym >= _current_ym:
                                continue

                            _amt = float(_a.get("amount", 0.0) or 0.0)
                            _task_name_raw = str(_a.get("task_name") or "").strip()
                            _rg_raw = str(_a.get("resource_group") or "").strip()
                            _mcr_from_actual = _cmp_canonical_project_no(_a.get("project_no"))
                            _task_name_key = (
                                _task_mapping_exact.get((_task_name_raw, _mcr_from_actual))
                                or _task_plan_by_mcr_task_rg.get((_mcr_from_actual, _task_name_raw.lower(), _rg_raw.lower()))
                                or _task_mapping_catchall.get(_task_name_raw)
                                or _task_plan_by_task_rg.get((_task_name_raw.lower(), _rg_raw.lower()))
                                or _task_name_raw
                                or "(unmapped task)"
                            )

                            if _mcr_from_actual in _mcrs_without_task_match:
                                if _mcr_from_actual in _eac_mcr_scope:
                                    _monthly_by_mcr.setdefault(_mcr_from_actual, {}).setdefault(_ym, 0.0)
                                    _monthly_by_mcr[_mcr_from_actual][_ym] = _monthly_by_mcr[_mcr_from_actual][_ym] + _amt
                            else:
                                _mcr_key = _mcr_from_actual or _task_mcr_lookup.get(_task_name_key, "(blank)")
                                if _mcr_key in _eac_mcr_scope:
                                    _key = (_mcr_key, _task_name_key)
                                    _monthly_by_key.setdefault(_key, {})[_ym] = _monthly_by_key.get(_key, {}).get(_ym, 0.0) + _amt

                        _all_overrides = db_forecast_overrides.get_all_forecast_overrides(_cmp_proj["id"])
                        _mcr_has_task0 = {
                            str(_mcr)
                            for _mcr, _ids in _plan_task_ids_by_mcr.items()
                            if 0 in _ids
                        }
                        _mcr_overrides = {}
                        _task_overrides = {}
                        for _ov in _all_overrides:
                            _ov_month = str(_ov.get("month_year") or "")
                            if _ov_month not in _cmp_months or _ov_month < _current_ym:
                                continue
                            _ov_mcr = _cmp_canonical_project_no(_ov.get("mcr"))
                            if _ov_mcr not in _eac_mcr_scope:
                                continue
                            _ov_amount = float(_ov.get("override_amount", 0.0) or 0.0)
                            _ov_task_id_raw = _ov.get("task_id")
                            if _ov_task_id_raw is None:
                                _ov_task_id = -1
                            else:
                                _ov_task_id = int(_ov_task_id_raw)
                            # Backward compatibility: historical MCR-level rows may be persisted as 0.
                            if _ov_task_id == 0 and _ov_mcr not in _mcr_has_task0:
                                _ov_task_id = -1
                            if _ov_task_id == -1:
                                # MCR-level overrides are only valid for non-expandable MCRs.
                                if _ov_mcr in _mcrs_without_task_match:
                                    _mcr_overrides[(_ov_mcr, _ov_month)] = _ov_amount
                            else:
                                _task_overrides[(_ov_mcr, _ov_task_id, _ov_month)] = _ov_amount

                        _eac_month_values = {}
                        for _m in _cmp_months:
                            _month_total = 0.0
                            for _mcr in _eac_mcr_scope:
                                if _m < _current_ym:
                                    if _mcr in _mcrs_without_task_match:
                                        _mcr_month_total = float(_monthly_by_mcr.get(_mcr, {}).get(_m, 0.0) or 0.0)
                                    else:
                                        _mcr_month_total = 0.0
                                        for _tn in _task_names_by_mcr.get(_mcr, set()):
                                            _mcr_month_total += float(_monthly_by_key.get((_mcr, _tn), {}).get(_m, 0.0) or 0.0)
                                else:
                                    if (_mcr, _m) in _mcr_overrides:
                                        _mcr_month_total = float(_mcr_overrides.get((_mcr, _m), 0.0) or 0.0)
                                    else:
                                        _mcr_month_total = 0.0
                                        for _tid in _plan_task_ids_by_mcr.get(_mcr, set()):
                                            _base_val = float(_cmp_vmap.get((_tid, _m), 0.0) or 0.0)
                                            _mcr_month_total += float(_task_overrides.get((_mcr, _tid, _m), _base_val) or 0.0)
                                _month_total += _mcr_month_total
                            _eac_month_values[_m] = _month_total

                        _eac_monthly = pd.DataFrame({"Month": _month_order})
                        _eac_monthly["EAC Monthly (\u20ac)"] = _eac_monthly["Month"].astype(str).map(lambda _m: _eac_month_values.get(_m, 0.0))
                        _eac_monthly["EAC Cum (\u20ac)"] = pd.to_numeric(_eac_monthly["EAC Monthly (\u20ac)"], errors="coerce").fillna(0.0).cumsum()

                        _risk_bundle = db.compute_project_financial_risk_monthly(_cmp_proj["id"], _cmp_months)
                        _risk_monthly_vals = {
                            _m: float(_risk_bundle.get("monthly_totals", {}).get(_m, 0.0) or 0.0)
                            for _m in _cmp_months
                        }
                        _risk_monthly = pd.DataFrame({"Month": _month_order})
                        _risk_monthly["Financial Risks Monthly (\u20ac)"] = _risk_monthly["Month"].astype(str).map(lambda _m: _risk_monthly_vals.get(_m, 0.0))
                        _risk_monthly["Financial Risks Cum (\u20ac)"] = pd.to_numeric(_risk_monthly["Financial Risks Monthly (\u20ac)"], errors="coerce").fillna(0.0).cumsum()

                        _chart_df = pd.concat([_actual_stacked, _plan_total], ignore_index=True)
                        _chart = px.bar(
                            _chart_df,
                            x="Month",
                            y="Amount",
                            color="Series",
                            barmode="stack",
                            category_orders={"Month": _month_order},
                            height=620,
                            title="Monthly Actuals by Project No. (stacked bars) vs Total Monthly Plan",
                        )
                        for _tr in _chart.data:
                            _tr.marker.line.width = 0
                            if _tr.name == "Total Plan":
                                _tr.offsetgroup = "plan"
                                _tr.marker.pattern.shape = "/"
                                _tr.opacity = 0.9
                            else:
                                _tr.offsetgroup = "actual"
                        _chart.add_trace(
                            go.Scatter(
                                x=_cum_df["Month"].astype(str),
                                y=_cum_df["Plan Cum (\u20ac)"],
                                mode="lines+markers",
                                name="Cumulative Plan",
                                line={"dash": "dash", "width": 2},
                                marker={"size": 6},
                            )
                        )
                        _cum_actual_df = _cum_df[_cum_df["Month"].astype(str) < _current_ym]
                        _chart.add_trace(
                            go.Scatter(
                                x=_cum_actual_df["Month"].astype(str),
                                y=_cum_actual_df["Actual Cum (\u20ac)"],
                                mode="lines+markers",
                                name="Cumulative Actual",
                                line={"dash": "solid", "width": 3, "color": "#0B6E4F"},
                                marker={"size": 6},
                            )
                        )
                        _eac_plan_months = _eac_monthly[_eac_monthly["Month"].astype(str) >= _current_ym].copy()
                        if not _eac_plan_months.empty:
                            _eac_line_x = _eac_plan_months["Month"].astype(str).tolist()
                            _eac_line_y = _eac_plan_months["EAC Cum (\u20ac)"].tolist()
                            if not _cum_actual_df.empty:
                                _anchor_x = str(_cum_actual_df.iloc[-1]["Month"])
                                _anchor_y = float(pd.to_numeric(_cum_actual_df.iloc[-1]["Actual Cum (\u20ac)"], errors="coerce") or 0.0)
                                _eac_line_x = [_anchor_x] + _eac_line_x
                                _eac_line_y = [_anchor_y] + _eac_line_y
                            _chart.add_trace(
                                go.Scatter(
                                    x=_eac_line_x,
                                    y=_eac_line_y,
                                    mode="lines+markers",
                                    name="Estimated at Completion (EAC)",
                                    line={"dash": "dash", "width": 3, "color": "#C75000"},
                                    marker={"size": 6, "symbol": "diamond-open"},
                                )
                            )

                            _risk_plan_months = _risk_monthly[_risk_monthly["Month"].astype(str) >= _current_ym].copy()
                            if not _risk_plan_months.empty:
                                _risk_line_x = _risk_plan_months["Month"].astype(str).tolist()
                                _risk_line_y = (
                                    _eac_plan_months["EAC Cum (\u20ac)"].to_numpy()
                                    + _risk_plan_months["Financial Risks Cum (\u20ac)"].to_numpy()
                                ).tolist()
                                if not _cum_actual_df.empty:
                                    _anchor_x = str(_cum_actual_df.iloc[-1]["Month"])
                                    _anchor_actual = float(pd.to_numeric(_cum_actual_df.iloc[-1]["Actual Cum (\u20ac)"], errors="coerce") or 0.0)
                                    _anchor_risk = float(pd.to_numeric(_risk_monthly.loc[_risk_monthly["Month"].astype(str) == _anchor_x, "Financial Risks Cum (\u20ac)"], errors="coerce").fillna(0.0).sum())
                                    _risk_line_x = [_anchor_x] + _risk_line_x
                                    _risk_line_y = [_anchor_actual + _anchor_risk] + _risk_line_y

                                # Keep endpoint aligned with section 1.1/5.1 cached EAC total.
                                _eac_cached_total = db.get_section_5_1_eac_cache(_cmp_proj["id"])
                                if _eac_cached_total is not None and len(_risk_line_y) > 0:
                                    _target_end = float(_eac_cached_total)
                                    _delta_end = _target_end - float(_risk_line_y[-1])
                                    if abs(_delta_end) > 0.5:
                                        _start_idx = 1 if (not _cum_actual_df.empty and len(_risk_line_y) > 1) else 0
                                        _n = len(_risk_line_y) - _start_idx
                                        if _n > 0:
                                            for _i in range(_start_idx, len(_risk_line_y)):
                                                _frac = (_i - _start_idx + 1) / _n
                                                _risk_line_y[_i] = float(_risk_line_y[_i]) + (_delta_end * _frac)
                                _chart.add_trace(
                                    go.Scatter(
                                        x=_risk_line_x,
                                        y=_risk_line_y,
                                        mode="lines+markers",
                                        name="Financial risks (EAC + risk)",
                                        line={"dash": "dot", "width": 3, "color": "#8E44AD"},
                                        marker={"size": 6, "symbol": "x-open"},
                                    )
                                )

                        _chart.update_layout(
                            legend_title_text="Series",
                            xaxis_title="Month",
                            yaxis_title="Amount (\u20ac)",
                            bargap=0.2,
                            bargroupgap=0.08,
                            xaxis={
                                "tickmode": "array",
                                "tickvals": _month_order,
                                "ticktext": _month_order,
                                "tickangle": -45,
                            },
                        )
                        _annotations = []
                        if not _cum_df.empty:
                            _plan_final = float(pd.to_numeric(_cum_df["Plan Cum (\u20ac)"].iloc[-1], errors="coerce") or 0.0)
                            _annotations.append(
                                {
                                    "x": str(_cum_df["Month"].iloc[-1]),
                                    "y": _plan_final,
                                    "text": f"{_plan_final/1_000_000:.2f}",
                                    "showarrow": True,
                                    "arrowhead": 2,
                                    "arrowsize": 1,
                                    "arrowwidth": 2,
                                    "arrowcolor": "#4C78A8",
                                    "ax": 40,
                                    "ay": -30,
                                    "font": {"size": 12, "color": "#4C78A8"},
                                    "bgcolor": "rgba(255, 255, 255, 0.8)",
                                    "bordercolor": "#4C78A8",
                                    "borderwidth": 1,
                                }
                            )
                        if _eac_line_y:
                            _eac_final = _eac_line_y[-1]
                            _eac_final_x = _eac_line_x[-1]
                            _annotations.append(
                                {
                                    "x": _eac_final_x,
                                    "y": _eac_final,
                                    "text": f"{_eac_final/1_000_000:.2f}",
                                    "showarrow": True,
                                    "arrowhead": 2,
                                    "arrowsize": 1,
                                    "arrowwidth": 2,
                                    "arrowcolor": "#C75000",
                                    "ax": 40,
                                    "ay": -30,
                                    "font": {"size": 12, "color": "#C75000"},
                                    "bgcolor": "rgba(255, 255, 255, 0.8)",
                                    "bordercolor": "#C75000",
                                    "borderwidth": 1,
                                }
                            )
                        if _risk_line_y:
                            _risk_final = _risk_line_y[-1]
                            _risk_final_x = _risk_line_x[-1]
                            _annotations.append(
                                {
                                    "x": _risk_final_x,
                                    "y": _risk_final,
                                    "text": f"{_risk_final/1_000_000:.2f}",
                                    "showarrow": True,
                                    "arrowhead": 2,
                                    "arrowsize": 1,
                                    "arrowwidth": 2,
                                    "arrowcolor": "#8E44AD",
                                    "ax": 40,
                                    "ay": -30,
                                    "font": {"size": 12, "color": "#8E44AD"},
                                    "bgcolor": "rgba(255, 255, 255, 0.8)",
                                    "bordercolor": "#8E44AD",
                                    "borderwidth": 1,
                                }
                            )
                        if _annotations:
                            _chart.update_layout(annotations=_annotations)
                        st.plotly_chart(_chart, use_container_width=True)
        st.markdown("---")
        with st.expander("🧮 1.5 Actuals by Project MCR (pivot-style)", expanded=False):
            st.caption(
                "Collapsed: one row per Project MCR with aggregated totals. "
                "Expanded: shows totals per Task Name under that Project MCR."
            )

            _pmap = {p["name"]: p for p in projects}
            if "_shared_project_name" not in st.session_state:
                st.session_state._shared_project_name = list(_pmap.keys())[0] if _pmap else None
            _proj_list = list(_pmap.keys())
            _proj_idx = _proj_list.index(st.session_state._shared_project_name) if st.session_state._shared_project_name in _proj_list else 0
            _new_sel = st.selectbox(
                "Project",
                _proj_list,
                index=_proj_idx,
                key="dash_mcr_project_sel",
            )
            if _new_sel != st.session_state._shared_project_name:
                st.session_state._shared_project_name = _new_sel
                st.rerun()
            _dash_proj_name = st.session_state._shared_project_name
            _dash_proj = _pmap[_dash_proj_name]

            _dash_tasks = db.get_plan_tasks(_dash_proj["id"])
            if not _dash_tasks:
                st.info("No budget plan tasks found for this project.")
            else:
                _dash_vmap = db.get_plan_values_for_project(_dash_proj["id"])
                _dash_tvmap = db.get_plan_text_values_for_project(_dash_proj["id"])
                _dash_cols = db.get_plan_columns(_dash_proj["id"])
                _dash_actuals = db.get_actuals(_dash_proj["id"])

                _mcr_group_col = _select_best_project_mcr_col(
                    _dash_cols,
                    _dash_tasks,
                    _dash_tvmap,
                    _dash_vmap,
                )

                _dash_col_keys = [c.get("col_key") for c in _dash_cols]
                _rg_type_col = next(
                    (
                        k for k in _dash_col_keys
                        if _norm_key_tokens(k) in {"sp rg type", "sp rg type t", "rg type"}
                        or "sp rg type" in _norm_key_tokens(k)
                    ),
                    None,
                )

                if not _mcr_group_col:
                    st.info("No Project MCR / Project No. column found for this project.")
                else:
                    try:
                        _ds = datetime.date.fromisoformat(_dash_proj.get("start_date") or "")
                        _de = datetime.date.fromisoformat(_dash_proj.get("end_date") or "")
                    except Exception:
                        _ds = datetime.date.today().replace(day=1)
                        _de = _ds.replace(year=_ds.year + 1)

                    _months = []
                    _cur = _ds.replace(day=1)
                    while _cur <= _de.replace(day=1):
                        _months.append(_cur.strftime("%Y-%m"))
                        if _cur.month == 12:
                            _cur = _cur.replace(year=_cur.year + 1, month=1)
                        else:
                            _cur = _cur.replace(month=_cur.month + 1)

                    def _canonical_mcr(_raw):
                        return _canonical_project_mcr(_raw)

                    _rows = []
                    for _t in _dash_tasks:
                        _tid = int(_t["id"])
                        _mcr_val = str(_dash_tvmap.get((_tid, _mcr_group_col), "") or "").strip()
                        if not _mcr_val:
                            _mcr_val = str(_dash_vmap.get((_tid, _mcr_group_col), "") or "").strip()
                        _mcr_val = _canonical_mcr(_mcr_val)
                        if not _mcr_val:
                            _mcr_val = "(blank)"

                        _rg_type_val = ""
                        if _rg_type_col:
                            _rg_type_val = str(_dash_tvmap.get((_tid, _rg_type_col), "") or "").strip()
                            if not _rg_type_val:
                                _rg_type_val = str(_dash_vmap.get((_tid, _rg_type_col), "") or "").strip()

                        _monthly_sum = sum(float(_dash_vmap.get((_tid, _m), 0.0) or 0.0) for _m in _months)
                        _rows.append(
                            {
                                "Project MCR #": _mcr_val,
                                "Task Name": str(_t.get("task_name") or ""),
                                "RG type": _rg_type_val,
                                "Resource group": str(_t.get("resource_group") or ""),
                                "Plan Total (€)": _monthly_sum,
                                "__task_id": _tid,
                            }
                        )

                    _src_df = pd.DataFrame(_rows)

                    _today = datetime.date.today()
                    _current_ym = _today.strftime("%Y-%m")
                    _month_index = {m: i for i, m in enumerate(_months)}

                    _task_mcr_lookup = {}
                    for _, _sr in _src_df.iterrows():
                        _tn = str(_sr.get("Task Name") or "").strip()
                        _mv = str(_sr.get("Project MCR #") or "").strip() or "(blank)"
                        if _tn and _tn not in _task_mcr_lookup:
                            _task_mcr_lookup[_tn] = _mv

                    _actual_ytd_by_key = {}
                    _actual_ytd_by_mcr = {}
                    for _a in _dash_actuals:
                        _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                        if pd.isna(_dt):
                            continue
                        _amt = float(_a.get("amount", 0.0) or 0.0)
                        _task_name_key = str(_a.get("task_name") or "").strip() or "(unmapped task)"
                        _mcr_from_actual = _canonical_mcr(_a.get("project_no"))
                        _mcr_key = _mcr_from_actual or _task_mcr_lookup.get(_task_name_key, "(blank)")
                        _key = (_mcr_key, _task_name_key)

                        if _dt.date() <= _today and _dt.year == _today.year:
                            _actual_ytd_by_key[_key] = _actual_ytd_by_key.get(_key, 0.0) + _amt
                            _actual_ytd_by_mcr[_mcr_key] = _actual_ytd_by_mcr.get(_mcr_key, 0.0) + _amt

                    # Align 1.5 "Estimated Actuals (projection)" with section 5.1
                    # (project-only EAC logic: actuals for completed months + plan/overrides for remaining months).
                    import db_task_mappings
                    import db_forecast_overrides

                    _task_mappings = db_task_mappings.get_all_task_mappings(_dash_proj["id"])
                    _task_mapping_exact = {
                        (m["actual_task_name"], m["actual_mcr"]): m["plan_task_name"]
                        for m in _task_mappings if m["actual_mcr"]
                    }
                    _task_mapping_catchall = {
                        m["actual_task_name"]: m["plan_task_name"]
                        for m in _task_mappings if not m["actual_mcr"]
                    }

                    _task_plan_by_mcr_task_rg = {}
                    _task_plan_by_task_rg = {}
                    for _, _sr in _src_df.iterrows():
                        _tn = str(_sr.get("Task Name") or "").strip()
                        _mcr_plan = str(_sr.get("Project MCR #") or "").strip() or "(blank)"
                        _rg_plan = str(_sr.get("Resource group") or "").strip()
                        if _tn:
                            _task_plan_by_mcr_task_rg.setdefault((_mcr_plan, _tn.lower(), _rg_plan.lower()), _tn)
                            _task_plan_by_task_rg.setdefault((_tn.lower(), _rg_plan.lower()), _tn)

                    _mcrs_without_task_match = {"BM-00110021_004", "BM-00110021_005"}
                    _monthly_by_key = {}
                    _monthly_by_mcr = {}
                    for _a in _dash_actuals:
                        _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                        if pd.isna(_dt):
                            continue
                        _ym = _dt.strftime("%Y-%m")
                        if _ym not in _month_index or _ym >= _current_ym:
                            continue

                        _amt = float(_a.get("amount", 0.0) or 0.0)
                        _task_name_raw = str(_a.get("task_name") or "").strip()
                        _rg_raw = str(_a.get("resource_group") or "").strip()
                        _mcr_from_actual = _canonical_mcr(_a.get("project_no"))
                        _task_name_key = (
                            _task_mapping_exact.get((_task_name_raw, _mcr_from_actual))
                            or _task_plan_by_mcr_task_rg.get((_mcr_from_actual or "(blank)", _task_name_raw.lower(), _rg_raw.lower()))
                            or _task_mapping_catchall.get(_task_name_raw)
                            or _task_plan_by_task_rg.get((_task_name_raw.lower(), _rg_raw.lower()))
                            or _task_name_raw
                            or "(unmapped task)"
                        )
                        if not _mcr_from_actual:
                            _mcr_from_actual = "(blank)"

                        if _mcr_from_actual in _mcrs_without_task_match:
                            _monthly_by_mcr.setdefault(_mcr_from_actual, {}).setdefault(_ym, 0.0)
                            _monthly_by_mcr[_mcr_from_actual][_ym] = _monthly_by_mcr[_mcr_from_actual][_ym] + _amt
                        else:
                            _k = (_mcr_from_actual, _task_name_key)
                            _monthly_by_key.setdefault(_k, {})[_ym] = _monthly_by_key.get(_k, {}).get(_ym, 0.0) + _amt

                    _all_overrides = db_forecast_overrides.get_all_forecast_overrides(_dash_proj["id"])
                    _override_map = {}
                    for _ov in _all_overrides:
                        _ov_task_id = _ov.get("task_id")
                        _ov_task_id = None if not _ov_task_id else int(_ov_task_id)
                        _ov_month = _ov.get("month_year")
                        if _ov_month and _ov_month < _current_ym:
                            continue
                        _override_map[(_ov["mcr"], _ov_task_id, _ov_month)] = float(_ov.get("override_amount", 0.0) or 0.0)

                    _estimated_total_by_key = {}
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

                        _mcr_total = 0.0
                        for _m in _months:
                            if _m < _current_ym:
                                if _is_non_expandable:
                                    _m_total = float(_monthly_by_mcr.get(_mcr, {}).get(_m, 0.0) or 0.0)
                                else:
                                    _m_total = 0.0
                                    for _, _tr in _task_agg.iterrows():
                                        _tn = str(_tr.get("Task Name") or "").strip()
                                        _task_name_key = _tn or "(unmapped task)"
                                        _m_total += float(_monthly_by_key.get((_mcr, _task_name_key), {}).get(_m, 0.0) or 0.0)
                            else:
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

                    _mcr_values = sorted(
                        set(_src_df["Project MCR #"].astype(str).unique().tolist())
                        | set(_actual_ytd_by_mcr.keys())
                        | set(_estimated_total_by_mcr.keys())
                    )
                    _mcr_expanded_key = f"dash_mcr_group_expanded__{_dash_proj['id']}"
                    if _mcr_expanded_key not in st.session_state:
                        st.session_state[_mcr_expanded_key] = {}

                    _g1, _g2, _g3, _g4 = st.columns([3, 1, 1, 3])
                    _sel_mcr = _g1.selectbox("Project MCR #", _mcr_values, key=f"{_mcr_expanded_key}__pick")
                    if _g2.button("Toggle", key=f"{_mcr_expanded_key}__toggle"):
                        _emap = st.session_state[_mcr_expanded_key]
                        _emap[_sel_mcr] = not bool(_emap.get(_sel_mcr, False))
                        st.session_state[_mcr_expanded_key] = _emap
                        st.rerun()
                    if _g3.button("Expand All", key=f"{_mcr_expanded_key}__expand_all"):
                        st.session_state[_mcr_expanded_key] = {k: True for k in _mcr_values}
                        st.rerun()
                    if _g4.button("Collapse All", key=f"{_mcr_expanded_key}__collapse_all"):
                        st.session_state[_mcr_expanded_key] = {}
                        st.rerun()

                    _out_rows = []
                    for _mcr in _mcr_values:
                        _mdf = _src_df[_src_df["Project MCR #"] == _mcr].copy()
                        _task_agg = _mdf.groupby("Task Name", dropna=False, as_index=False).agg({
                            "Plan Total (€)": "sum",
                            "RG type": lambda s: ", ".join(sorted({str(v).strip() for v in s if str(v).strip()})),
                            "Resource group": lambda s: ", ".join(sorted({str(v).strip() for v in s if str(v).strip()})),
                        }).sort_values("Task Name")

                        _task_rows = []
                        for _, _tr in _task_agg.iterrows():
                            _tn = str(_tr.get("Task Name") or "").strip()
                            _task_name_key = _tn or "(unmapped task)"
                            _key = (_mcr, _task_name_key)
                            _plan_total = float(_tr.get("Plan Total (€)") or 0.0)
                            _actuals_ytd = float(_actual_ytd_by_key.get(_key, 0.0) or 0.0)
                            _estimated_total = float(_estimated_total_by_key.get(_key, 0.0) or 0.0)
                            _variance = _estimated_total - _plan_total
                            _task_rows.append(
                                {
                                    "Project MCR #": "",
                                    "Task Name": f"↳ {_tn}",
                                    "RG type": str(_tr.get("RG type") or ""),
                                    "Resource group": str(_tr.get("Resource group") or ""),
                                    "Plan Total (€)": _plan_total,
                                    "Actuals (€)": _actuals_ytd,
                                    "Estimated Actuals (projection)": _estimated_total,
                                    "Variance (€) (Planned vs. Estimated Actuals)": _variance,
                                }
                            )

                        _out_rows.append(
                            {
                                "Project MCR #": _mcr,
                                "Task Name": "",
                                "RG type": "",
                                "Resource group": "",
                                "Plan Total (€)": float(pd.to_numeric(_task_agg["Plan Total (€)"], errors="coerce").fillna(0.0).sum()),
                                "Actuals (€)": float(_actual_ytd_by_mcr.get(_mcr, 0.0) or 0.0),
                                "Estimated Actuals (projection)": float(_estimated_total_by_mcr.get(_mcr, 0.0) or 0.0),
                                "Variance (€) (Planned vs. Estimated Actuals)": float(_estimated_total_by_mcr.get(_mcr, 0.0) or 0.0) - float(pd.to_numeric(_task_agg["Plan Total (€)"], errors="coerce").fillna(0.0).sum()),
                            }
                        )
                        if bool(st.session_state[_mcr_expanded_key].get(_mcr, False)):
                            _out_rows.extend(_task_rows)

                    _out_df = pd.DataFrame(_out_rows)[[
                        "Project MCR #",
                        "Task Name",
                        "RG type",
                        "Resource group",
                        "Plan Total (€)",
                        "Actuals (€)",
                        "Estimated Actuals (projection)",
                        "Variance (€) (Planned vs. Estimated Actuals)",
                    ]]

                    _parent_rows = _out_df[_out_df["Project MCR #"].astype(str).str.strip() != ""].copy()
                    _total_row = {
                        "Project MCR #": "TOTAL",
                        "Task Name": "",
                        "RG type": "",
                        "Resource group": "",
                        "Plan Total (€)": float(pd.to_numeric(_parent_rows["Plan Total (€)"], errors="coerce").fillna(0.0).sum()),
                        "Actuals (€)": float(pd.to_numeric(_parent_rows["Actuals (€)"], errors="coerce").fillna(0.0).sum()),
                        "Estimated Actuals (projection)": float(pd.to_numeric(_parent_rows["Estimated Actuals (projection)"], errors="coerce").fillna(0.0).sum()),
                        "Variance (€) (Planned vs. Estimated Actuals)": float(pd.to_numeric(_parent_rows["Variance (€) (Planned vs. Estimated Actuals)"], errors="coerce").fillna(0.0).sum()),
                    }
                    _out_df = pd.concat([_out_df, pd.DataFrame([_total_row])], ignore_index=True)

                    def _variance_color(_v):
                        try:
                            _fv = float(_v)
                        except Exception:
                            return ""
                        if _fv > 0:
                            return "color: #b00020; font-weight: 600"  # overspend vs plan
                        if _fv < 0:
                            return "color: #0b7a0b; font-weight: 600"  # underspend vs plan
                        return ""

                    _currency_cols = [
                        "Plan Total (€)",
                        "Actuals (€)",
                        "Estimated Actuals (projection)",
                        "Variance (€) (Planned vs. Estimated Actuals)",
                    ]
                    _styled_out_df = (
                        _out_df.style
                        .format({c: "€ {:,.0f}" for c in _currency_cols})
                        .map(_variance_color, subset=["Variance (€) (Planned vs. Estimated Actuals)"])
                    )

                    st.dataframe(
                        _styled_out_df,
                        hide_index=True,
                        use_container_width=True,
                    )

        st.markdown("---")
        with st.expander("🔀 1.6 Cross-check Task Names", expanded=False):
            st.caption(
                "Verifies that task names used in Actuals (Labour + Other) match those defined in the Budget Plan. "
                "Rows show presence per unique task name across Actuals and Plan."
            )

            _xc_pmap = {p["name"]: p for p in projects}
            _xc_proj_name = st.selectbox(
                "Project",
                list(_xc_pmap.keys()),
                key="dash_xc_project_sel",
            )
            _xc_proj = _xc_pmap[_xc_proj_name]

            _xc_plan_tasks = db.get_plan_tasks(_xc_proj["id"])
            _xc_actuals = db.get_actuals(_xc_proj["id"])

            # Plan task names (non-blank)
            _xc_plan_task_set = {
                str(t.get("task_name") or "").strip()
                for t in _xc_plan_tasks
                if str(t.get("task_name") or "").strip()
            }

            # Actuals: collect (project_no, task_name) pairs
            _xc_act_rows = []
            for _a in _xc_actuals:
                _tn = str(_a.get("task_name") or "").strip() or "(Unmapped Task)"
                _pno_raw = str(_a.get("project_no") or "").strip()
                _m = re.search(r"([A-Za-z]{2,}-\d+_\d+)", _pno_raw)
                _pno_clean = _m.group(1) if _m else (_pno_raw or "(No Project No.)")
                _xc_act_rows.append({"Project No.": _pno_clean, "task": _tn})

            _xc_act_task_set = {r["task"] for r in _xc_act_rows}

            if not _xc_plan_task_set and not _xc_act_task_set:
                st.info("No plan tasks or actuals found for this project.")
            else:
                _xc_act_df = (
                    pd.DataFrame(_xc_act_rows).drop_duplicates().sort_values(["Project No.", "task"]).reset_index(drop=True)
                    if _xc_act_rows
                    else pd.DataFrame(columns=["Project No.", "task"])
                )

                _all_task_names = sorted(_xc_act_task_set | _xc_plan_task_set)

                _xc_table_rows = []
                for _tn in _all_task_names:
                    _in_act = _tn in _xc_act_task_set
                    _in_plan = _tn in _xc_plan_task_set
                    _pnos = sorted(
                        _xc_act_df.loc[_xc_act_df["task"] == _tn, "Project No."].astype(str).unique().tolist()
                    ) if _in_act else []
                    _xc_table_rows.append({
                        "Project No.": ", ".join(_pnos) if _pnos else "—",
                        "Task Name (Actuals)": _tn if _in_act else "",
                        "Task Name (Plan)": _tn if _in_plan else "",
                        "Match": "✅ Both" if (_in_act and _in_plan) else ("⚠️ Actuals only" if _in_act else "❌ Plan only"),
                    })

                _xc_out_df = pd.DataFrame(_xc_table_rows)

                _xc_f1, _xc_f2 = st.columns(2)
                _xc_all_pnos = sorted(_xc_out_df["Project No."].astype(str).unique().tolist())
                _xc_all_matches = ["✅ Both", "⚠️ Actuals only", "❌ Plan only"]
                _xc_sel_pnos = _xc_f1.multiselect(
                    "Filter by Project No.",
                    _xc_all_pnos,
                    default=_xc_all_pnos,
                    key="dash_xc_pno_filter",
                )
                _xc_sel_match = _xc_f2.multiselect(
                    "Filter by Match status",
                    _xc_all_matches,
                    default=_xc_all_matches,
                    key="dash_xc_match_filter",
                )
                _xc_view_df = _xc_out_df.copy()
                if _xc_sel_pnos:
                    _xc_view_df = _xc_view_df[_xc_view_df["Project No."].isin(_xc_sel_pnos)]
                if _xc_sel_match:
                    _xc_view_df = _xc_view_df[_xc_view_df["Match"].isin(_xc_sel_match)]

                _xc_m1, _xc_m2, _xc_m3, _xc_m4 = st.columns(4)
                _xc_m1.metric("Total tasks", len(_xc_view_df))
                _xc_m2.metric("✅ In both", (_xc_view_df["Match"] == "✅ Both").sum())
                _xc_m3.metric("⚠️ Actuals only", (_xc_view_df["Match"] == "⚠️ Actuals only").sum())
                _xc_m4.metric("❌ Plan only", (_xc_view_df["Match"] == "❌ Plan only").sum())

                def _xc_row_style(_row):
                    _match_val = _row.get("Match", "")
                    if _match_val == "⚠️ Actuals only":
                        return ["background-color: #fff3cd"] * len(_row)
                    if _match_val == "❌ Plan only":
                        return ["background-color: #f8d7da"] * len(_row)
                    return [""] * len(_row)

                _xc_styled = (
                    _xc_view_df.reset_index(drop=True)
                    .style
                    .apply(_xc_row_style, axis=1)
                )
                st.dataframe(
                    _xc_styled,
                    hide_index=True,
                    use_container_width=True,
                )

        st.markdown("---")
        with st.expander("⚠️ 1.7 Financial Risks Summary", expanded=False):
            _risk_summary_rows = []
            for _p in projects:
                try:
                    _rs = datetime.date.fromisoformat(_p.get("start_date") or "")
                    _re = datetime.date.fromisoformat(_p.get("end_date") or "")
                except Exception:
                    _rs = datetime.date.today().replace(day=1)
                    _re = _rs

                _risk_months = []
                _r_cur = _rs.replace(day=1)
                while _r_cur <= _re.replace(day=1):
                    _risk_months.append(_r_cur.strftime("%Y-%m"))
                    if _r_cur.month == 12:
                        _r_cur = _r_cur.replace(year=_r_cur.year + 1, month=1)
                    else:
                        _r_cur = _r_cur.replace(month=_r_cur.month + 1)

                _rb = db.compute_project_financial_risk_monthly(_p["id"], _risk_months)
                _risk_rows = _rb.get("risk_rows", [])
                _risk_count = len(_risk_rows)
                _initial_total = float(sum(float(_r.get("total_initial_amount") or 0.0) for _r in _risk_rows))
                _remaining_total = float(sum(float(_r.get("remaining_amount") or 0.0) for _r in _risk_rows))
                _covered_total = float(max(0.0, _initial_total - _remaining_total))
                _coverage_pct = (_covered_total / _initial_total * 100.0) if _initial_total > 0 else 0.0
                _fully = sum(1 for _r in _risk_rows if str(_r.get("risk_status") or "") == "fully covered")
                _partial = sum(1 for _r in _risk_rows if str(_r.get("risk_status") or "") == "partially covered")
                _uncovered = sum(1 for _r in _risk_rows if str(_r.get("risk_status") or "") == "uncovered")

                _risk_summary_rows.append(
                    {
                        "Project": _p.get("name") or "",
                        "Risks": int(_risk_count),
                        "Fully covered": int(_fully),
                        "Partially covered": int(_partial),
                        "Uncovered": int(_uncovered),
                        "Total risk (€)": _initial_total,
                        "Covered risk (€)": _covered_total,
                        "Uncovered risk (€)": _remaining_total,
                        "Coverage (%)": _coverage_pct,
                    }
                )

            _risk_df = pd.DataFrame(_risk_summary_rows)
            _risk_projects_with_data = int((_risk_df["Risks"] > 0).sum()) if not _risk_df.empty else 0
            _risk_total_count = int(_risk_df["Risks"].sum()) if not _risk_df.empty else 0
            _risk_total_initial = float(_risk_df["Total risk (€)"].sum()) if not _risk_df.empty else 0.0
            _risk_total_covered = float(_risk_df["Covered risk (€)"].sum()) if not _risk_df.empty else 0.0
            _risk_total_uncovered = float(_risk_df["Uncovered risk (€)"].sum()) if not _risk_df.empty else 0.0
            _risk_total_coverage_pct = (_risk_total_covered / _risk_total_initial * 100.0) if _risk_total_initial > 0 else 0.0

            _rk1, _rk2, _rk3, _rk4, _rk5 = st.columns(5)
            _rk1.metric("Projects with risks", _risk_projects_with_data)
            _rk2.metric("Total risks", _risk_total_count)
            _rk3.metric("Total risk amount", f"€ {_risk_total_initial:,.0f}")
            _rk4.metric("Covered risk amount", f"€ {_risk_total_covered:,.0f}")
            _rk5.metric("Uncovered risk amount", f"€ {_risk_total_uncovered:,.0f}")
            st.metric("Coverage ratio", f"{_risk_total_coverage_pct:.1f}%")

            if _risk_total_count == 0:
                st.info("No financial risks registered yet.")
            else:
                _risk_total_row = {
                    "Project": "TOTAL",
                    "Risks": _risk_total_count,
                    "Fully covered": int(_risk_df["Fully covered"].sum()),
                    "Partially covered": int(_risk_df["Partially covered"].sum()),
                    "Uncovered": int(_risk_df["Uncovered"].sum()),
                    "Total risk (€)": _risk_total_initial,
                    "Covered risk (€)": _risk_total_covered,
                    "Uncovered risk (€)": _risk_total_uncovered,
                    "Coverage (%)": _risk_total_coverage_pct,
                }
                _risk_df_view = pd.concat([_risk_df, pd.DataFrame([_risk_total_row])], ignore_index=True)
                st.dataframe(
                    _risk_df_view,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Total risk (€)": st.column_config.NumberColumn(format="€ %,.2f"),
                        "Covered risk (€)": st.column_config.NumberColumn(format="€ %,.2f"),
                        "Uncovered risk (€)": st.column_config.NumberColumn(format="€ %,.2f"),
                        "Coverage (%)": st.column_config.NumberColumn(format="%.1f%%"),
                    },
                )



        st.markdown("---")
        with st.expander("📌 1.8 Task Deviation Pareto (Top 10 Over/Under)", expanded=False):
            st.caption(
                "Deviation is calculated per task as EAC (same logic as section 5.1, excluding financial risks) minus Total Plan. "
                "Charts show top overspending and top underspending tasks with Project MCR and Resource Group context."
            )

            _pareto_pmap = {p["name"]: p for p in projects}
            _pareto_proj_name = st.selectbox(
                "Project",
                list(_pareto_pmap.keys()),
                key="dash_pareto_project_sel",
            )
            _pareto_proj = _pareto_pmap[_pareto_proj_name]

            _p_tasks = db.get_plan_tasks(_pareto_proj["id"])
            if not _p_tasks:
                st.info("No budget plan tasks found for this project.")
            else:
                _p_vmap = db.get_plan_values_for_project(_pareto_proj["id"])
                _p_tvmap = db.get_plan_text_values_for_project(_pareto_proj["id"])
                _p_cols = db.get_plan_columns(_pareto_proj["id"])
                _p_actuals = db.get_actuals(_pareto_proj["id"])

                _p_mcr_col = _select_best_project_mcr_col(_p_cols, _p_tasks, _p_tvmap, _p_vmap)
                if not _p_mcr_col:
                    st.info("No Project MCR / Project No. column found for this project.")
                else:
                    try:
                        _ps = datetime.date.fromisoformat(_pareto_proj.get("start_date") or "")
                        _pe = datetime.date.fromisoformat(_pareto_proj.get("end_date") or "")
                    except Exception:
                        _ps = datetime.date.today().replace(day=1)
                        _pe = _ps

                    _p_months = []
                    _p_cur = _ps.replace(day=1)
                    while _p_cur <= _pe.replace(day=1):
                        _p_months.append(_p_cur.strftime("%Y-%m"))
                        if _p_cur.month == 12:
                            _p_cur = _p_cur.replace(year=_p_cur.year + 1, month=1)
                        else:
                            _p_cur = _p_cur.replace(month=_p_cur.month + 1)

                    _p_current_ym = datetime.date.today().strftime("%Y-%m")
                    _p_month_index = {m: i for i, m in enumerate(_p_months)}

                    _p_rows = []
                    for _t in _p_tasks:
                        _tid = int(_t["id"])
                        _mcr_val = str(_p_tvmap.get((_tid, _p_mcr_col), "") or "").strip()
                        if not _mcr_val:
                            _mcr_val = str(_p_vmap.get((_tid, _p_mcr_col), "") or "").strip()
                        _mcr_val = _canonical_project_mcr(_mcr_val) or "(blank)"
                        _p_rows.append(
                            {
                                "Project MCR #": _mcr_val,
                                "Task Name": str(_t.get("task_name") or ""),
                                "Resource group": str(_t.get("resource_group") or ""),
                                "__task_id": _tid,
                            }
                        )
                    _p_src_df = pd.DataFrame(_p_rows)

                    _p_task_mcr_lookup = {}
                    for _, _sr in _p_src_df.iterrows():
                        _tn = str(_sr.get("Task Name") or "").strip()
                        _mv = str(_sr.get("Project MCR #") or "").strip() or "(blank)"
                        if _tn and _tn not in _p_task_mcr_lookup:
                            _p_task_mcr_lookup[_tn] = _mv

                    import db_task_mappings
                    import db_forecast_overrides

                    _p_task_mappings = db_task_mappings.get_all_task_mappings(_pareto_proj["id"])
                    _p_map_exact = {
                        (m["actual_task_name"], m["actual_mcr"]): m["plan_task_name"]
                        for m in _p_task_mappings if m["actual_mcr"]
                    }
                    _p_map_catch = {
                        m["actual_task_name"]: m["plan_task_name"]
                        for m in _p_task_mappings if not m["actual_mcr"]
                    }

                    _p_task_plan_by_mcr_task_rg = {}
                    _p_task_plan_by_task_rg = {}
                    for _, _sr in _p_src_df.iterrows():
                        _tn = str(_sr.get("Task Name") or "").strip()
                        _mcr_plan = str(_sr.get("Project MCR #") or "").strip() or "(blank)"
                        _rg_plan = str(_sr.get("Resource group") or "").strip()
                        if _tn:
                            _p_task_plan_by_mcr_task_rg.setdefault((_mcr_plan, _tn.lower(), _rg_plan.lower()), _tn)
                            _p_task_plan_by_task_rg.setdefault((_tn.lower(), _rg_plan.lower()), _tn)

                    _p_mcrs_without_task_match = {"BM-00110021_004", "BM-00110021_005"}
                    _p_monthly_by_key = {}
                    _p_monthly_by_mcr = {}
                    for _a in _p_actuals:
                        _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                        if pd.isna(_dt):
                            continue
                        _ym = _dt.strftime("%Y-%m")
                        if _ym not in _p_month_index or _ym >= _p_current_ym:
                            continue

                        _amt = float(_a.get("amount", 0.0) or 0.0)
                        _task_raw = str(_a.get("task_name") or "").strip()
                        _rg_raw = str(_a.get("resource_group") or "").strip()
                        _mcr_actual = _canonical_project_mcr(_a.get("project_no"))
                        _task_key = (
                            _p_map_exact.get((_task_raw, _mcr_actual))
                            or _p_task_plan_by_mcr_task_rg.get(((_mcr_actual or "(blank)"), _task_raw.lower(), _rg_raw.lower()))
                            or _p_map_catch.get(_task_raw)
                            or _p_task_plan_by_task_rg.get((_task_raw.lower(), _rg_raw.lower()))
                            or _task_raw
                            or "(unmapped task)"
                        )
                        _mcr_actual = _mcr_actual or _p_task_mcr_lookup.get(_task_key, "(blank)") or "(blank)"

                        if _mcr_actual in _p_mcrs_without_task_match:
                            _p_monthly_by_mcr.setdefault(_mcr_actual, {}).setdefault(_ym, 0.0)
                            _p_monthly_by_mcr[_mcr_actual][_ym] = _p_monthly_by_mcr[_mcr_actual][_ym] + _amt
                        else:
                            _k = (_mcr_actual, _task_key)
                            _p_monthly_by_key.setdefault(_k, {})[_ym] = _p_monthly_by_key.get(_k, {}).get(_ym, 0.0) + _amt

                    _p_override_map = {}
                    for _ov in db_forecast_overrides.get_all_forecast_overrides(_pareto_proj["id"]):
                        _ov_tid = _ov.get("task_id")
                        _ov_tid = None if not _ov_tid else int(_ov_tid)
                        _p_override_map[(_ov["mcr"], _ov_tid, _ov["month_year"])] = float(_ov.get("override_amount", 0.0) or 0.0)

                    def _p_mcr_month_total(_mcr, _m, _mdf, _task_agg, _non_expandable):
                        if _m < _p_current_ym:
                            if _non_expandable:
                                return float(_p_monthly_by_mcr.get(_mcr, {}).get(_m, 0.0) or 0.0)
                            _sum_val = 0.0
                            for _, _tr in _task_agg.iterrows():
                                _tn = str(_tr.get("Task Name") or "").strip() or "(unmapped task)"
                                _sum_val += float(_p_monthly_by_key.get((_mcr, _tn), {}).get(_m, 0.0) or 0.0)
                            return _sum_val

                        if (_mcr, None, _m) in _p_override_map:
                            return float(_p_override_map.get((_mcr, None, _m), 0.0) or 0.0)

                        _sum_val = 0.0
                        for _, _sr in _mdf.iterrows():
                            _tid = int(_sr.get("__task_id") or 0)
                            _base = float(_p_vmap.get((_tid, _m), 0.0) or 0.0)
                            _sum_val += float(_p_override_map.get((_mcr, _tid, _m), _base) or 0.0)
                        return _sum_val

                    _task_rows = []
                    _p_mcr_universe = sorted(
                        set(_p_src_df["Project MCR #"].astype(str).unique().tolist())
                        | set(_p_monthly_by_mcr.keys())
                        | {k[0] for k in _p_monthly_by_key.keys()}
                    )

                    for _mcr in _p_mcr_universe:
                        _mdf = _p_src_df[_p_src_df["Project MCR #"] == _mcr].copy()
                        if _mdf.empty:
                            continue
                        _task_agg = _mdf.groupby("Task Name", dropna=False, as_index=False).agg(
                            {
                                "Resource group": lambda s: ", ".join(sorted({str(v).strip() for v in s if str(v).strip()})),
                                "__task_id": "first",
                            }
                        ).sort_values("Task Name")

                        _has_mcr_level_override = any(
                            (_mcr, None, _m) in _p_override_map for _m in _p_months if _m >= _p_current_ym
                        )
                        _aggregate_mode = (_mcr in _p_mcrs_without_task_match) or _has_mcr_level_override

                        if _aggregate_mode:
                            _mcr_eac = 0.0
                            for _m in _p_months:
                                _mcr_eac += _p_mcr_month_total(_mcr, _m, _mdf, _task_agg, _mcr in _p_mcrs_without_task_match)

                            _mcr_plan = 0.0
                            for _, _sr in _mdf.iterrows():
                                _tid = int(_sr.get("__task_id") or 0)
                                for _m in _p_months:
                                    _mcr_plan += float(_p_vmap.get((_tid, _m), 0.0) or 0.0)

                            _task_rows.append(
                                {
                                    "Task": "(MCR-level aggregate)",
                                    "Project MCR #": _mcr,
                                    "Resource group": "(multiple)",
                                    "Plan (€)": _mcr_plan,
                                    "EAC (€)": _mcr_eac,
                                    "Deviation (€)": _mcr_eac - _mcr_plan,
                                }
                            )
                        else:
                            for _, _tr in _task_agg.iterrows():
                                _tn = str(_tr.get("Task Name") or "").strip() or "(unmapped task)"
                                _tid = int(_tr.get("__task_id") or 0)
                                _rg = str(_tr.get("Resource group") or "").strip() or "(blank)"

                                _task_plan = 0.0
                                _task_eac = 0.0
                                for _m in _p_months:
                                    _base = float(_p_vmap.get((_tid, _m), 0.0) or 0.0)
                                    _task_plan += _base
                                    if _m < _p_current_ym:
                                        _task_eac += float(_p_monthly_by_key.get((_mcr, _tn), {}).get(_m, 0.0) or 0.0)
                                    else:
                                        _task_eac += float(_p_override_map.get((_mcr, _tid, _m), _base) or 0.0)

                                _task_rows.append(
                                    {
                                        "Task": _tn,
                                        "Project MCR #": _mcr,
                                        "Resource group": _rg,
                                        "Plan (€)": _task_plan,
                                        "EAC (€)": _task_eac,
                                        "Deviation (€)": _task_eac - _task_plan,
                                    }
                                )

                    _pareto_df = pd.DataFrame(_task_rows)
                    if _pareto_df.empty:
                        st.info("No task-level rows available for Pareto analysis.")
                    else:
                        _over_df = _pareto_df[_pareto_df["Deviation (€)"] > 0].copy().sort_values("Deviation (€)", ascending=False).head(10)
                        _under_df = _pareto_df[_pareto_df["Deviation (€)"] < 0].copy()
                        _under_df["Savings (€)"] = -_under_df["Deviation (€)"]
                        _under_df = _under_df.sort_values("Savings (€)", ascending=False).head(10)

                        def _build_pareto(_df_in, _value_col, _title, _bar_color):
                            if _df_in.empty:
                                return None
                            _d = _df_in.reset_index(drop=True).copy()
                            _d["Rank"] = _d.index + 1
                            _d["Task Label"] = _d.apply(lambda r: f"{int(r['Rank'])}. {str(r['Task'])}", axis=1)
                            _fig = go.Figure()
                            _fig.add_trace(
                                go.Bar(
                                    x=_d["Task Label"],
                                    y=_d[_value_col],
                                    name="Deviation",
                                    marker_color=_bar_color,
                                    customdata=_d[["Project MCR #", "Resource group", "Task"]],
                                    hovertemplate=(
                                        "Task: %{customdata[2]}<br>"
                                        "Project MCR #: %{customdata[0]}<br>"
                                        "Resource group: %{customdata[1]}<br>"
                                        f"{_value_col}: € %{{y:,.0f}}<extra></extra>"
                                    ),
                                ),
                            )
                            _fig.update_layout(
                                title=_title,
                                height=480,
                                xaxis={"tickangle": -30},
                                legend={"orientation": "h", "y": 1.08},
                            )
                            _fig.update_yaxes(title_text="Amount (€)")
                            return _fig

                        _c1, _c2 = st.columns(2)
                        _fig_over = _build_pareto(
                            _over_df,
                            "Deviation (€)",
                            "Top 10 Overspending Tasks (EAC - Plan)",
                            "#D62728",
                        )
                        if _fig_over is None:
                            _c1.info("No overspending tasks found.")
                        else:
                            _c1.plotly_chart(_fig_over, use_container_width=True)

                        _fig_under = _build_pareto(
                            _under_df,
                            "Savings (€)",
                            "Top 10 Underspending Tasks (Plan - EAC)",
                            "#2E8B57",
                        )
                        if _fig_under is None:
                            _c2.info("No underspending tasks found.")
                        else:
                            _c2.plotly_chart(_fig_under, use_container_width=True)

                        st.markdown("**Top 10 Overspending Tasks (with context):**")
                        if _over_df.empty:
                            st.info("No overspending tasks found.")
                        else:
                            st.dataframe(
                                _over_df[["Task", "Project MCR #", "Resource group", "Plan (€)", "EAC (€)", "Deviation (€)"]],
                                hide_index=True,
                                use_container_width=True,
                                column_config={
                                    "Plan (€)": st.column_config.NumberColumn(format="€ %,.0f"),
                                    "EAC (€)": st.column_config.NumberColumn(format="€ %,.0f"),
                                    "Deviation (€)": st.column_config.NumberColumn(format="€ %,.0f"),
                                },
                            )

                        st.markdown("**Top 10 Underspending Tasks (with context):**")
                        if _under_df.empty:
                            st.info("No underspending tasks found.")
                        else:
                            st.dataframe(
                                _under_df[["Task", "Project MCR #", "Resource group", "Plan (€)", "EAC (€)", "Deviation (€)", "Savings (€)"]],
                                hide_index=True,
                                use_container_width=True,
                                column_config={
                                    "Plan (€)": st.column_config.NumberColumn(format="€ %,.0f"),
                                    "EAC (€)": st.column_config.NumberColumn(format="€ %,.0f"),
                                    "Deviation (€)": st.column_config.NumberColumn(format="€ %,.0f"),
                                    "Savings (€)": st.column_config.NumberColumn(format="€ %,.0f"),
                                },
                            )

        st.markdown("---")
        with st.expander("🏢 1.9 Plan vs. Actuals (monthly) by Organization", expanded=False):
            st.caption(
                "Pivot-style monthly comparison of Plan vs Actual grouped by Organisation. "
                "Top level shows Organisation totals; expand to see Project MCR # and Task details."
            )

            _org_pmap = {p["name"]: p for p in projects}
            if "_shared_project_name" not in st.session_state:
                st.session_state._shared_project_name = list(_org_pmap.keys())[0] if _org_pmap else None
            _org_proj_list = list(_org_pmap.keys())
            _org_proj_idx = _org_proj_list.index(st.session_state._shared_project_name) if st.session_state._shared_project_name in _org_proj_list else 0
            _new_org_sel = st.selectbox(
                "Project",
                _org_proj_list,
                index=_org_proj_idx,
                key="dash_org_plan_actual_project_sel",
            )
            if _new_org_sel != st.session_state._shared_project_name:
                st.session_state._shared_project_name = _new_org_sel
                st.rerun()
            _org_proj_name = st.session_state._shared_project_name
            _org_proj = _org_pmap[_org_proj_name]

            _org_tasks = db.get_plan_tasks(_org_proj["id"])
            _org_actuals = db.get_actuals(_org_proj["id"])
            if not _org_tasks and not _org_actuals:
                st.info("No plan or actual data found for this project.")
            else:
                _org_vmap = db.get_plan_values_for_project(_org_proj["id"])
                _org_tvmap = db.get_plan_text_values_for_project(_org_proj["id"])
                _org_cols = db.get_plan_columns(_org_proj["id"])
                _org_mcr_col = _select_best_project_mcr_col(_org_cols, _org_tasks, _org_tvmap, _org_vmap)

                def _org_norm_token(_v):
                    return str(_v or "").strip().lower().replace("_", " ")

                _org_col_key = None
                for _cm in _org_cols:
                    _k_norm = _org_norm_token(_cm.get("col_key"))
                    _l_norm = _org_norm_token(_cm.get("col_label"))
                    if (
                        "organisation" in _k_norm
                        or "organization" in _k_norm
                        or "organisation" in _l_norm
                        or "organization" in _l_norm
                    ):
                        _org_col_key = _cm.get("col_key")
                        break

                try:
                    _org_s = datetime.date.fromisoformat(_org_proj.get("start_date") or "")
                    _org_e = datetime.date.fromisoformat(_org_proj.get("end_date") or "")
                except Exception:
                    _org_s = datetime.date.today().replace(day=1)
                    _org_e = _org_s

                _org_months = []
                _org_cur = _org_s.replace(day=1)
                while _org_cur <= _org_e.replace(day=1):
                    _org_months.append(_org_cur.strftime("%Y-%m"))
                    if _org_cur.month == 12:
                        _org_cur = _org_cur.replace(year=_org_cur.year + 1, month=1)
                    else:
                        _org_cur = _org_cur.replace(month=_org_cur.month + 1)

                _org_current_ym = datetime.date.today().strftime("%Y-%m")

                import db_task_mappings
                import db_forecast_overrides

                # Calculate financial risks for this project
                _org_financial_risk_bundle = db.compute_project_financial_risk_monthly(_org_proj["id"], _org_months)
                _org_financial_risk_by_month = _org_financial_risk_bundle.get("monthly_totals", {})

                _org_task_mappings = db_task_mappings.get_all_task_mappings(_org_proj["id"])
                _org_map_exact = {
                    (m["actual_task_name"], m["actual_mcr"]): m["plan_task_name"]
                    for m in _org_task_mappings if m["actual_mcr"]
                }
                _org_map_catch = {
                    m["actual_task_name"]: m["plan_task_name"]
                    for m in _org_task_mappings if not m["actual_mcr"]
                }
                _xc_org_name = "XC"
                _xc_mcr_set = {"BM-00110021_004", "BM-00110021_005"}

                _task_to_org = {}
                _task_to_org_rg = {}
                _plan_by_key_month = {}
                _xc_plan_by_month = {m: 0.0 for m in _org_months}
                _task_ids_by_key = {}
                _task_ids_by_mcr = {}
                _plan_task_case_map = {}
                for _t in _org_tasks:
                    _tid = int(_t.get("id") or 0)
                    _tname = str(_t.get("task_name") or "").strip()
                    if not _tname:
                        continue
                    _plan_task_case_map.setdefault(_tname.lower(), _tname)

                    _mcr = ""
                    if _org_mcr_col:
                        _mcr = str(_org_tvmap.get((_tid, _org_mcr_col), "") or "").strip()
                        if not _mcr:
                            _mcr = str(_org_vmap.get((_tid, _org_mcr_col), "") or "").strip()
                    _mcr = _canonical_project_mcr(_mcr) or "(blank)"

                    _org_name = ""
                    if _org_col_key:
                        _org_name = str(_org_tvmap.get((_tid, _org_col_key), "") or "").strip()
                        if not _org_name:
                            _org_name = str(_org_vmap.get((_tid, _org_col_key), "") or "").strip()
                    _org_name = _org_name or "Unknown Org"
                    _rg_plan = str(_t.get("resource_group") or "").strip()

                    _task_to_org.setdefault((_mcr, _tname), _org_name)
                    _task_to_org.setdefault((None, _tname), _org_name)
                    _task_to_org_rg.setdefault((_mcr, _tname, _rg_plan), _org_name)
                    _task_to_org_rg.setdefault((None, _tname, _rg_plan), _org_name)

                    _key = (_org_name, _mcr, _tname)
                    _plan_by_key_month.setdefault(_key, {})
                    _task_ids_by_key.setdefault(_key, set()).add(_tid)
                    _task_ids_by_mcr.setdefault(_mcr, set()).add(_tid)

                    for _m in _org_months:
                        _amt = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
                        if _mcr in _xc_mcr_set:
                            _xc_plan_by_month[_m] = float(_xc_plan_by_month.get(_m, 0.0) or 0.0) + _amt
                        _plan_by_key_month[_key][_m] = _plan_by_key_month[_key].get(_m, 0.0) + _amt

                _actual_by_key_month = {}
                _xc_actual_by_month = {m: 0.0 for m in _org_months}
                for _a in _org_actuals:
                    _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                    if pd.isna(_dt):
                        continue
                    _ym = _dt.strftime("%Y-%m")
                    if _ym not in _org_months:
                        continue

                    _amt = float(_a.get("amount", 0.0) or 0.0)
                    _task_raw = str(_a.get("task_name") or "").strip()
                    _rg_raw = str(_a.get("resource_group") or "").strip()
                    _mcr_raw = _canonical_project_mcr(_a.get("project_no")) or "(blank)"

                    # XC special case: actuals are only meaningful at aggregated MCR level
                    # and should be shown at Organisation level only.
                    if _mcr_raw in _xc_mcr_set:
                        _xc_actual_by_month[_ym] = float(_xc_actual_by_month.get(_ym, 0.0) or 0.0) + _amt
                        continue

                    _mapped_task = (
                        _org_map_exact.get((_task_raw, _mcr_raw))
                        or _org_map_catch.get(_task_raw)
                        or _task_raw
                        or "(unmapped task)"
                    )
                    _mapped_task = _plan_task_case_map.get(str(_mapped_task).strip().lower(), _mapped_task)
                    _org_name = (
                        _task_to_org_rg.get((_mcr_raw, _mapped_task, _rg_raw))
                        or _task_to_org_rg.get((None, _mapped_task, _rg_raw))
                        or _task_to_org.get((_mcr_raw, _mapped_task))
                        or _task_to_org.get((None, _mapped_task))
                        or "Unknown Org"
                    )
                    _key = (_org_name, _mcr_raw, _mapped_task)

                    _actual_by_key_month.setdefault(_key, {})
                    _actual_by_key_month[_key][_ym] = _actual_by_key_month[_key].get(_ym, 0.0) + _amt

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
                        continue
                    _ov_mcr = _canonical_project_mcr(_ov.get("mcr")) or "(blank)"
                    _ov_amount = float(_ov.get("override_amount", 0.0) or 0.0)
                    _ov_task_id_raw = _ov.get("task_id")
                    if _ov_task_id_raw is None:
                        _ov_task_id = -1
                    else:
                        _ov_task_id = int(_ov_task_id_raw)
                    # Backward compatibility: historical MCR-level rows may be persisted as 0.
                    if _ov_task_id == 0 and _ov_mcr not in _mcr_has_task0:
                        _ov_task_id = -1

                    if _ov_task_id == -1:
                        _mcr_level_override[(_ov_mcr, _ov_month)] = _ov_amount
                    else:
                        _task_override[(_ov_mcr, _ov_task_id, _ov_month)] = _ov_amount

                _all_keys = sorted(set(_plan_by_key_month.keys()) | set(_actual_by_key_month.keys()))
                if not _all_keys:
                    st.info("No grouped monthly rows found for Organization view.")
                else:
                    _base_rows = []
                    for _org_name, _mcr, _task_name in _all_keys:
                        # MCR 004/005 are represented only by the synthetic XC aggregate row.
                        if _mcr in _xc_mcr_set:
                            continue
                        _row = {
                            "Organisation": _org_name or "Unknown Org",
                            "Project MCR #": _mcr or "(blank)",
                            "Task Name": _task_name or "(unmapped task)",
                        }
                        _plan_total = 0.0
                        _plan_ytd = 0.0
                        _actual_total = 0.0
                        for _m in _org_months:
                            _plan_val = float(_plan_by_key_month.get((_org_name, _mcr, _task_name), {}).get(_m, 0.0) or 0.0)
                            _actual_val = float(_actual_by_key_month.get((_org_name, _mcr, _task_name), {}).get(_m, 0.0) or 0.0)
                            _row[f"{_m} Plan (€)"] = _plan_val
                            _row[f"{_m} Actual (€)"] = _actual_val
                            _plan_total += _plan_val
                            _actual_total += _actual_val
                            if _m < _org_current_ym:
                                _plan_ytd += _plan_val

                        _eac_total = 0.0
                        for _m in _org_months:
                            if _m < _org_current_ym:
                                _eac_m = float(_actual_by_key_month.get((_org_name, _mcr, _task_name), {}).get(_m, 0.0) or 0.0)
                            else:
                                _future_val = 0.0
                                for _tid in _task_ids_by_key.get((_org_name, _mcr, _task_name), set()):
                                    _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
                                    _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
                                _eac_m = _future_val

                            _row[f"{_m} EAC (€)"] = _eac_m
                            _eac_total += _eac_m

                        _row["Plan Total (€)"] = _plan_total
                        _row["Plan YTD (€)"] = _plan_ytd
                        _row["Actual Total (€)"] = _actual_total
                        _row["EAC Total (€)"] = _eac_total
                        _row["Delta YTD (€)"] = _actual_total - _plan_ytd
                        _row["Delta (EAC vs. Plan)"] = _eac_total - _plan_total
                        _base_rows.append(_row)

                    _base_df = pd.DataFrame(_base_rows)
                    _org_values = sorted(_base_df["Organisation"].astype(str).unique().tolist())
                    if (any(abs(float(v or 0.0)) > 0.0 for v in _xc_actual_by_month.values()) or any(abs(float(v or 0.0)) > 0.0 for v in _xc_plan_by_month.values())) and _xc_org_name not in _org_values:
                        _org_values.append(_xc_org_name)
                        _org_values = sorted(_org_values)

                    _xc_eac_by_month = {m: 0.0 for m in _org_months}
                    for _m in _org_months:
                        if _m < _org_current_ym:
                            _xc_eac_by_month[_m] = float(_xc_actual_by_month.get(_m, 0.0) or 0.0)
                        else:
                            _mcr_month_sum = 0.0
                            for _xc_mcr in _xc_mcr_set:
                                if (_xc_mcr, _m) in _mcr_level_override:
                                    _mcr_month_sum += float(_mcr_level_override.get((_xc_mcr, _m), 0.0) or 0.0)
                                else:
                                    for _tid in _task_ids_by_mcr.get(_xc_mcr, set()):
                                        _base = float(_org_vmap.get((_tid, _m), 0.0) or 0.0)
                                        _mcr_month_sum += float(_task_override.get((_xc_mcr, _tid, _m), _base) or 0.0)
                            _xc_eac_by_month[_m] = _mcr_month_sum

                    _filter_orgs = _org_values
                    _sel_orgs = st.multiselect(
                        "Filter Organisation",
                        _filter_orgs,
                        default=_filter_orgs,
                        key=f"dash_org_plan_actual_filter__{_org_proj['id']}",
                    )
                    _show_monthly_cols = st.toggle(
                        "Show monthly comparison columns",
                        value=True,
                        key=f"dash_org_plan_actual_show_months__{_org_proj['id']}",
                    )
                    _delta_basis = st.radio(
                        "Delta basis",
                        ["Act vs. Plan", "EAC vs. Plan"],
                        horizontal=True,
                        key=f"dash_org_plan_actual_delta_basis__{_org_proj['id']}",
                    )
                    _view_mode = st.radio(
                        "View mode",
                        ["Table + Charts", "Table only", "Charts only"],
                        horizontal=True,
                        key=f"dash_org_plan_actual_view_mode__{_org_proj['id']}",
                    )

                    _pivot_expand_key = f"dash_org_group_expanded__{_org_proj['id']}"
                    if _pivot_expand_key not in st.session_state:
                        st.session_state[_pivot_expand_key] = {}

                    _visible_orgs = [o for o in _org_values if o in set(_sel_orgs)] if _sel_orgs else []
                    if not _visible_orgs:
                        st.info("No rows for selected Organisation filter.")
                    else:
                        _out_rows = []
                        _parent_visual_rows = []
                        _value_cols = []
                        if _show_monthly_cols:
                            for _m in _org_months:
                                _value_cols.extend([f"{_m} Plan (€)", f"{_m} Actual (€)"])
                        _value_cols.extend(["Plan Total (€)", "Plan YTD (€)", "Actual Total (€)", "EAC Total (€)", "Delta YTD (€)", "Delta (EAC vs. Plan)"])

                        for _org_name in _visible_orgs:
                            _odf = _base_df[_base_df["Organisation"].astype(str) == str(_org_name)].copy()
                            _parent = {
                                "Organisation": _org_name,
                                "Project MCR #": "",
                                "Task Name": "",
                            }

                            if str(_org_name).strip().upper() == _xc_org_name:
                                for _m in _org_months:
                                    _plan_col = f"{_m} Plan (€)"
                                    _actual_col = f"{_m} Actual (€)"
                                    _plan_val = float(_xc_plan_by_month.get(_m, 0.0) or 0.0)
                                    _actual_val = float(_xc_actual_by_month.get(_m, 0.0) or 0.0)
                                    if _show_monthly_cols:
                                        _parent[_plan_col] = _plan_val
                                        _parent[_actual_col] = _actual_val

                                _plan_total_xc = float(sum(float(v or 0.0) for v in _xc_plan_by_month.values()))
                                _plan_ytd_xc = float(sum(float(_xc_plan_by_month.get(_m, 0.0) or 0.0) for _m in _org_months if _m < _org_current_ym))
                                _actual_total_xc = float(sum(float(v or 0.0) for v in _xc_actual_by_month.values()))
                                _eac_total_xc = float(sum(float(v or 0.0) for v in _xc_eac_by_month.values()))
                                _parent["Plan Total (€)"] = _plan_total_xc
                                _parent["Plan YTD (€)"] = _plan_ytd_xc
                                _parent["Actual Total (€)"] = _actual_total_xc
                                _parent["EAC Total (€)"] = _eac_total_xc
                                _parent["Delta YTD (€)"] = _actual_total_xc - _plan_ytd_xc
                                _parent["Delta (EAC vs. Plan)"] = _eac_total_xc - _plan_total_xc
                            else:
                                for _c in _value_cols:
                                    _parent[_c] = float(pd.to_numeric(_odf[_c], errors="coerce").fillna(0.0).sum()) if _c in _odf.columns else 0.0
                            _parent_visual_rows.append(dict(_parent))
                            _out_rows.append(_parent)

                            # XC is organization-level only (no drill down).
                            if str(_org_name).strip().upper() == _xc_org_name:
                                continue

                            if bool(st.session_state[_pivot_expand_key].get(_org_name, False)):
                                _odf = _odf.sort_values(["Project MCR #", "Task Name"]).reset_index(drop=True)
                                for _, _dr in _odf.iterrows():
                                    _child = {
                                        "Organisation": "",
                                        "Project MCR #": str(_dr.get("Project MCR #") or ""),
                                        "Task Name": str(_dr.get("Task Name") or "").strip(),
                                    }
                                    for _c in _value_cols:
                                        _child[_c] = float(_dr.get(_c, 0.0) or 0.0)
                                    _out_rows.append(_child)

                    _parent_visual_df = pd.DataFrame(_parent_visual_rows)
                    _delta_col_active = "Delta YTD (€)" if _delta_basis == "Act vs. Plan" else "Delta (EAC vs. Plan)"

                    if _view_mode != "Table only" and not _parent_visual_df.empty:
                        _k1, _k2, _k3, _k4 = st.columns(4)
                        _k1.metric("Total Plan", f"€ {float(pd.to_numeric(_parent_visual_df['Plan Total (€)'], errors='coerce').fillna(0.0).sum()):,.0f}")
                        _k2.metric("Total Actual", f"€ {float(pd.to_numeric(_parent_visual_df['Actual Total (€)'], errors='coerce').fillna(0.0).sum()):,.0f}")
                        _total_eac = float(pd.to_numeric(_parent_visual_df['EAC Total (€)'], errors='coerce').fillna(0.0).sum())
                        _total_financial_risk = sum(float(v or 0.0) for v in _org_financial_risk_by_month.values())
                        _total_eac_with_risk = _total_eac + _total_financial_risk
                        _k3.metric("Total EAC (+ risks)", f"€ {_total_eac_with_risk:,.0f}")
                        _k4.metric("Net Delta", f"€ {float(pd.to_numeric(_parent_visual_df[_delta_col_active], errors='coerce').fillna(0.0).sum()):,.0f}")

                        _rank_df = _parent_visual_df[["Organisation", "Plan Total (€)", "Plan YTD (€)", "Actual Total (€)", "EAC Total (€)", "Delta YTD (€)", "Delta (EAC vs. Plan)"]].copy()
                        _rank_df["Sign"] = _rank_df[_delta_col_active].apply(lambda v: "Overspending" if float(v or 0.0) > 0 else ("Saving" if float(v or 0.0) < 0 else "Neutral"))
                        _rank_df = _rank_df.sort_values(_delta_col_active, ascending=False)

                        _vc1, _vc2 = st.columns(2)
                        _rank_fig = px.bar(
                            _rank_df,
                            x=_delta_col_active,
                            y="Organisation",
                            orientation="h",
                            color="Sign",
                            color_discrete_map={"Overspending": "#b00020", "Saving": "#0b7a0b", "Neutral": "#7f7f7f"},
                            title=f"Organisation Ranking by {_delta_col_active}",
                            height=420,
                        )
                        _rank_fig.update_layout(yaxis={"categoryorder": "total ascending"})
                        _vc1.plotly_chart(_rank_fig, use_container_width=True)

                        _quad_df = _rank_df.copy()
                        _quad_df["Bubble Size"] = pd.to_numeric(_quad_df["EAC Total (€)"], errors="coerce").fillna(0.0).abs()
                        _quad_fig = px.scatter(
                            _quad_df,
                            x="Delta YTD (€)",
                            y="Delta (EAC vs. Plan)",
                            size="Bubble Size",
                            color="Organisation",
                            text="Organisation",
                            title="Risk Quadrant: Delta YTD vs Delta (EAC vs. Plan)",
                            height=420,
                        )
                        _x_vals = pd.to_numeric(_quad_df["Delta YTD (€)"], errors="coerce").fillna(0.0)
                        _y_vals = pd.to_numeric(_quad_df["Delta (EAC vs. Plan)"], errors="coerce").fillna(0.0)
                        _x_abs_max = float(max(abs(_x_vals.min()), abs(_x_vals.max()), 1.0))
                        _y_abs_max = float(max(abs(_y_vals.min()), abs(_y_vals.max()), 1.0))
                        _x_pad = _x_abs_max * 0.1
                        _y_pad = _y_abs_max * 0.1
                        _quad_fig.update_xaxes(range=[-_x_abs_max - _x_pad, _x_abs_max + _x_pad], zeroline=True, zerolinecolor="#666")
                        _quad_fig.update_yaxes(range=[-_y_abs_max - _y_pad, _y_abs_max + _y_pad], zeroline=True, zerolinecolor="#666")
                        _quad_fig.add_vline(x=0, line_dash="dash", line_color="#666")
                        _quad_fig.add_hline(y=0, line_dash="dash", line_color="#666")
                        _qx = _x_abs_max * 0.6
                        _qy = _y_abs_max * 0.6
                        _quad_fig.add_annotation(x=_qx, y=_qy, text="Q1 (+YTD, +EAC)<br>highest risk", showarrow=False, font={"size": 11, "color": "#8b0000"})
                        _quad_fig.add_annotation(x=-_qx, y=_qy, text="Q2 (-YTD, +EAC)<br>emerging future risk", showarrow=False, font={"size": 11, "color": "#8b5a00"})
                        _quad_fig.add_annotation(x=-_qx, y=-_qy, text="Q3 (-YTD, -EAC)<br>savings/opportunity", showarrow=False, font={"size": 11, "color": "#0b7a0b"})
                        _quad_fig.add_annotation(x=_qx, y=-_qy, text="Q4 (+YTD, -EAC)<br>recovery trajectory", showarrow=False, font={"size": 11, "color": "#0b4f8a"})
                        _quad_fig.update_traces(textposition="top center")
                        _vc2.plotly_chart(_quad_fig, use_container_width=True)

                        _trend_org = st.selectbox(
                            "Trend Organisation",
                            _visible_orgs,
                            key=f"dash_org_plan_actual_trend_sel__{_org_proj['id']}",
                        )
                        _trend_rows = []
                        for _m in _org_months:
                            if str(_trend_org).strip().upper() == _xc_org_name:
                                _pval = float(_xc_plan_by_month.get(_m, 0.0) or 0.0)
                                _aval = float(_xc_actual_by_month.get(_m, 0.0) or 0.0)
                                _eval = float(_xc_eac_by_month.get(_m, 0.0) or 0.0)
                            else:
                                _org_slice = _base_df[_base_df["Organisation"].astype(str) == str(_trend_org)]
                                _pval = float(pd.to_numeric(_org_slice.get(f"{_m} Plan (€)", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
                                _aval = float(pd.to_numeric(_org_slice.get(f"{_m} Actual (€)", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
                                _eval = float(pd.to_numeric(_org_slice.get(f"{_m} EAC (€)", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
                            _trend_rows.append({"Month": _m, "Plan (€)": _pval, "Actual (€)": _aval, "EAC (€)": _eval})

                        _trend_df = pd.DataFrame(_trend_rows)
                        if not _trend_df.empty:
                            _trend_df["Plan Cum (€)"] = pd.to_numeric(_trend_df["Plan (€)"], errors="coerce").fillna(0.0).cumsum()
                            _trend_df["Actual Cum (€)"] = pd.to_numeric(_trend_df["Actual (€)"], errors="coerce").fillna(0.0).cumsum()
                            _trend_df["EAC Cum (€)"] = pd.to_numeric(_trend_df["EAC (€)"], errors="coerce").fillna(0.0).cumsum()
                            
                            # Build trend chart with Actual Cum stopping at last actual month
                            _actual_vals = pd.to_numeric(_trend_df["Actual (€)"], errors="coerce").fillna(0.0)
                            _has_actual_mask = _actual_vals != 0
                            
                            # Find last month with actual data
                            _last_actual_idx = -1
                            if _has_actual_mask.any():
                                _last_actual_idx = len(_trend_df) - 1 - _has_actual_mask[::-1].argmax()
                            
                            # Build long format with filtering applied
                            _trend_long_rows = []
                            for _i, _row in _trend_df.iterrows():
                                _trend_long_rows.append({"Month": _row["Month"], "Series": "Plan Cum (€)", "Amount": _row["Plan Cum (€)"]})
                                _trend_long_rows.append({"Month": _row["Month"], "Series": "EAC Cum (€)", "Amount": _row["EAC Cum (€)"]})
                                # Only include Actual Cum up to last actual month
                                if _i <= _last_actual_idx:
                                    _trend_long_rows.append({"Month": _row["Month"], "Series": "Actual Cum (€)", "Amount": _row["Actual Cum (€)"]})
                            
                            _trend_long = pd.DataFrame(_trend_long_rows)
                            _trend_fig = px.line(
                                _trend_long,
                                x="Month",
                                y="Amount",
                                color="Series",
                                markers=True,
                                title=f"Cumulative Plan vs Actual vs EAC - {_trend_org}",
                                height=420,
                            )
                            st.plotly_chart(_trend_fig, use_container_width=True)

                    if _view_mode != "Charts only":
                        _g1, _g2, _g3, _g4 = st.columns([3, 1, 1, 3])
                        _pick_org = _g1.selectbox("Organisation", _visible_orgs, key=f"{_pivot_expand_key}__pick")
                        if _g2.button("Toggle", key=f"{_pivot_expand_key}__toggle"):
                            _emap = st.session_state[_pivot_expand_key]
                            _emap[_pick_org] = not bool(_emap.get(_pick_org, False))
                            st.session_state[_pivot_expand_key] = _emap
                            st.rerun()
                        if _g3.button("Expand All", key=f"{_pivot_expand_key}__expand_all"):
                            st.session_state[_pivot_expand_key] = {k: True for k in _visible_orgs}
                            st.rerun()
                        if _g4.button("Collapse All", key=f"{_pivot_expand_key}__collapse_all"):
                            st.session_state[_pivot_expand_key] = {}
                            st.rerun()

                    _out_df = pd.DataFrame(_out_rows)

                    _parent_rows = _out_df[_out_df["Organisation"].astype(str).str.strip() != ""].copy()
                    _total_row = {
                        "Organisation": "TOTAL",
                        "Project MCR #": "",
                        "Task Name": "",
                    }
                    for _c in _value_cols:
                        _total_row[_c] = float(pd.to_numeric(_parent_rows[_c], errors="coerce").fillna(0.0).sum()) if _c in _parent_rows.columns else 0.0
                    _out_df = pd.concat([_out_df, pd.DataFrame([_total_row])], ignore_index=True)

                    def _delta_color_ytd(_v):
                        try:
                            _fv = float(_v)
                        except Exception:
                            return ""
                        if _fv > 0:
                            return "color: #b00020; font-weight: 600"
                        if _fv < 0:
                            return "color: #0b7a0b; font-weight: 600"
                        return ""

                    def _delta_color_eac(_v):
                        try:
                            _fv = float(_v)
                        except Exception:
                            return ""
                        if _fv > 0:
                            return "color: #b00020; font-weight: 600"
                        if _fv < 0:
                            return "color: #0b7a0b; font-weight: 600"
                        return ""

                    _styled_out_df = (
                        _out_df.style
                        .format({c: "€ {:,.0f}" for c in _value_cols})
                        .map(_delta_color_ytd, subset=["Delta YTD (€)"])
                        .map(_delta_color_eac, subset=["Delta (EAC vs. Plan)"])
                    )

                    if _view_mode != "Charts only":
                        st.dataframe(
                            _styled_out_df,
                            hide_index=True,
                            use_container_width=True,
                        )

        st.markdown("---")
        with st.expander("🧩 1.10 Plan vs. Actuals (monthly) by Task", expanded=False):
            st.caption(
                "Pivot-style monthly comparison of Plan vs Actual grouped by Task (Task + Project MCR #). "
                "Top level shows Task totals for each MCR; expand to see Organisation details."
            )

            _task_pmap = {p["name"]: p for p in projects}
            if "_shared_project_name" not in st.session_state:
                st.session_state._shared_project_name = list(_task_pmap.keys())[0] if _task_pmap else None
            _task_proj_list = list(_task_pmap.keys())
            _task_proj_idx = _task_proj_list.index(st.session_state._shared_project_name) if st.session_state._shared_project_name in _task_proj_list else 0
            _new_task_sel = st.selectbox(
                "Project",
                _task_proj_list,
                index=_task_proj_idx,
                key="dash_task_plan_actual_project_sel",
            )
            if _new_task_sel != st.session_state._shared_project_name:
                st.session_state._shared_project_name = _new_task_sel
                st.rerun()
            _task_proj_name = st.session_state._shared_project_name
            _task_proj = _task_pmap[_task_proj_name]

            _task_tasks = db.get_plan_tasks(_task_proj["id"])
            _task_actuals = db.get_actuals(_task_proj["id"])
            if not _task_tasks and not _task_actuals:
                st.info("No plan or actual data found for this project.")
            else:
                _task_vmap = db.get_plan_values_for_project(_task_proj["id"])
                _task_tvmap = db.get_plan_text_values_for_project(_task_proj["id"])
                _task_cols = db.get_plan_columns(_task_proj["id"])
                _task_mcr_col = _select_best_project_mcr_col(_task_cols, _task_tasks, _task_tvmap, _task_vmap)

                def _task_norm_token(_v):
                    return str(_v or "").strip().lower().replace("_", " ")

                _task_org_col_key = None
                for _cm in _task_cols:
                    _k_norm = _task_norm_token(_cm.get("col_key"))
                    _l_norm = _task_norm_token(_cm.get("col_label"))
                    if (
                        "organisation" in _k_norm
                        or "organization" in _k_norm
                        or "organisation" in _l_norm
                        or "organization" in _l_norm
                    ):
                        _task_org_col_key = _cm.get("col_key")
                        break

                try:
                    _task_s = datetime.date.fromisoformat(_task_proj.get("start_date") or "")
                    _task_e = datetime.date.fromisoformat(_task_proj.get("end_date") or "")
                except Exception:
                    _task_s = datetime.date.today().replace(day=1)
                    _task_e = _task_s

                _task_months = []
                _task_cur = _task_s.replace(day=1)
                while _task_cur <= _task_e.replace(day=1):
                    _task_months.append(_task_cur.strftime("%Y-%m"))
                    if _task_cur.month == 12:
                        _task_cur = _task_cur.replace(year=_task_cur.year + 1, month=1)
                    else:
                        _task_cur = _task_cur.replace(month=_task_cur.month + 1)

                _task_current_ym = datetime.date.today().strftime("%Y-%m")

                import db_task_mappings
                import db_forecast_overrides

                # Calculate financial risks for this project
                _task_financial_risk_bundle = db.compute_project_financial_risk_monthly(_task_proj["id"], _task_months)
                _task_financial_risk_by_month = _task_financial_risk_bundle.get("monthly_totals", {})

                _task_task_mappings = db_task_mappings.get_all_task_mappings(_task_proj["id"])
                _task_map_exact = {
                    (m["actual_task_name"], m["actual_mcr"]): m["plan_task_name"]
                    for m in _task_task_mappings if m["actual_mcr"]
                }
                _task_map_catch = {
                    m["actual_task_name"]: m["plan_task_name"]
                    for m in _task_task_mappings if not m["actual_mcr"]
                }
                _task_xc_name = "XC - aggregation BM-00110021_004 & _005"
                _task_xc_mcr_set = {"BM-00110021_004", "BM-00110021_005"}

                _task_to_org = {}
                _task_to_org_rg = {}
                _task_plan_by_key_month = {}
                _task_xc_plan_by_month = {m: 0.0 for m in _task_months}
                _task_ids_by_key = {}
                _task_ids_by_mcr = {}
                _task_plan_case_map = {}
                for _t in _task_tasks:
                    _tid = int(_t.get("id") or 0)
                    _tname = str(_t.get("task_name") or "").strip()
                    if not _tname:
                        continue
                    _task_plan_case_map.setdefault(_tname.lower(), _tname)

                    _mcr = ""
                    if _task_mcr_col:
                        _mcr = str(_task_tvmap.get((_tid, _task_mcr_col), "") or "").strip()
                        if not _mcr:
                            _mcr = str(_task_vmap.get((_tid, _task_mcr_col), "") or "").strip()
                    _mcr = _canonical_project_mcr(_mcr) or "(blank)"

                    _org_name = ""
                    if _task_org_col_key:
                        _org_name = str(_task_tvmap.get((_tid, _task_org_col_key), "") or "").strip()
                        if not _org_name:
                            _org_name = str(_task_vmap.get((_tid, _task_org_col_key), "") or "").strip()
                    _org_name = _org_name or "Unknown Org"
                    _rg_plan = str(_t.get("resource_group") or "").strip()

                    _task_to_org.setdefault((_mcr, _tname), _org_name)
                    _task_to_org.setdefault((None, _tname), _org_name)
                    _task_to_org_rg.setdefault((_mcr, _tname, _rg_plan), _org_name)
                    _task_to_org_rg.setdefault((None, _tname, _rg_plan), _org_name)

                    _k = (_tname, _mcr, _org_name)
                    _task_plan_by_key_month.setdefault(_k, {})
                    _task_ids_by_key.setdefault(_k, set()).add(_tid)
                    _task_ids_by_mcr.setdefault(_mcr, set()).add(_tid)

                    for _m in _task_months:
                        _amt = float(_task_vmap.get((_tid, _m), 0.0) or 0.0)
                        if _mcr in _task_xc_mcr_set:
                            _task_xc_plan_by_month[_m] = float(_task_xc_plan_by_month.get(_m, 0.0) or 0.0) + _amt
                        _task_plan_by_key_month[_k][_m] = _task_plan_by_key_month[_k].get(_m, 0.0) + _amt

                _task_actual_by_key_month = {}
                _task_xc_actual_by_month = {m: 0.0 for m in _task_months}
                for _a in _task_actuals:
                    _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                    if pd.isna(_dt):
                        continue
                    _ym = _dt.strftime("%Y-%m")
                    if _ym not in _task_months:
                        continue

                    _amt = float(_a.get("amount", 0.0) or 0.0)
                    _task_raw = str(_a.get("task_name") or "").strip()
                    _rg_raw = str(_a.get("resource_group") or "").strip()
                    _mcr_raw = _canonical_project_mcr(_a.get("project_no")) or "(blank)"

                    if _mcr_raw in _task_xc_mcr_set:
                        _task_xc_actual_by_month[_ym] = float(_task_xc_actual_by_month.get(_ym, 0.0) or 0.0) + _amt
                        continue

                    _mapped_task = (
                        _task_map_exact.get((_task_raw, _mcr_raw))
                        or _task_map_catch.get(_task_raw)
                        or _task_raw
                        or "(unmapped task)"
                    )
                    _mapped_task = _task_plan_case_map.get(str(_mapped_task).strip().lower(), _mapped_task)
                    _org_name = (
                        _task_to_org_rg.get((_mcr_raw, _mapped_task, _rg_raw))
                        or _task_to_org_rg.get((None, _mapped_task, _rg_raw))
                        or _task_to_org.get((_mcr_raw, _mapped_task))
                        or _task_to_org.get((None, _mapped_task))
                        or "Unknown Org"
                    )
                    _k = (_mapped_task, _mcr_raw, _org_name)
                    _task_actual_by_key_month.setdefault(_k, {})
                    _task_actual_by_key_month[_k][_ym] = _task_actual_by_key_month[_k].get(_ym, 0.0) + _amt

                _task_all_overrides = db_forecast_overrides.get_all_forecast_overrides(_task_proj["id"])
                _task_mcr_has_task0 = {
                    str(_mcr)
                    for _mcr, _ids in _task_ids_by_mcr.items()
                    if 0 in _ids
                }
                _task_mcr_level_override = {}
                _task_override = {}
                for _ov in _task_all_overrides:
                    _ov_month = str(_ov.get("month_year") or "")
                    if _ov_month not in _task_months or _ov_month < _task_current_ym:
                        continue
                    _ov_mcr = _canonical_project_mcr(_ov.get("mcr")) or "(blank)"
                    _ov_amount = float(_ov.get("override_amount", 0.0) or 0.0)
                    _ov_task_id_raw = _ov.get("task_id")
                    if _ov_task_id_raw is None:
                        _ov_task_id = -1
                    else:
                        _ov_task_id = int(_ov_task_id_raw)
                    if _ov_task_id == 0 and _ov_mcr not in _task_mcr_has_task0:
                        _ov_task_id = -1

                    if _ov_task_id == -1:
                        _task_mcr_level_override[(_ov_mcr, _ov_month)] = _ov_amount
                    else:
                        _task_override[(_ov_mcr, _ov_task_id, _ov_month)] = _ov_amount

                _task_all_keys = sorted(set(_task_plan_by_key_month.keys()) | set(_task_actual_by_key_month.keys()))
                if not _task_all_keys:
                    st.info("No grouped monthly rows found for Task view.")
                else:
                    _task_base_rows = []
                    for _tname, _mcr, _org_name in _task_all_keys:
                        # MCR 004/005 are represented only by the synthetic XC aggregate row.
                        if _mcr in _task_xc_mcr_set:
                            continue
                        _row = {
                            "Task": _tname or "(unmapped task)",
                            "Project MCR #": _mcr or "(blank)",
                            "Organisation": _org_name or "Unknown Org",
                        }
                        _plan_total = 0.0
                        _plan_ytd = 0.0
                        _actual_total = 0.0
                        for _m in _task_months:
                            _plan_val = float(_task_plan_by_key_month.get((_tname, _mcr, _org_name), {}).get(_m, 0.0) or 0.0)
                            _actual_val = float(_task_actual_by_key_month.get((_tname, _mcr, _org_name), {}).get(_m, 0.0) or 0.0)
                            _row[f"{_m} Plan (€)"] = _plan_val
                            _row[f"{_m} Actual (€)"] = _actual_val
                            _plan_total += _plan_val
                            _actual_total += _actual_val
                            if _m < _task_current_ym:
                                _plan_ytd += _plan_val

                        _eac_total = 0.0
                        for _m in _task_months:
                            if _m < _task_current_ym:
                                _eac_m = float(_task_actual_by_key_month.get((_tname, _mcr, _org_name), {}).get(_m, 0.0) or 0.0)
                            else:
                                _future_val = 0.0
                                for _tid in _task_ids_by_key.get((_tname, _mcr, _org_name), set()):
                                    _base = float(_task_vmap.get((_tid, _m), 0.0) or 0.0)
                                    _future_val += float(_task_override.get((_mcr, _tid, _m), _base) or 0.0)
                                _eac_m = _future_val
                            _row[f"{_m} EAC (€)"] = _eac_m
                            _eac_total += _eac_m

                        _row["Plan Total (€)"] = _plan_total
                        _row["Plan YTD (€)"] = _plan_ytd
                        _row["Actual Total (€)"] = _actual_total
                        _row["EAC Total (€)"] = _eac_total
                        _row["Delta YTD (€)"] = _actual_total - _plan_ytd
                        _row["Delta (EAC vs. Plan)"] = _eac_total - _plan_total
                        _task_base_rows.append(_row)

                    _task_base_df = pd.DataFrame(_task_base_rows)
                    _task_values = sorted(_task_base_df["Task"].astype(str).unique().tolist())
                    if (any(abs(float(v or 0.0)) > 0.0 for v in _task_xc_actual_by_month.values()) or any(abs(float(v or 0.0)) > 0.0 for v in _task_xc_plan_by_month.values())) and _task_xc_name not in _task_values:
                        _task_values.append(_task_xc_name)
                        _task_values = sorted(_task_values)

                    _task_xc_eac_by_month = {m: 0.0 for m in _task_months}
                    for _m in _task_months:
                        if _m < _task_current_ym:
                            _task_xc_eac_by_month[_m] = float(_task_xc_actual_by_month.get(_m, 0.0) or 0.0)
                        else:
                            _mcr_month_sum = 0.0
                            for _xc_mcr in _task_xc_mcr_set:
                                if (_xc_mcr, _m) in _task_mcr_level_override:
                                    _mcr_month_sum += float(_task_mcr_level_override.get((_xc_mcr, _m), 0.0) or 0.0)
                                else:
                                    for _tid in _task_ids_by_mcr.get(_xc_mcr, set()):
                                        _base = float(_task_vmap.get((_tid, _m), 0.0) or 0.0)
                                        _mcr_month_sum += float(_task_override.get((_xc_mcr, _tid, _m), _base) or 0.0)
                            _task_xc_eac_by_month[_m] = _mcr_month_sum

                    _task_parent_keys = sorted({(str(r["Task"]), str(r["Project MCR #"])) for _, r in _task_base_df.iterrows()})
                    _task_mcr_values = sorted({m for _, m in _task_parent_keys if m})
                    _task_xc_filter_token = "XC (BM-00110021_004/_005)"
                    _task_mcr_filter_options = list(_task_mcr_values)
                    if _task_xc_name in _task_values:
                        _task_mcr_filter_options.append(_task_xc_filter_token)

                    _sel_mcrs = st.multiselect(
                        "Filter MCR Project No.",
                        _task_mcr_filter_options,
                        default=_task_mcr_filter_options,
                        key=f"dash_task_plan_actual_mcr_filter__{_task_proj['id']}",
                    )

                    _task_keys_after_mcr = [k for k in _task_parent_keys if k[1] in set(_sel_mcrs)] if _sel_mcrs else []
                    if _task_xc_filter_token in set(_sel_mcrs):
                        _task_keys_after_mcr.append((_task_xc_name, ""))

                    _task_values_after_mcr = sorted({k[0] for k in _task_keys_after_mcr})
                    _sel_tasks = st.multiselect(
                        "Filter Task name",
                        _task_values_after_mcr,
                        default=_task_values_after_mcr,
                        key=f"dash_task_plan_actual_filter__{_task_proj['id']}",
                    )
                    _show_monthly_cols = st.toggle(
                        "Show monthly comparison columns",
                        value=True,
                        key=f"dash_task_plan_actual_show_months__{_task_proj['id']}",
                    )
                    _delta_basis = st.radio(
                        "Delta basis",
                        ["Act vs. Plan", "EAC vs. Plan"],
                        horizontal=True,
                        key=f"dash_task_plan_actual_delta_basis__{_task_proj['id']}",
                    )
                    _view_mode = st.radio(
                        "View mode",
                        ["Table + Charts", "Table only", "Charts only"],
                        horizontal=True,
                        key=f"dash_task_plan_actual_view_mode__{_task_proj['id']}",
                    )

                    _task_expand_key = f"dash_task_group_expanded__{_task_proj['id']}"
                    if _task_expand_key not in st.session_state:
                        st.session_state[_task_expand_key] = {}

                    _task_parent_keys_visible = [k for k in _task_keys_after_mcr if k[0] in set(_sel_tasks)] if _sel_tasks else []

                    if not _task_parent_keys_visible:
                        st.info("No rows for selected Task filter.")
                    else:
                        _out_rows = []
                        _parent_visual_rows = []
                        _value_cols = []
                        if _show_monthly_cols:
                            for _m in _task_months:
                                _value_cols.extend([f"{_m} Plan (€)", f"{_m} Actual (€)"])
                        _value_cols.extend(["Plan Total (€)", "Plan YTD (€)", "Actual Total (€)", "EAC Total (€)", "Delta YTD (€)", "Delta (EAC vs. Plan)"])

                        for _task_name, _task_mcr in _task_parent_keys_visible:
                            _odf = _task_base_df[
                                (_task_base_df["Task"].astype(str) == str(_task_name))
                                & (_task_base_df["Project MCR #"].astype(str) == str(_task_mcr))
                            ].copy()
                            _parent = {
                                "Task": _task_name,
                                "Project MCR #": _task_mcr,
                                "Organisation": "",
                            }

                            if str(_task_name).strip() == _task_xc_name:
                                for _m in _task_months:
                                    _plan_col = f"{_m} Plan (€)"
                                    _actual_col = f"{_m} Actual (€)"
                                    _plan_val = float(_task_xc_plan_by_month.get(_m, 0.0) or 0.0)
                                    _actual_val = float(_task_xc_actual_by_month.get(_m, 0.0) or 0.0)
                                    if _show_monthly_cols:
                                        _parent[_plan_col] = _plan_val
                                        _parent[_actual_col] = _actual_val

                                _plan_total_xc = float(sum(float(v or 0.0) for v in _task_xc_plan_by_month.values()))
                                _plan_ytd_xc = float(sum(float(_task_xc_plan_by_month.get(_m, 0.0) or 0.0) for _m in _task_months if _m < _task_current_ym))
                                _actual_total_xc = float(sum(float(v or 0.0) for v in _task_xc_actual_by_month.values()))
                                _eac_total_xc = float(sum(float(v or 0.0) for v in _task_xc_eac_by_month.values()))
                                _parent["Plan Total (€)"] = _plan_total_xc
                                _parent["Plan YTD (€)"] = _plan_ytd_xc
                                _parent["Actual Total (€)"] = _actual_total_xc
                                _parent["EAC Total (€)"] = _eac_total_xc
                                _parent["Delta YTD (€)"] = _actual_total_xc - _plan_ytd_xc
                                _parent["Delta (EAC vs. Plan)"] = _eac_total_xc - _plan_total_xc
                            else:
                                for _c in _value_cols:
                                    _parent[_c] = float(pd.to_numeric(_odf[_c], errors="coerce").fillna(0.0).sum()) if _c in _odf.columns else 0.0

                            _parent_visual_rows.append(dict(_parent))
                            _out_rows.append(_parent)

                            if str(_task_name).strip() == _task_xc_name:
                                continue

                            _expand_id = f"{_task_name} || {_task_mcr}" if str(_task_mcr).strip() else str(_task_name)
                            if bool(st.session_state[_task_expand_key].get(_expand_id, False)):
                                _odf = _odf.sort_values(["Organisation"]).reset_index(drop=True)
                                for _, _dr in _odf.iterrows():
                                    _child = {
                                        "Task": "",
                                        "Project MCR #": str(_dr.get("Project MCR #") or ""),
                                        "Organisation": str(_dr.get("Organisation") or "").strip(),
                                    }
                                    for _c in _value_cols:
                                        _child[_c] = float(_dr.get(_c, 0.0) or 0.0)
                                    _out_rows.append(_child)

                        _parent_visual_df = pd.DataFrame(_parent_visual_rows)
                        _delta_col_active = "Delta YTD (€)" if _delta_basis == "Act vs. Plan" else "Delta (EAC vs. Plan)"

                        if _view_mode != "Table only" and not _parent_visual_df.empty:
                            _k1, _k2, _k3, _k4 = st.columns(4)
                            _k1.metric("Total Plan", f"€ {float(pd.to_numeric(_parent_visual_df['Plan Total (€)'], errors='coerce').fillna(0.0).sum()):,.0f}")
                            _k2.metric("Total Actual", f"€ {float(pd.to_numeric(_parent_visual_df['Actual Total (€)'], errors='coerce').fillna(0.0).sum()):,.0f}")
                            _total_eac_task = float(pd.to_numeric(_parent_visual_df['EAC Total (€)'], errors='coerce').fillna(0.0).sum())
                            _total_financial_risk_task = sum(float(v or 0.0) for v in _task_financial_risk_by_month.values())
                            _total_eac_with_risk_task = _total_eac_task + _total_financial_risk_task
                            _k3.metric("Total EAC (+ risks)", f"€ {_total_eac_with_risk_task:,.0f}")
                            _k4.metric("Net Delta", f"€ {float(pd.to_numeric(_parent_visual_df[_delta_col_active], errors='coerce').fillna(0.0).sum()):,.0f}")

                            _rank_df = _parent_visual_df[["Task", "Project MCR #", "Plan Total (€)", "Plan YTD (€)", "Actual Total (€)", "EAC Total (€)", "Delta YTD (€)", "Delta (EAC vs. Plan)"]].copy()
                            _rank_df["Task Key"] = _rank_df.apply(lambda r: f"{r['Task']} | {r['Project MCR #']}" if str(r.get("Project MCR #") or "").strip() else str(r.get("Task") or ""), axis=1)
                            _rank_df["Sign"] = _rank_df[_delta_col_active].apply(lambda v: "Overspending" if float(v or 0.0) > 0 else ("Saving" if float(v or 0.0) < 0 else "Neutral"))
                            _rank_df = _rank_df.sort_values(_delta_col_active, ascending=False)

                            _vc1, _vc2 = st.columns(2)
                            _rank_fig = px.bar(
                                _rank_df,
                                x=_delta_col_active,
                                y="Task Key",
                                orientation="h",
                                color="Sign",
                                color_discrete_map={"Overspending": "#b00020", "Saving": "#0b7a0b", "Neutral": "#7f7f7f"},
                                title=f"Task Ranking by {_delta_col_active}",
                                height=420,
                            )
                            _rank_fig.update_layout(yaxis={"categoryorder": "total ascending"})
                            _vc1.plotly_chart(_rank_fig, use_container_width=True)

                            _quad_df = _rank_df.copy()
                            _quad_df["Bubble Size"] = pd.to_numeric(_quad_df["EAC Total (€)"], errors="coerce").fillna(0.0).abs()
                            _quad_fig = px.scatter(
                                _quad_df,
                                x="Delta YTD (€)",
                                y="Delta (EAC vs. Plan)",
                                size="Bubble Size",
                                color="Task Key",
                                text="Task Key",
                                title="Risk Quadrant: Delta YTD vs Delta (EAC vs. Plan)",
                                height=420,
                            )
                            _x_vals = pd.to_numeric(_quad_df["Delta YTD (€)"], errors="coerce").fillna(0.0)
                            _y_vals = pd.to_numeric(_quad_df["Delta (EAC vs. Plan)"] , errors="coerce").fillna(0.0)
                            _x_abs_max = float(max(abs(_x_vals.min()), abs(_x_vals.max()), 1.0))
                            _y_abs_max = float(max(abs(_y_vals.min()), abs(_y_vals.max()), 1.0))
                            _x_pad = _x_abs_max * 0.1
                            _y_pad = _y_abs_max * 0.1
                            _quad_fig.update_xaxes(range=[-_x_abs_max - _x_pad, _x_abs_max + _x_pad], zeroline=True, zerolinecolor="#666")
                            _quad_fig.update_yaxes(range=[-_y_abs_max - _y_pad, _y_abs_max + _y_pad], zeroline=True, zerolinecolor="#666")
                            _quad_fig.add_vline(x=0, line_dash="dash", line_color="#666")
                            _quad_fig.add_hline(y=0, line_dash="dash", line_color="#666")
                            _qx = _x_abs_max * 0.6
                            _qy = _y_abs_max * 0.6
                            _quad_fig.add_annotation(x=_qx, y=_qy, text="Q1 (+YTD, +EAC)<br>highest risk", showarrow=False, font={"size": 11, "color": "#8b0000"})
                            _quad_fig.add_annotation(x=-_qx, y=_qy, text="Q2 (-YTD, +EAC)<br>emerging future risk", showarrow=False, font={"size": 11, "color": "#8b5a00"})
                            _quad_fig.add_annotation(x=-_qx, y=-_qy, text="Q3 (-YTD, -EAC)<br>savings/opportunity", showarrow=False, font={"size": 11, "color": "#0b7a0b"})
                            _quad_fig.add_annotation(x=_qx, y=-_qy, text="Q4 (+YTD, -EAC)<br>recovery trajectory", showarrow=False, font={"size": 11, "color": "#0b4f8a"})
                            _quad_fig.update_traces(textposition="top center")
                            _vc2.plotly_chart(_quad_fig, use_container_width=True)

                            _trend_task_key_options = _rank_df["Task Key"].dropna().astype(str).tolist()
                            _trend_task_key = st.selectbox(
                                "Trend Task",
                                _trend_task_key_options,
                                key=f"dash_task_plan_actual_trend_sel__{_task_proj['id']}",
                            )
                            _trend_match = _rank_df[_rank_df["Task Key"].astype(str) == str(_trend_task_key)]
                            _trend_task_name = str(_trend_match.iloc[0]["Task"]) if not _trend_match.empty else ""
                            _trend_task_mcr = str(_trend_match.iloc[0]["Project MCR #"]) if not _trend_match.empty else ""

                            _trend_rows = []
                            for _m in _task_months:
                                if str(_trend_task_name).strip() == _task_xc_name:
                                    _pval = float(_task_xc_plan_by_month.get(_m, 0.0) or 0.0)
                                    _aval = float(_task_xc_actual_by_month.get(_m, 0.0) or 0.0)
                                    _eval = float(_task_xc_eac_by_month.get(_m, 0.0) or 0.0)
                                else:
                                    _slice = _task_base_df[
                                        (_task_base_df["Task"].astype(str) == str(_trend_task_name))
                                        & (_task_base_df["Project MCR #"].astype(str) == str(_trend_task_mcr))
                                    ]
                                    _pval = float(pd.to_numeric(_slice.get(f"{_m} Plan (€)", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
                                    _aval = float(pd.to_numeric(_slice.get(f"{_m} Actual (€)", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
                                    _eval = float(pd.to_numeric(_slice.get(f"{_m} EAC (€)", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
                                _trend_rows.append({"Month": _m, "Plan (€)": _pval, "Actual (€)": _aval, "EAC (€)": _eval})

                            _trend_df = pd.DataFrame(_trend_rows)
                            if not _trend_df.empty:
                                _trend_df["Plan Cum (€)"] = pd.to_numeric(_trend_df["Plan (€)"], errors="coerce").fillna(0.0).cumsum()
                                _trend_df["Actual Cum (€)"] = pd.to_numeric(_trend_df["Actual (€)"], errors="coerce").fillna(0.0).cumsum()
                                _trend_df["EAC Cum (€)"] = pd.to_numeric(_trend_df["EAC (€)"], errors="coerce").fillna(0.0).cumsum()
                                _actual_vals = pd.to_numeric(_trend_df["Actual (€)"], errors="coerce").fillna(0.0)
                                _has_actual_mask = _actual_vals != 0
                                _last_actual_idx = -1
                                if _has_actual_mask.any():
                                    _last_actual_idx = len(_trend_df) - 1 - _has_actual_mask[::-1].argmax()

                                _trend_long_rows = []
                                for _i, _row in _trend_df.iterrows():
                                    _trend_long_rows.append({"Month": _row["Month"], "Series": "Plan Cum (€)", "Amount": _row["Plan Cum (€)"]})
                                    _trend_long_rows.append({"Month": _row["Month"], "Series": "EAC Cum (€)", "Amount": _row["EAC Cum (€)"]})
                                    if _i <= _last_actual_idx:
                                        _trend_long_rows.append({"Month": _row["Month"], "Series": "Actual Cum (€)", "Amount": _row["Actual Cum (€)"]})

                                _trend_long = pd.DataFrame(_trend_long_rows)
                                _trend_fig = px.line(
                                    _trend_long,
                                    x="Month",
                                    y="Amount",
                                    color="Series",
                                    markers=True,
                                    title=f"Cumulative Plan vs Actual vs EAC - {_trend_task_key}",
                                    height=420,
                                )
                                st.plotly_chart(_trend_fig, use_container_width=True)

                        if _view_mode != "Charts only":
                            _g1, _g2, _g3, _g4 = st.columns([3, 1, 1, 3])
                            _pick_task = _g1.selectbox(
                                "Task",
                                [f"{t} || {m}" if str(m).strip() else str(t) for t, m in _task_parent_keys_visible],
                                key=f"{_task_expand_key}__pick",
                            )
                            if _g2.button("Toggle", key=f"{_task_expand_key}__toggle"):
                                _emap = st.session_state[_task_expand_key]
                                _emap[_pick_task] = not bool(_emap.get(_pick_task, False))
                                st.session_state[_task_expand_key] = _emap
                                st.rerun()
                            if _g3.button("Expand All", key=f"{_task_expand_key}__expand_all"):
                                st.session_state[_task_expand_key] = {
                                    (f"{t} || {m}" if str(m).strip() else str(t)): True for t, m in _task_parent_keys_visible
                                }
                                st.rerun()
                            if _g4.button("Collapse All", key=f"{_task_expand_key}__collapse_all"):
                                st.session_state[_task_expand_key] = {}
                                st.rerun()

                        _out_df = pd.DataFrame(_out_rows)
                        _parent_rows = _out_df[_out_df["Task"].astype(str).str.strip() != ""].copy()
                        _total_row = {
                            "Task": "TOTAL",
                            "Project MCR #": "",
                            "Organisation": "",
                        }
                        for _c in _value_cols:
                            _total_row[_c] = float(pd.to_numeric(_parent_rows[_c], errors="coerce").fillna(0.0).sum()) if _c in _parent_rows.columns else 0.0
                        _out_df = pd.concat([_out_df, pd.DataFrame([_total_row])], ignore_index=True)

                        def _delta_color_ytd(_v):
                            try:
                                _fv = float(_v)
                            except Exception:
                                return ""
                            if _fv > 0:
                                return "color: #b00020; font-weight: 600"
                            if _fv < 0:
                                return "color: #0b7a0b; font-weight: 600"
                            return ""

                        def _delta_color_eac(_v):
                            try:
                                _fv = float(_v)
                            except Exception:
                                return ""
                            if _fv > 0:
                                return "color: #b00020; font-weight: 600"
                            if _fv < 0:
                                return "color: #0b7a0b; font-weight: 600"
                            return ""

                        _styled_out_df = (
                            _out_df.style
                            .format({c: "€ {:,.0f}" for c in _value_cols})
                            .map(_delta_color_ytd, subset=["Delta YTD (€)"])
                            .map(_delta_color_eac, subset=["Delta (EAC vs. Plan)"])
                        )

                        if _view_mode != "Charts only":
                            st.dataframe(
                                _styled_out_df,
                                hide_index=True,
                                use_container_width=True,
                            )

# ════════════════════════════════════════════════════════════════════════════
# 2. PROJECTS
# ════════════════════════════════════════════════════════════════════════════
elif page == "📁 Projects":
    st.title("📁 2. Projects")
    st.caption("Reference: 2.1 Add New Project | 2.2 Edit Existing Projects")

    # ── Add new project ──
    with st.expander("➕ 2.1 Add New Project", expanded=False):
        with st.form("add_project_form"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Project Name *")
            status = c2.selectbox("Status", ["Active", "On Hold", "Completed", "Cancelled"])
            description = st.text_area("Description")
            c3, c4 = st.columns(2)
            start_date = c3.date_input("Start Date", value=datetime.date.today())
            end_date = c4.date_input("End Date", value=datetime.date.today() + datetime.timedelta(days=365))
            submitted = st.form_submit_button("Save Project")
            if submitted:
                if not name.strip():
                    st.error("Project name is required.")
                else:
                    try:
                        db.add_project(name.strip(), description, str(start_date), str(end_date), status)
                        st.success(f"Project '{name}' created!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

    # ── List & edit existing projects ──
    projects = db.get_projects()
    if not projects:
        st.info("No projects yet.")
    else:
        for p in projects:
            with st.expander(f"2.2 {p['name']}  |  {p['status']}  |  {p['start_date']} → {p['end_date']}"):
                with st.form(f"edit_proj_{p['id']}"):
                    c1, c2 = st.columns(2)
                    new_name = c1.text_input("Name", value=p["name"])
                    new_status = c2.selectbox("Status", ["Active", "On Hold", "Completed", "Cancelled"],
                                              index=["Active", "On Hold", "Completed", "Cancelled"].index(p["status"]))
                    new_desc = st.text_area("Description", value=p["description"] or "")
                    c3, c4 = st.columns(2)
                    new_start = c3.date_input("Start Date",
                                              value=datetime.date.fromisoformat(p["start_date"]) if p["start_date"] else datetime.date.today())
                    new_end = c4.date_input("End Date",
                                            value=datetime.date.fromisoformat(p["end_date"]) if p["end_date"] else datetime.date.today())
                    col_save, col_del = st.columns([3, 1])
                    save = col_save.form_submit_button("💾 Save Changes")
                    delete = col_del.form_submit_button("🗑️ Delete Project", type="secondary")

                    if save:
                        db.update_project(p["id"], new_name, new_desc, str(new_start), str(new_end), new_status)
                        st.success("Saved!")
                        st.rerun()
                    if delete:
                        db.delete_project(p["id"])
                        st.warning("Project deleted.")
                        st.rerun()

# ════════════════════════════════════════════════════════════════════════════
# 3. BUDGET PLANNING
# ════════════════════════════════════════════════════════════════════════════
elif page == "📋 Budget Planning":
    st.title("📋 3. Budget Planning")
    st.caption("Reference: 3.1 Plan | 3.2 Import from Excel | 3.3 Baselines | 3.4 Columns | 3.5 Audit Log")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    # ── Month helpers ────────────────────────────────────────────────────────
    def _bp_project_months(start, end):
        months = []
        cur = start.replace(day=1)
        while cur <= end.replace(day=1):
            months.append(cur.strftime("%Y-%m"))
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)
        return months

    def _month_label(ym):
        try:
            return datetime.date.fromisoformat(ym + "-01").strftime("%b %y")
        except Exception:
            return ym

    try:
        p_start = datetime.date.fromisoformat(project["start_date"])
        p_end   = datetime.date.fromisoformat(project["end_date"])
    except Exception:
        p_start = datetime.date.today().replace(day=1)
        p_end   = p_start.replace(year=p_start.year + 1)

    all_months = _bp_project_months(p_start, p_end)

    # ── User identification (for audit trail) ────────────────────────────────
    _plan_last_user_pref_key = "plan_last_user"
    if "plan_user" not in st.session_state:
        _saved_plan_user = db.get_plan_user_preference(
            project_id,
            "__default__",
            _plan_last_user_pref_key,
            default="",
        )
        st.session_state["plan_user"] = str(_saved_plan_user or "").strip()
    _pu_col, _ = st.columns([2, 5])
    _plan_user_input = _pu_col.text_input(
        "👤 Your name (for audit trail)",
        value=st.session_state["plan_user"],
        key="plan_user_field",
        placeholder="Enter your name...",
    )
    if _plan_user_input.strip() != st.session_state["plan_user"]:
        st.session_state["plan_user"] = _plan_user_input.strip()
        db.set_plan_user_preference(
            project_id,
            "__default__",
            _plan_last_user_pref_key,
            st.session_state["plan_user"],
        )
    current_user = st.session_state["plan_user"]
    _view_pref_user = "__default__"
    if not current_user:
        st.warning("⚠️ Enter your name above — required for save and audit trail.")

    # ── Load custom columns ──────────────────────────────────────────────────
    _custom_cols_meta = db.get_plan_columns(project_id)
    _custom_col_keys  = [c["col_key"] for c in _custom_cols_meta]
    _custom_col_labels = {c["col_key"]: c["col_label"] for c in _custom_cols_meta}
    _custom_col_data_types = {c["col_key"]: (c.get("data_type") or "decimal") for c in _custom_cols_meta}
    _text_custom_col_keys = [k for k in _custom_col_keys if (_custom_col_data_types.get(k) or "").lower() == "text"]
    _text_custom_col_keys_set = set(_text_custom_col_keys)

    def _norm_label(text):
        s = str(text or "").strip().lower()
        for ch in ["/", "-", "_", "(", ")", ".", ","]:
            s = s.replace(ch, " ")
        return " ".join(s.split())

    _norm_label_to_key = {
        _norm_label(c["col_label"]): c["col_key"]
        for c in _custom_cols_meta
    }

    def _find_col_key(candidates):
        for cand in candidates:
            k = _norm_label_to_key.get(_norm_label(cand))
            if k:
                return k
        return None

    _rate_col_key = _find_col_key([
        "rate / hour", "rate per hour", "hourly rate", "rate hour", "rate"
    ])
    _hours_col_key = _find_col_key([
        "hours (annually)", "hours annually", "annual hours", "hours annual", "hours year", "yearly hours"
    ])
    _total_col_key = _find_col_key([
        "total", "total annual", "annual total", "total annual cost", "annual cost"
    ])
    _auto_rate_hours_total = bool(_rate_col_key and _hours_col_key)
    _calc_mode_store_key = "__calc_mode_labor"
    _all_value_keys = all_months + _custom_col_keys

    # ── Helper: load plan DataFrame from DB ──────────────────────────────────
    def _load_plan_df():
        tasks = db.get_plan_tasks(project_id)
        vmap  = db.get_plan_values_for_project(project_id)
        tvmap = db.get_plan_text_values_for_project(project_id)
        base_cols = ["_task_id", "Task Name", "Cost Type", "_calc_mode", "Resource Group", "Employees"]
        if not tasks:
            return pd.DataFrame(columns=base_cols + _all_value_keys + ["Total (€)"])
        records = []
        for t in tasks:
            _stored_mode = vmap.get((t["id"], _calc_mode_store_key), None)
            _default_mode = "Calculated" if (_stored_mode is not None and float(_stored_mode or 0) > 0.5) else "Direct"
            row = {
                "_task_id": int(t["id"]),
                "Task Name": t["task_name"] or "",
                "Cost Type": t["cost_type"] or "Direct cost",
                "_calc_mode": _default_mode,
                "Resource Group": t["resource_group"] or "",
                "Employees": t["employees"] or "",
            }
            for ck in _all_value_keys:
                if ck in _text_custom_col_keys:
                    row[ck] = str(tvmap.get((t["id"], ck), "") or "")
                else:
                    _val = vmap.get((t["id"], ck), 0.0)
                    row[ck] = float(_val if _val is not None else 0.0)
            # Total (€) reflects annual budget as the sum of monthly allocations only.
            row["Total (€)"] = sum(row[ck] for ck in all_months)
            records.append(row)
        return pd.DataFrame(records)

    plan_df = _load_plan_df()
    _persisted_by_tid = {}
    if not plan_df.empty and "_task_id" in plan_df.columns:
        for _, _pr in plan_df.iterrows():
            try:
                _pid = int(float(_pr.get("_task_id") or 0))
            except Exception:
                _pid = 0
            if _pid > 0:
                _persisted_by_tid[_pid] = _pr

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab_plan, tab_import, tab_baselines, tab_columns, tab_audit = st.tabs([
        "📋 3.1 Plan", "📥 3.2 Import from Excel", "🧊 3.3 Baselines", "⚙️ 3.4 Columns", "📜 3.5 Audit Log"
    ])

    # ════════════════════════════════════════════════════════════════════════
    # TAB: Budget Plan
    # ════════════════════════════════════════════════════════════════════════
    with tab_plan:
        _plan_table_slot = st.container()
        # Summary + export
        if not plan_df.empty:
            _total_plan = plan_df["Total (€)"].sum()
            _h1, _h2 = st.columns([3, 1])
            _h1.metric("Total Plan", f"€ {_total_plan:,.0f}")
            _exp_df = plan_df.drop(columns=["_task_id"]).copy()
            _exp_buf = BytesIO()
            with pd.ExcelWriter(_exp_buf, engine="openpyxl") as _ew:
                _exp_df.rename(columns={ck: _month_label(ck) for ck in all_months}).to_excel(
                    _ew, index=False, sheet_name="Budget Plan"
                )
            _h2.download_button(
                "📥 Export to Excel",
                data=_exp_buf.getvalue(),
                file_name=f"budget_plan_{project['name'].replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_plan_excel",
            )

            # Keep the main planning table directly below the Total Plan summary.

        # Column config
        _col_conf = {
            "_task_id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "Task Name": st.column_config.TextColumn("Task Name", width="large"),
            "Cost Type": st.column_config.SelectboxColumn(
                "Cost Type", options=["Labor cost", "Direct cost"], width="medium"
            ),
            "Resource Group": st.column_config.TextColumn("Resource Group", width="medium"),
            "Employees": st.column_config.TextColumn(
                "Employees", width="large", help="Comma-separated employee names for Labor cost tasks"
            ),
            "Total (€)": st.column_config.NumberColumn("Total (€)", format="€ %,.0f", min_value=0.0, step=100.0, width="medium"),
        }
        for _ck in all_months:
            _col_conf[_ck] = st.column_config.NumberColumn(
                _month_label(_ck), format="€ %,.0f", min_value=0.0, step=100.0
            )
        for _ck in _custom_col_keys:
            _label = _custom_col_labels.get(_ck, _ck)
            if _ck == _rate_col_key:
                _col_conf[_ck] = st.column_config.NumberColumn(
                    _label, format="€ %,.0f", min_value=0.0, step=1.0
                )
            elif _ck == _hours_col_key:
                _col_conf[_ck] = st.column_config.NumberColumn(
                    _label, format="%.2f", min_value=0.0, step=1.0
                )
            else:
                _dt = (_custom_col_data_types.get(_ck) or "decimal").lower()
                if _dt == "text":
                    _col_conf[_ck] = st.column_config.TextColumn(_label)
                elif _dt == "integer":
                    _col_conf[_ck] = st.column_config.NumberColumn(
                        _label, format="%.0f", min_value=0.0, step=1.0
                    )
                elif _dt == "currency_eur":
                    _col_conf[_ck] = st.column_config.NumberColumn(
                        _label, format="€ %,.2f", min_value=0.0, step=100.0
                    )
                else:
                    _col_conf[_ck] = st.column_config.NumberColumn(
                        _label, format="%.2f", min_value=0.0, step=1.0
                    )
        
        if _rate_col_key and _hours_col_key:
            _col_conf["_calc_mode"] = st.column_config.SelectboxColumn(
                "Auto-Calc Mode (Labor)",
                options=["Direct", "Calculated"],
                width="medium",
                help="Direct (default): keep month-driven values. Calculated: apply Rate×Hours and distribute across months."
            )

        _rate_candidates_set  = {"rate / hour", "rate per hour", "hourly rate", "rate hour", "rate"}
        _hours_candidates_set = {"hours (annually)", "hours annually", "annual hours", "hours annual", "hours year", "yearly hours"}
        _preferred_helpers = [k for k in [_rate_col_key, _hours_col_key] if k]
        # Exclude: already in preferred_helpers, any other rate/hours alias (dedup),
        # or custom Total col (we show the computed Total (€) instead).
        def _is_rate_or_hours_col(col_key):
            lbl = _norm_label(_custom_col_labels.get(col_key, col_key))
            return lbl in {_norm_label(c) for c in _rate_candidates_set | _hours_candidates_set}
        _other_custom_cols = [
            k for k in _custom_col_keys
            if k not in _preferred_helpers
            and k != _total_col_key
            and not _is_rate_or_hours_col(k)
        ]
        if "custom_project_mcr" in _other_custom_cols and "prj_mcr_number" in _other_custom_cols:
            _other_custom_cols = [k for k in _other_custom_cols if k != "prj_mcr_number"]
        _editor_col_order = [
            "_task_id",
            "Task Name",
            "Cost Type",
            "_calc_mode",
            "Resource Group",
            *_preferred_helpers,
            "Employees",
            *all_months,
            *_other_custom_cols,
            "Total (€)",
        ]

        _show_months_key = f"plan_show_month_cols__{project_id}"
        _plan_show_months_pref_key = "plan_editor_show_month_columns"
        _plan_visible_cols_pref_key = "plan_editor_visible_columns"
        if _show_months_key not in st.session_state:
            _saved_show_months = (
                db.get_plan_user_preference(project_id, _view_pref_user, _plan_show_months_pref_key, default=False)
            )
            st.session_state[_show_months_key] = bool(_saved_show_months)

        _plan_col_pref_key = "plan_editor_column_order"

        def _normalize_editor_col_order(order_values):
            if not isinstance(order_values, list):
                order_values = []
            _seen = set()
            _normalized = []
            for _c in order_values:
                if _c in _editor_col_order and _c not in _seen:
                    _normalized.append(_c)
                    _seen.add(_c)
            for _c in _editor_col_order:
                if _c not in _seen:
                    _normalized.append(_c)
            return _normalized

        _saved_editor_col_order = (
            db.get_plan_user_preference(project_id, _view_pref_user, _plan_col_pref_key, default=[])
        )
        _col_order_state_key = f"plan_editor_col_order__{project_id}"
        if _col_order_state_key not in st.session_state:
            st.session_state[_col_order_state_key] = _normalize_editor_col_order(_saved_editor_col_order)
        else:
            st.session_state[_col_order_state_key] = _normalize_editor_col_order(
                st.session_state[_col_order_state_key]
            )

        _active_editor_col_order = st.session_state[_col_order_state_key]
        _visible_cols_state_key = f"plan_editor_visible_cols__{project_id}"
        if _visible_cols_state_key not in st.session_state:
            _saved_visible_cols = db.get_plan_user_preference(
                project_id,
                _view_pref_user,
                _plan_visible_cols_pref_key,
                default=[],
            )
            if isinstance(_saved_visible_cols, list) and _saved_visible_cols:
                _saved_set = set(_saved_visible_cols)
                st.session_state[_visible_cols_state_key] = [
                    _c for _c in _active_editor_col_order if _c in _saved_set
                ]
            else:
                st.session_state[_visible_cols_state_key] = list(_active_editor_col_order)

        # Keep visible-columns state aligned with any new/removed columns.
        _visible_set = set(st.session_state.get(_visible_cols_state_key, []))
        st.session_state[_visible_cols_state_key] = [
            _c for _c in _active_editor_col_order if _c in _visible_set
        ]

        def _persist_view_state(_order_vals=None):
            _order_payload = _normalize_editor_col_order(_order_vals or st.session_state.get(_col_order_state_key, _active_editor_col_order))
            db.set_plan_user_preference(
                project_id,
                _view_pref_user,
                _plan_col_pref_key,
                _order_payload,
            )
            db.set_plan_user_preference(
                project_id,
                _view_pref_user,
                _plan_show_months_pref_key,
                bool(st.session_state.get(_show_months_key, False)),
            )
            db.set_plan_user_preference(
                project_id,
                _view_pref_user,
                _plan_visible_cols_pref_key,
                st.session_state.get(_visible_cols_state_key, _order_payload),
            )

        with st.expander("🧭 3.1.1 Table Layout", expanded=False):
            st.caption("Use this as the canonical place to configure table layout (visibility, month columns, and order).")
            _v1, _v2 = st.columns([2, 6])
            _v1.toggle("Show month columns", key=_show_months_key)
            _v2.caption("Turn off for compact view; turn on to edit monthly allocations.")

            def _visible_col_label(_col_key):
                if _col_key == "_task_id":
                    return "ID"
                if _col_key == "Total (€)":
                    return "Total"
                return _col_key

            _visible_label_to_key = {
                _visible_col_label(_c): _c for _c in _active_editor_col_order
            }
            _visible_options = list(_visible_label_to_key.keys())
            _visible_default = [
                _visible_col_label(_c)
                for _c in st.session_state.get(_visible_cols_state_key, _active_editor_col_order)
                if _c in _active_editor_col_order
            ]
            _vc_sel = st.multiselect(
                "Visible columns",
                options=_visible_options,
                default=_visible_default,
                key=f"{_visible_cols_state_key}__widget",
                help="Persisted layout control. Use this for stable show/hide behavior across section switches.",
            )
            _selected_key_set = {_visible_label_to_key[_lbl] for _lbl in _vc_sel if _lbl in _visible_label_to_key}
            _vc_ordered = [
                _c for _c in _active_editor_col_order
                if _c in _selected_key_set
            ]
            st.session_state[_visible_cols_state_key] = _vc_ordered

            st.markdown("**Column order**")
            _picked_col = st.selectbox(
                "Column to move",
                _active_editor_col_order,
                key=f"{_col_order_state_key}__pick",
            )
            _co1, _co2, _co3, _co4 = st.columns(4)
            if _co1.button("⬅️ Move Left", key=f"{_col_order_state_key}__left"):
                _idx = _active_editor_col_order.index(_picked_col)
                if _idx > 0:
                    _active_editor_col_order[_idx - 1], _active_editor_col_order[_idx] = (
                        _active_editor_col_order[_idx],
                        _active_editor_col_order[_idx - 1],
                    )
                    st.session_state[_col_order_state_key] = _active_editor_col_order
                    _persist_view_state(_active_editor_col_order)
                    st.session_state.pop("plan_data_editor", None)
                    st.rerun()
            if _co2.button("➡️ Move Right", key=f"{_col_order_state_key}__right"):
                _idx = _active_editor_col_order.index(_picked_col)
                if _idx < len(_active_editor_col_order) - 1:
                    _active_editor_col_order[_idx + 1], _active_editor_col_order[_idx] = (
                        _active_editor_col_order[_idx],
                        _active_editor_col_order[_idx + 1],
                    )
                    st.session_state[_col_order_state_key] = _active_editor_col_order
                    _persist_view_state(_active_editor_col_order)
                    st.session_state.pop("plan_data_editor", None)
                    st.rerun()
            if _co3.button("💾 Save Layout", key=f"{_col_order_state_key}__save"):
                _persist_view_state(_active_editor_col_order)
                st.success("Table layout saved.")
            if _co4.button("↩️ Reset Layout", key=f"{_col_order_state_key}__reset"):
                st.session_state[_col_order_state_key] = _normalize_editor_col_order([])
                st.session_state[_visible_cols_state_key] = list(st.session_state[_col_order_state_key])
                _persist_view_state(st.session_state[_col_order_state_key])
                st.session_state.pop("plan_data_editor", None)
                st.rerun()

            _persist_view_state()

        _visible_editor_col_order = [
            _c for _c in st.session_state[_visible_cols_state_key]
            if st.session_state[_show_months_key] or _c not in all_months
        ]

        st.caption(
            "✏️ Edit task metadata and monthly values directly in the table. "
            "Add rows at the bottom (**+**). Delete rows with the row-delete icon (✕ on hover). "
            "Click **💾 Save Changes** to persist."
        )

        if _auto_rate_hours_total:
            _has_labor = not plan_df.empty and any(str(ct).strip().lower() == "labor cost" for ct in plan_df.get("Cost Type", []))
            if _has_labor:
                st.info(
                    "💡 **Auto-Calc Modes**:\n"
                    "- **Calculated (toggle ON)**: Rate/Hour × Hours(Annual) → Total (auto-distributed to months)\n"
                    "- **Direct (toggle OFF, default)**: keep month-driven values and derive Total from month sum\n"
                    "Select mode in the '_Auto-Calc Mode (Labor)' column."
                )

        def _apply_live_calc_preview(_df_in, _last_edit_hint=None):
            _df_out = _df_in.copy()
            def _num_or_zero_local(v):
                try:
                    if pd.isna(v):
                        return 0.0
                    return float(v)
                except Exception:
                    return 0.0

            _can_write_total_col = (
                bool(_total_col_key)
                and _total_col_key in _df_out.columns
                and not pd.api.types.is_string_dtype(_df_out[_total_col_key].dtype)
            )

            if _auto_rate_hours_total and all_months:
                for _idx, _r in _df_out.iterrows():
                    if str(_r.get("Task Name") or "").strip().upper().startswith("TOTAL"):
                        continue
                    _calc_mode = "Calculated" if str(_r.get("_calc_mode", "Direct")).strip().lower() in ("calculate", "calculated", "true", "1", "yes", "on") else "Direct"
                    _hint_col = (_last_edit_hint or {}).get(_idx, "")
                    _prev_row = None
                    if _work_df_key in st.session_state:
                        _prev_df = st.session_state[_work_df_key]
                        if isinstance(_prev_df, pd.DataFrame) and _idx < len(_prev_df):
                            _prev_row = _prev_df.iloc[_idx]
                    if _calc_mode == "Calculated":
                        _rate_val = _num_or_zero_local(_r.get(_rate_col_key, 0.0))
                        _hours_val = _num_or_zero_local(_r.get(_hours_col_key, 0.0))
                        if _rate_val > 0 and _hours_val > 0:
                            _annual_total = _rate_val * _hours_val
                            if _can_write_total_col:
                                _df_out.at[_idx, _total_col_key] = _annual_total
                            _per_month = _annual_total / len(all_months)
                            for _m in all_months:
                                _df_out.at[_idx, _m] = _per_month
                        else:
                            # Lock month/total cells in Calculated mode even when helpers are empty.
                            # Revert to previous row values if available; otherwise keep them at zero.
                            if _prev_row is not None:
                                if _can_write_total_col:
                                    _df_out.at[_idx, _total_col_key] = _num_or_zero_local(_prev_row.get(_total_col_key, 0.0))
                                for _m in all_months:
                                    _df_out.at[_idx, _m] = _num_or_zero_local(_prev_row.get(_m, 0.0))
                            else:
                                if _can_write_total_col:
                                    _df_out.at[_idx, _total_col_key] = 0.0
                                for _m in all_months:
                                    _df_out.at[_idx, _m] = 0.0
                    elif _calc_mode == "Direct":
                        _month_sum = sum(_num_or_zero_local(_r.get(_m, 0.0)) for _m in all_months)
                        _table_total_val = _num_or_zero_local(_r.get("Total (€)", _month_sum))
                        _total_val = _table_total_val
                        if _can_write_total_col and _hint_col != "Total (€)":
                            _total_val = _num_or_zero_local(_r.get(_total_col_key, _month_sum))
                        _prev_month_sum = _month_sum
                        _prev_total_val = _total_val
                        if _prev_row is not None:
                            _prev_month_sum = sum(_num_or_zero_local(_prev_row.get(_m, 0.0)) for _m in all_months)
                            _prev_table_total_val = _num_or_zero_local(_prev_row.get("Total (€)", _prev_month_sum))
                            _prev_total_val = _prev_table_total_val
                            if _can_write_total_col and _hint_col != "Total (€)":
                                _prev_total_val = _num_or_zero_local(_prev_row.get(_total_col_key, _prev_month_sum))

                        _month_changed = abs(_month_sum - _prev_month_sum) > 0.001
                        _total_changed = abs(_total_val - _prev_total_val) > 0.001

                        _total_wins = (_hint_col == "Total (€)")
                        _months_win = (_hint_col in all_months)

                        if _total_wins or (_total_changed and not _month_changed and not _months_win):
                            _per_month = (_total_val / len(all_months)) if all_months else 0.0
                            for _m in all_months:
                                _df_out.at[_idx, _m] = _per_month
                            if _can_write_total_col:
                                _df_out.at[_idx, _total_col_key] = _total_val
                        else:
                            if _can_write_total_col:
                                _df_out.at[_idx, _total_col_key] = _month_sum
                    if _calc_mode == "Calculated":
                        # Hard lock in-editor for month/total cells: always restore from formula or persisted values.
                        _rate_val = _num_or_zero_local(_r.get(_rate_col_key, 0.0))
                        _hours_val = _num_or_zero_local(_r.get(_hours_col_key, 0.0))
                        if not (_rate_val > 0 and _hours_val > 0):
                            _tid_val = 0
                            try:
                                _tid_val = int(float(_r.get("_task_id") or 0))
                            except Exception:
                                _tid_val = 0
                            _p_row = _persisted_by_tid.get(_tid_val)
                            if _p_row is not None:
                                if _can_write_total_col:
                                    _df_out.at[_idx, _total_col_key] = _num_or_zero_local(_p_row.get(_total_col_key, 0.0))
                                for _m in all_months:
                                    _df_out.at[_idx, _m] = _num_or_zero_local(_p_row.get(_m, 0.0))
            if "Total (€)" in _df_out.columns:
                for _idx, _r in _df_out.iterrows():
                    if str(_r.get("Task Name") or "").strip().upper().startswith("TOTAL"):
                        continue
                    _df_out.at[_idx, "Total (€)"] = sum(_num_or_zero_local(_r.get(_m, 0.0)) for _m in all_months)
            return _df_out

        _work_df_key = f"plan_editor_work_df__{project_id}__{current_user or 'anon'}"
        _work_sig_key = f"{_work_df_key}__sig"
        _plan_sig = (
            tuple(plan_df.get("_task_id", pd.Series(dtype=float)).fillna(0).astype(int).tolist()) if not plan_df.empty else tuple(),
            tuple(plan_df.columns.tolist()),
            int(len(plan_df)),
        )
        if _work_df_key not in st.session_state or st.session_state.get(_work_sig_key) != _plan_sig:
            st.session_state[_work_df_key] = _apply_live_calc_preview(plan_df)
            st.session_state[_work_sig_key] = _plan_sig

        _disabled_cols = ["_task_id"]
        
        with _plan_table_slot:
            # ── Filter controls for Plan table ──────────────────────────────────────────────
            _work_df_for_filter = st.session_state[_work_df_key].copy() if _work_df_key in st.session_state else plan_df.copy()
            
            # Extract unique values for filtering
            _all_mcr_values = []
            _all_task_names = []
            _all_resource_groups = []
            _all_organisations = []
            _organisation_col = None
            _organisation_available = False
            
            if not _work_df_for_filter.empty:
                def _norm_org_token(v):
                    return str(v or "").strip().lower().replace("_", " ")

                for _c in _work_df_for_filter.columns:
                    _c_norm = _norm_org_token(_c)
                    _label_norm = _norm_org_token(_custom_col_labels.get(_c, _c))
                    if (
                        "organisation" in _c_norm
                        or "organization" in _c_norm
                        or "organisation" in _label_norm
                        or "organization" in _label_norm
                    ):
                        _organisation_col = _c
                        break

                # Check if prj_mcr_name exists as a custom column in the dataframe
                if "prj_mcr_name" in _work_df_for_filter.columns:
                    _all_mcr_values = sorted(set(
                        str(v).strip() for v in _work_df_for_filter["prj_mcr_name"].fillna("").unique()
                        if str(v).strip()
                    ))
                
                _all_task_names = sorted(set(
                    str(v).strip() for v in _work_df_for_filter["Task Name"].fillna("").unique()
                    if str(v).strip()
                ))
                
                _all_resource_groups = sorted(set(
                    str(v).strip() for v in _work_df_for_filter["Resource Group"].fillna("").unique()
                    if str(v).strip()
                ))

                if _organisation_col:
                    _organisation_available = True
                    _all_organisations = sorted(set(
                        str(v).strip() for v in _work_df_for_filter[_organisation_col].fillna("").unique()
                        if str(v).strip()
                    ))
            
            # Render filter controls right above the table
            st.markdown("**Filters:**")
            _filter_cols = st.columns([1, 1, 1, 1]) if _all_mcr_values else st.columns([1, 1, 1])
            
            _selected_mcrs = None
            if _all_mcr_values:
                with _filter_cols[0]:
                    _selected_mcrs = st.multiselect(
                        "Project MCR",
                        _all_mcr_values,
                        key=f"plan_filter_mcr_{project_id}",
                        help="Leave blank to show all"
                    )
                _task_col_idx = 1
            else:
                _task_col_idx = 0
            
            with _filter_cols[_task_col_idx]:
                _selected_tasks = st.multiselect(
                    "Task Name",
                    _all_task_names,
                    key=f"plan_filter_task_{project_id}",
                    help="Leave blank to show all"
                )
            
            with _filter_cols[_task_col_idx + 1]:
                _selected_resources = st.multiselect(
                    "Resource Group",
                    _all_resource_groups,
                    key=f"plan_filter_resource_{project_id}",
                    help="Leave blank to show all"
                )

            _selected_orgs = []
            with _filter_cols[_task_col_idx + 2]:
                _selected_orgs = st.multiselect(
                    "Organisation",
                    _all_organisations,
                    key=f"plan_filter_org_{project_id}",
                    help=("Leave blank to show all" if _organisation_available else "No Organisation column found in this table")
                )
            
            # Apply filters to the work dataframe
            _filtered_df = _work_df_for_filter.copy()
            
            if _selected_mcrs and "prj_mcr_name" in _filtered_df.columns:
                _filtered_df = _filtered_df[_filtered_df["prj_mcr_name"].fillna("").astype(str).str.strip().isin(_selected_mcrs)]
            
            if _selected_tasks:
                _filtered_df = _filtered_df[_filtered_df["Task Name"].fillna("").astype(str).str.strip().isin(_selected_tasks)]
            
            if _selected_resources:
                _filtered_df = _filtered_df[_filtered_df["Resource Group"].fillna("").astype(str).str.strip().isin(_selected_resources)]

            if _selected_orgs and _organisation_available and _organisation_col in _filtered_df.columns:
                _filtered_df = _filtered_df[_filtered_df[_organisation_col].fillna("").astype(str).str.strip().isin(_selected_orgs)]
            
            # Show filter status
            if _selected_mcrs or _selected_tasks or _selected_resources or _selected_orgs:
                _filtered_count = len(_filtered_df)
                _total_count = len(_work_df_for_filter)
                st.info(f"📊 Showing {_filtered_count} of {_total_count} tasks ({100*_filtered_count/_total_count:.0f}%)")

            _display_df = _filtered_df.copy()
            if not _display_df.empty:
                _total_row = {c: "" for c in _display_df.columns}
                _total_row["_task_id"] = -1
                _total_row["Task Name"] = "TOTAL - SUMMARY"
                _total_row["Cost Type"] = ""
                _total_row["_calc_mode"] = ""
                _total_row["Resource Group"] = ""
                _total_row["Employees"] = ""

                for _m in all_months:
                    if _m in _display_df.columns:
                        _total_row[_m] = float(pd.to_numeric(_display_df[_m], errors="coerce").fillna(0.0).sum())

                if "Total (€)" in _display_df.columns:
                    _total_row["Total (€)"] = float(pd.to_numeric(_display_df["Total (€)"], errors="coerce").fillna(0.0).sum())

                _display_df = pd.concat([_display_df, pd.DataFrame([_total_row])], ignore_index=True)

            st.caption("-------------------- SUMMARY ROW --------------------  TOTAL - SUMMARY (display only)")
            
            edited_plan = st.data_editor(
                _display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                column_order=_visible_editor_col_order,
                disabled=_disabled_cols,
                column_config=_col_conf,
                key="plan_data_editor",
            )

            _edited_plan_no_total = edited_plan[
                ~edited_plan["Task Name"].fillna("").astype(str).str.strip().str.upper().str.startswith("TOTAL")
            ].copy()

            _save_table_col, _ = st.columns([1, 5])
            _save_clicked = _save_table_col.button(
                "💾 Save Input Table Changes",
                type="primary",
                key="save_plan_btn_table",
            )
            st.caption("Edits are only persisted after clicking Save Input Table Changes.")

        _editor_state = st.session_state.get("plan_data_editor", {})
        _last_edit_hint = {}
        _locked_edit_reverted = False
        if isinstance(_editor_state, dict):
            _state_col_order = _editor_state.get("column_order")
            if isinstance(_state_col_order, list) and _state_col_order:
                _normalized_from_editor = _normalize_editor_col_order(_state_col_order)
                if _normalized_from_editor != st.session_state.get(_col_order_state_key, []):
                    st.session_state[_col_order_state_key] = _normalized_from_editor
                    _active_editor_col_order = _normalized_from_editor
                    _persist_view_state(_normalized_from_editor)
            _edited_rows = _editor_state.get("edited_rows", {})
            if isinstance(_edited_rows, dict):
                for _rk, _changes in _edited_rows.items():
                    try:
                        _ri = int(_rk)
                    except Exception:
                        continue
                    if not (isinstance(_changes, dict) and _changes):
                        continue
                    if _ri < 0 or _ri >= len(edited_plan):
                        continue

                    _row_mode = "Calculated" if str(edited_plan.iloc[_ri].get("_calc_mode", "Direct")).strip().lower() in ("calculate", "calculated", "true", "1", "yes", "on") else "Direct"
                    _allowed_keys = list(_changes.keys())

                    if _row_mode == "Calculated":
                        _locked_cols = [k for k in _changes.keys() if k == "Total (€)" or k in all_months]
                        if _locked_cols:
                            _prev_df = st.session_state.get(_work_df_key)
                            if isinstance(_prev_df, pd.DataFrame) and not _prev_df.empty:
                                # Use _task_id to map rows instead of row index (handles filtering correctly)
                                _task_id_val = None
                                try:
                                    _task_id_val = int(float(edited_plan.iloc[_ri].get("_task_id") or 0))
                                except (ValueError, TypeError):
                                    pass
                                
                                if _task_id_val and _task_id_val > 0:
                                    _prev_rows = _prev_df[_prev_df["_task_id"] == _task_id_val]
                                    if not _prev_rows.empty:
                                        for _lk in _locked_cols:
                                            if _lk in edited_plan.columns and _lk in _prev_df.columns:
                                                edited_plan.at[_ri, _lk] = _prev_rows.iloc[0].get(_lk)
                            _allowed_keys = [k for k in _allowed_keys if k not in _locked_cols]
                            _locked_edit_reverted = True

                    if _allowed_keys:
                        _last_edit_hint[_ri] = _allowed_keys[-1]

            if _locked_edit_reverted:
                st.info("In Calculated mode, month and Total cells are locked. Edit Rate/Hours or switch to Direct mode.")

            pass

        # Reconcile filtered dataframe changes back to the full work dataframe
        # When filters are active, edited_plan contains only filtered rows
        # We need to merge changes back into the full _work_df_key
        _full_work_df = st.session_state.get(_work_df_key, plan_df.copy())
        
        if not (_filtered_df.equals(_full_work_df)):
            # Filtering was applied - reconcile changes from edited_plan back to full dataframe
            if not _edited_plan_no_total.empty and not _full_work_df.empty:
                for _, _edited_row in _edited_plan_no_total.iterrows():
                    try:
                        _task_id_val = int(float(_edited_row.get("_task_id") or 0))
                        if _task_id_val > 0:
                            _full_rows = _full_work_df[_full_work_df["_task_id"] == _task_id_val]
                            if not _full_rows.empty:
                                _full_idx = _full_rows.index[0]
                                for _col in _edited_row.index:
                                    _full_work_df.at[_full_idx, _col] = _edited_row[_col]
                    except (ValueError, TypeError, KeyError):
                        pass
            _live_editor_df = _apply_live_calc_preview(_full_work_df, _last_edit_hint=_last_edit_hint)
        else:
            # No filtering - use edited_plan directly
            _live_editor_df = _apply_live_calc_preview(_edited_plan_no_total, _last_edit_hint=_last_edit_hint)
        
        if not _live_editor_df.equals(st.session_state[_work_df_key]):
            st.session_state[_work_df_key] = _live_editor_df
            if not _save_clicked:
                st.rerun()

        if _save_clicked:
            if not current_user:
                st.error("⚠️ Please enter your name (for audit trail) before saving.")
            else:
                _added = _deleted = _meta_upd = _val_upd = 0

                _save_df = st.session_state[_work_df_key].copy()

                def _num_or_zero(v):
                    try:
                        if pd.isna(v):
                            return 0.0
                        return float(v)
                    except Exception:
                        return 0.0

                _orig_df = plan_df
                _orig_ids = (
                    set(int(v) for v in _orig_df["_task_id"].dropna().tolist())
                    if not _orig_df.empty else set()
                )

                # Collect existing IDs still present in edited result
                _edit_ids = set()
                for _v in _save_df["_task_id"].dropna():
                    try:
                        _iv = int(float(_v))
                        if _iv > 0:
                            _edit_ids.add(_iv)
                    except (ValueError, TypeError):
                        pass

                # Deleted tasks
                for _tid in _orig_ids - _edit_ids:
                    _trow = _orig_df[_orig_df["_task_id"] == _tid].iloc[0]
                    db.delete_plan_task(_tid)
                    db.add_plan_audit(
                        project_id, _tid, _trow["Task Name"], "", "", "", "delete_task", current_user
                    )
                    _deleted += 1

                _orig_by_id = _orig_df.set_index("_task_id") if not _orig_df.empty else None

                def _locked_calc_values(_row_s, _old_row_s=None):
                    _vals = {}
                    _mode_row = "Calculated" if str(_row_s.get("_calc_mode", "Direct")).strip().lower() in ("calculate", "calculated", "true", "1", "yes", "on") else "Direct"
                    if _mode_row != "Calculated":
                        return _vals
                    _r_val = _num_or_zero(_row_s.get(_rate_col_key, 0.0))
                    _h_val = _num_or_zero(_row_s.get(_hours_col_key, 0.0))
                    if _r_val > 0 and _h_val > 0 and all_months:
                        _annual = _r_val * _h_val
                        if _total_col_key:
                            _vals[_total_col_key] = _annual
                        _per = _annual / len(all_months)
                        for _m in all_months:
                            _vals[_m] = _per
                        return _vals
                    # If helpers are empty in Calculated mode, keep persisted values (ignore user edits).
                    if _old_row_s is not None:
                        if _total_col_key:
                            _vals[_total_col_key] = _num_or_zero(_old_row_s.get(_total_col_key, 0.0))
                        for _m in all_months:
                            _vals[_m] = _num_or_zero(_old_row_s.get(_m, 0.0))
                    else:
                        if _total_col_key:
                            _vals[_total_col_key] = 0.0
                        for _m in all_months:
                            _vals[_m] = 0.0
                    return _vals

                for _, _row in _save_df.iterrows():
                    _raw_id = _row.get("_task_id")
                    try:
                        _tid = int(float(_raw_id)) if (_raw_id is not None and not pd.isna(_raw_id)) else 0
                    except (ValueError, TypeError):
                        _tid = 0

                    _tname = str(_row.get("Task Name") or "").strip()
                    _ctype = str(_row.get("Cost Type") or "Direct cost").strip() or "Direct cost"
                    _calc_mo = "Calculated" if str(_row.get("_calc_mode", "Direct")).strip().lower() in ("calculate", "calculated", "true", "1", "yes", "on") else "Direct"
                    _rg    = str(_row.get("Resource Group") or "").strip()
                    _emps  = str(_row.get("Employees") or "").strip()

                    if not _tname:
                        continue  # skip blank rows

                    if _tid == 0 or _tid not in _orig_ids:
                        # New task
                        _new_tid = db.add_plan_task(project_id, _tname, _ctype, _rg, _emps)
                        db.add_plan_audit(project_id, _new_tid, _tname, "", "", "", "add_task", current_user)
                        db.upsert_plan_value(
                            _new_tid,
                            _calc_mode_store_key,
                            1.0 if _calc_mo == "Calculated" else 0.0,
                            current_user,
                        )
                        _added += 1
                        _calc_locked = _locked_calc_values(_row, None)
                        for _ck in _all_value_keys:
                            if _ck in _text_custom_col_keys_set:
                                _tv = str(_row.get(_ck) or "").strip()
                                if _tv:
                                    db.upsert_plan_text_value(_new_tid, _ck, _tv, current_user)
                                    _val_upd += 1
                            else:
                                _val = float(_calc_locked.get(_ck, _row.get(_ck, 0)) or 0)
                                if _val != 0:
                                    db.upsert_plan_value(_new_tid, _ck, _val, current_user)
                                    _val_upd += 1
                    else:
                        # Existing task
                        if _orig_by_id is not None and _tid in _orig_ids:
                            _orow = _orig_by_id.loc[_tid]
                            _fmap = {
                                "Task Name": _tname,
                                "Cost Type": _ctype,
                                "Resource Group": _rg,
                                "Employees": _emps,
                            }
                            _old_mo = "Calculated" if str(_orow.get("_calc_mode", "Direct")).strip().lower() in ("calculate", "calculated", "true", "1", "yes", "on") else "Direct"
                            _meta_changed = False
                            for _field, _nv in _fmap.items():
                                _ov = str(_orow.get(_field) or "").strip()
                                if _ov != _nv:
                                    db.add_plan_audit(
                                        project_id, _tid, _tname, _field, _ov, _nv, "update_meta", current_user
                                    )
                                    _meta_changed = True
                            if _calc_mo != _old_mo:
                                db.add_plan_audit(
                                    project_id, _tid, _tname, "_calc_mode", _old_mo, _calc_mo, "update_meta", current_user
                                )
                                db.upsert_plan_value(
                                    _tid,
                                    _calc_mode_store_key,
                                    1.0 if _calc_mo == "Calculated" else 0.0,
                                    current_user,
                                )
                                _meta_changed = True
                            if _meta_changed:
                                db.update_plan_task(_tid, _tname, _ctype, _rg, _emps)
                                _meta_upd += 1

                            _calc_locked = _locked_calc_values(_row, _orow)
                            for _ck in _all_value_keys:
                                if _ck in _text_custom_col_keys_set:
                                    _nv = str(_row.get(_ck) or "").strip()
                                    _ov = str(_orow.get(_ck) or "").strip()
                                    if _nv != _ov:
                                        db.upsert_plan_text_value(_tid, _ck, _nv, current_user)
                                        db.add_plan_audit(
                                            project_id, _tid, _tname, _ck, _ov, _nv, "update", current_user
                                        )
                                        _val_upd += 1
                                else:
                                    _nv = float(_calc_locked.get(_ck, _row.get(_ck, 0)) or 0)
                                    _ov = float(_orow.get(_ck) or 0)
                                    if abs(_nv - _ov) > 0.001:
                                        db.upsert_plan_value(_tid, _ck, _nv, current_user)
                                        db.add_plan_audit(
                                            project_id, _tid, _tname, _ck, _ov, _nv, "update", current_user
                                        )
                                        _val_upd += 1

                _parts = []
                if _added:    _parts.append(f"{_added} task(s) added")
                if _deleted:  _parts.append(f"{_deleted} task(s) deleted")
                if _meta_upd: _parts.append(f"{_meta_upd} task(s) metadata updated")
                if _val_upd:  _parts.append(f"{_val_upd} value(s) changed")
                if _parts:
                    st.success("✅ " + " | ".join(_parts))
                else:
                    st.info("No changes detected.")
                _order_to_persist = st.session_state.get(_col_order_state_key, _active_editor_col_order)
                _editor_state_after_save = st.session_state.get("plan_data_editor", {})
                if isinstance(_editor_state_after_save, dict):
                    _state_col_order = _editor_state_after_save.get("column_order")
                    if isinstance(_state_col_order, list) and _state_col_order:
                        _order_to_persist = _normalize_editor_col_order(_state_col_order)
                        st.session_state[_col_order_state_key] = _order_to_persist
                db.set_plan_user_preference(
                    project_id,
                    _view_pref_user,
                    _plan_col_pref_key,
                    _order_to_persist,
                )
                db.set_plan_user_preference(
                    project_id,
                    _view_pref_user,
                    _plan_show_months_pref_key,
                    bool(st.session_state.get(_show_months_key, False)),
                )
                st.session_state.pop(_work_df_key, None)
                st.session_state.pop(_work_sig_key, None)
                st.rerun()

        # ── Add Task form ────────────────────────────────────────────────────
        with st.expander("➕ 3.1.2 Add New Task", expanded=plan_df.empty):
            with st.form("add_task_form"):
                _at1, _at2 = st.columns(2)
                _new_tname = _at1.text_input("Task Name *")
                _new_ctype = _at2.selectbox("Cost Type *", ["Labor cost", "Direct cost"])
                _at3, _at4 = st.columns(2)
                _new_rg    = _at3.text_input("Resource Group")
                _new_emps  = _at4.text_input(
                    "Employees (comma-separated)",
                    help="Required for Labor cost tasks — names of employees who will book time",
                )
                if st.form_submit_button("Add Task"):
                    if not _new_tname.strip():
                        st.error("Task Name is required.")
                    elif _new_ctype == "Labor cost" and not _new_emps.strip():
                        st.error("Employee name(s) are required for Labor cost tasks.")
                    else:
                        _ntid = db.add_plan_task(
                            project_id, _new_tname.strip(), _new_ctype,
                            _new_rg.strip(), _new_emps.strip()
                        )
                        db.add_plan_audit(
                            project_id, _ntid, _new_tname.strip(), "", "", "",
                            "add_task", current_user or "unknown"
                        )
                        st.success(f"Task added: **{_new_tname.strip()}**")
                        st.rerun()

        # ── Annual distribution ──────────────────────────────────────────────
        if not plan_df.empty:
            with st.expander("📅 3.1.3 Enter Annual Total (distribute equally across months)"):
                st.caption(
                    "Select a task and year, enter the total annual budget. "
                    "The amount will be split equally across all plan months within that year."
                )
                # Derive unique years from project months
                _plan_years = sorted(set(ym[:4] for ym in all_months))

                with st.form("annual_dist_form"):
                    _ad1, _ad2, _ad3 = st.columns(3)
                    _ad_task = _ad1.selectbox(
                        "Task *",
                        plan_df["Task Name"].tolist(),
                        key="ad_task_sel",
                    )
                    _ad_year = _ad2.selectbox("Year *", _plan_years, key="ad_year_sel")
                    _ad_amount = _ad3.number_input(
                        "Annual Total (€) *", min_value=0.0, step=500.0, key="ad_amount"
                    )

                    # Preview: months in selected year that exist in the plan
                    _yr_months = [ym for ym in all_months if ym.startswith(_ad_year if _ad_year else "")]
                    if _yr_months:
                        _per_m = (_ad_amount / len(_yr_months)) if _ad_amount > 0 else 0
                        st.info(
                            f"**{len(_yr_months)} months** in {_ad_year} "
                            f"({_yr_months[0]} → {_yr_months[-1]}) → "
                            f"**€ {_per_m:,.2f} / month**"
                        )

                    if st.form_submit_button("📅 Distribute Annual Amount"):
                        if not current_user:
                            st.error("⚠️ Please enter your name (for audit trail) before saving.")
                        elif _ad_amount <= 0:
                            st.error("Please enter an amount greater than zero.")
                        elif not _yr_months:
                            st.error(f"No plan months found for year {_ad_year}.")
                        else:
                            # Look up the task ID
                            _match = plan_df[plan_df["Task Name"] == _ad_task]
                            if _match.empty:
                                st.error("Task not found.")
                            else:
                                _ad_tid = int(_match.iloc[0]["_task_id"])
                                _per_month_val = _ad_amount / len(_yr_months)
                                _dist_count = 0
                                for _ym in _yr_months:
                                    _old_v = float(_match.iloc[0].get(_ym, 0) or 0)
                                    db.upsert_plan_value(_ad_tid, _ym, _per_month_val, current_user)
                                    db.add_plan_audit(
                                        project_id, _ad_tid, _ad_task, _ym,
                                        _old_v, _per_month_val, "annual_distribute", current_user
                                    )
                                    _dist_count += 1
                                st.success(
                                    f"✅ Distributed € {_ad_amount:,.2f} across {_dist_count} months "
                                    f"(€ {_per_month_val:,.2f} each) for **{_ad_task}** in {_ad_year}."
                                )
                                st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # TAB: Import from Excel
    # ════════════════════════════════════════════════════════════════════════
    with tab_import:
        st.markdown(
            "Upload your existing Excel or CSV budget plan to import tasks and monthly values. "
            "Map columns to the correct fields below."
        )
        uploaded_plan = st.file_uploader(
            "Upload Budget Plan file", type=["xlsx", "xls", "csv"], key="plan_import_upload"
        )
        if uploaded_plan:
            try:
                if uploaded_plan.name.lower().endswith(".csv"):
                    df_imp = pd.read_csv(uploaded_plan)
                else:
                    _xls = pd.ExcelFile(uploaded_plan)
                    _imp_sheet = st.selectbox("Sheet", _xls.sheet_names, key="plan_imp_sheet")
                    df_imp = pd.read_excel(uploaded_plan, sheet_name=_imp_sheet)

                st.write(f"Preview ({len(df_imp)} rows):", df_imp.head(8))

                _imp_cols = ["(none)"] + list(df_imp.columns)
                _d_year = guess_column(df_imp.columns, ["year (yyyy)", "year", "yyyy"])
                _d_month = guess_column(df_imp.columns, ["month (mm)", "month", "mm"])
                _d_task = guess_column(df_imp.columns, ["task id t", "task id", "task", "task name"])
                _d_rg_type = guess_column(df_imp.columns, ["sp rg type t", "rg type", "resource group type"])
                _d_rg = guess_column(df_imp.columns, ["sp rg res group t", "resource group", "res group", "rg"])
                _d_wbs = guess_column(df_imp.columns, ["wbs element k", "wbs element", "wbs"])
                _d_mcr_no = guess_column(df_imp.columns, ["prj mcr number", "mcr number", "project mcr"])
                _d_mcr_name = guess_column(df_imp.columns, ["prj mcr - name", "prj mcr name", "mcr name"])
                _d_amount = guess_column(df_imp.columns, ["amount", "total", "value"])
                _d_hours = guess_column(df_imp.columns, ["hours", "hour"])
                _d_rate = guess_column(df_imp.columns, ["rate", "rate / hour", "rate per hour"])

                st.markdown("**3.2.1 Map row-based import columns:**")
                _r1, _r2, _r3, _r4 = st.columns(4)
                _imp_year_col = _r1.selectbox("Year (YYYY) *", _imp_cols, index=_imp_cols.index(_d_year) if _d_year in _imp_cols else 0, key="imp_row_year_col")
                _imp_month_col = _r2.selectbox("Month (MM) *", _imp_cols, index=_imp_cols.index(_d_month) if _d_month in _imp_cols else 0, key="imp_row_month_col")
                _imp_task_col = _r3.selectbox("Task ID T *", _imp_cols, index=_imp_cols.index(_d_task) if _d_task in _imp_cols else 0, key="imp_row_task_col")
                _imp_amount_col = _r4.selectbox("Amount *", _imp_cols, index=_imp_cols.index(_d_amount) if _d_amount in _imp_cols else 0, key="imp_row_amount_col")

                _r5, _r6, _r7, _r8 = st.columns(4)
                _imp_rg_type_col = _r5.selectbox("SP RG Type T", _imp_cols, index=_imp_cols.index(_d_rg_type) if _d_rg_type in _imp_cols else 0, key="imp_row_rg_type_col")
                _imp_rg_col = _r6.selectbox("SP RG Res Group T", _imp_cols, index=_imp_cols.index(_d_rg) if _d_rg in _imp_cols else 0, key="imp_row_rg_col")
                _imp_wbs_col = _r7.selectbox("WBS Element K", _imp_cols, index=_imp_cols.index(_d_wbs) if _d_wbs in _imp_cols else 0, key="imp_row_wbs_col")
                _imp_hours_col = _r8.selectbox("Hours", _imp_cols, index=_imp_cols.index(_d_hours) if _d_hours in _imp_cols else 0, key="imp_row_hours_col")

                _r9, _r10, _r11 = st.columns(3)
                _imp_rate_col = _r9.selectbox("Rate", _imp_cols, index=_imp_cols.index(_d_rate) if _d_rate in _imp_cols else 0, key="imp_row_rate_col")
                _imp_mcr_no_col = _r10.selectbox("PRJ MCR number", _imp_cols, index=_imp_cols.index(_d_mcr_no) if _d_mcr_no in _imp_cols else 0, key="imp_row_mcr_no_col")
                _imp_mcr_name_col = _r11.selectbox("PRJ MCR - Name", _imp_cols, index=_imp_cols.index(_d_mcr_name) if _d_mcr_name in _imp_cols else 0, key="imp_row_mcr_name_col")

                _def_ctype = st.selectbox(
                    "Default Cost Type",
                    ["Direct cost", "Labor cost"],
                    key="imp_row_default_ct",
                )

                if st.button("⬇️ Import row-based plan", key="do_plan_row_import"):
                    if not current_user:
                        st.error("Please set your name for the audit trail.")
                    elif _imp_year_col == "(none)" or _imp_month_col == "(none)" or _imp_task_col == "(none)" or _imp_amount_col == "(none)":
                        st.error("Year, Month, Task ID T, and Amount mappings are required.")
                    else:
                        # Ensure helper and metadata custom columns exist.
                        _hours_key = "hours_annually"
                        _rate_key = "rate_per_hour"
                        _total_key = "total"
                        _mcr_no_key = "custom_project_mcr"
                        _mcr_name_key = "prj_mcr_name"
                        _wbs_key = "wbs_element_k"
                        _rg_type_key = "sp_rg_type_t"

                        if _imp_hours_col != "(none)":
                            db.add_plan_column(project_id, _hours_key, "Hours (annually)", col_type="custom", data_type="decimal", sort_order=60)
                        if _imp_rate_col != "(none)":
                            db.add_plan_column(project_id, _rate_key, "Rate / hour", col_type="custom", data_type="currency_eur", sort_order=59)
                        db.add_plan_column(project_id, _total_key, "Total", col_type="custom", data_type="currency_eur", sort_order=61)
                        if _imp_mcr_no_col != "(none)":
                            db.add_plan_column(project_id, _mcr_no_key, "Project MCR #", col_type="custom", data_type="text", sort_order=70)
                        if _imp_mcr_name_col != "(none)":
                            db.add_plan_column(project_id, _mcr_name_key, "PRJ MCR - Name", col_type="custom", data_type="text", sort_order=71)
                        if _imp_wbs_col != "(none)":
                            db.add_plan_column(project_id, _wbs_key, "WBS Element K", col_type="custom", data_type="text", sort_order=72)
                        if _imp_rg_type_col != "(none)":
                            db.add_plan_column(project_id, _rg_type_key, "SP RG Type T", col_type="custom", data_type="text", sort_order=73)

                        def _clean_text(_v):
                            if pd.isna(_v):
                                return ""
                            _s = str(_v).strip()
                            return "" if _s.lower() in ("nan", "none") else _s

                        def _parse_year_month(_y_raw, _m_raw):
                            _y = pd.to_numeric(pd.Series([_y_raw]), errors="coerce").iloc[0]
                            _m = pd.to_numeric(pd.Series([_m_raw]), errors="coerce").iloc[0]
                            if pd.isna(_y) or pd.isna(_m):
                                try:
                                    _dt = pd.to_datetime(f"{str(_y_raw).strip()}-{str(_m_raw).strip()}-01", errors="coerce")
                                    if pd.isna(_dt):
                                        return None
                                    return _dt.strftime("%Y-%m")
                                except Exception:
                                    return None
                            _yy = int(float(_y))
                            _mm = int(float(_m))
                            if _yy < 100:
                                _yy += 2000
                            if _mm < 1 or _mm > 12:
                                return None
                            return f"{_yy:04d}-{_mm:02d}"

                        _agg = {}
                        _skipped_empty = 0
                        _skipped_month = 0
                        for _, _ir in df_imp.iterrows():
                            _task_name = _clean_text(_ir[_imp_task_col])
                            if not _task_name:
                                _skipped_empty += 1
                                continue

                            _ym = _parse_year_month(_ir[_imp_year_col], _ir[_imp_month_col])
                            if not _ym or _ym not in all_months:
                                _skipped_month += 1
                                continue

                            _amt = parse_amount(_ir[_imp_amount_col])
                            if _amt is None:
                                _amt = 0.0

                            _rg_type = _clean_text(_ir[_imp_rg_type_col]) if _imp_rg_type_col != "(none)" else ""
                            _rg = _clean_text(_ir[_imp_rg_col]) if _imp_rg_col != "(none)" else ""
                            _wbs = _clean_text(_ir[_imp_wbs_col]) if _imp_wbs_col != "(none)" else ""
                            _mcr_no = _clean_text(_ir[_imp_mcr_no_col]) if _imp_mcr_no_col != "(none)" else ""
                            _mcr_name = _clean_text(_ir[_imp_mcr_name_col]) if _imp_mcr_name_col != "(none)" else ""
                            _hours = parse_amount(_ir[_imp_hours_col]) if _imp_hours_col != "(none)" else None
                            _rate = parse_amount(_ir[_imp_rate_col]) if _imp_rate_col != "(none)" else None

                            _rt = _rg_type.lower()
                            _ctype = "Labor cost" if _rt == "human" else "Direct cost"

                            _k = (_task_name, _ctype, _rg)
                            if _k not in _agg:
                                _agg[_k] = {
                                    "task_name": _task_name,
                                    "cost_type": _ctype,
                                    "resource_group": _rg,
                                    "months": {},
                                    "hours_sum": 0.0,
                                    "rate_weighted": 0.0,
                                    "rate_hours": 0.0,
                                    "rate_samples": [],
                                    "amount_total": 0.0,
                                    "mcr_no": _mcr_no,
                                    "mcr_name": _mcr_name,
                                    "wbs": _wbs,
                                    "rg_type": _rg_type,
                                }
                            _e = _agg[_k]
                            _e["months"][_ym] = float(_e["months"].get(_ym, 0.0) or 0.0) + float(_amt)
                            _e["amount_total"] += float(_amt)
                            if _hours is not None:
                                _e["hours_sum"] += float(_hours)
                            if _rate is not None:
                                if _hours is not None and float(_hours) != 0:
                                    _e["rate_weighted"] += float(_rate) * float(_hours)
                                    _e["rate_hours"] += float(_hours)
                                else:
                                    _e["rate_samples"].append(float(_rate))
                            if not _e["mcr_no"] and _mcr_no:
                                _e["mcr_no"] = _mcr_no
                            if not _e["mcr_name"] and _mcr_name:
                                _e["mcr_name"] = _mcr_name
                            if not _e["wbs"] and _wbs:
                                _e["wbs"] = _wbs
                            if not _e["rg_type"] and _rg_type:
                                _e["rg_type"] = _rg_type

                        _imp_tasks = 0
                        _imp_vals = 0
                        for _e in _agg.values():
                            _new_tid = db.add_plan_task(
                                project_id,
                                _e["task_name"],
                                _e["cost_type"],
                                _e["resource_group"],
                                "",
                            )
                            db.add_plan_audit(project_id, _new_tid, _e["task_name"], "", "", "", "import", current_user)
                            _imp_tasks += 1

                            _is_human = str(_e.get("rg_type", "") or "").strip().lower() == "human"
                            _calc_mode_import = "Calculated" if _is_human else "Direct"
                            db.upsert_plan_value(
                                _new_tid,
                                _calc_mode_store_key,
                                1.0 if _calc_mode_import == "Calculated" else 0.0,
                                current_user,
                            )
                            db.add_plan_audit(
                                project_id,
                                _new_tid,
                                _e["task_name"],
                                "_calc_mode",
                                "",
                                _calc_mode_import,
                                "import",
                                current_user,
                            )
                            _imp_vals += 1

                            _rate_final = None
                            if _imp_rate_col != "(none)":
                                if _e["rate_hours"] > 0:
                                    _rate_final = _e["rate_weighted"] / _e["rate_hours"]
                                elif _e["rate_samples"]:
                                    _rate_final = sum(_e["rate_samples"]) / len(_e["rate_samples"])

                            if _calc_mode_import == "Calculated":
                                _months_for_dist = sorted(_e["months"].keys()) if _e["months"] else list(all_months)
                                _calc_total = float(_e["amount_total"])
                                if _rate_final is not None and float(_e["hours_sum"]) != 0:
                                    _calc_total = float(_rate_final) * float(_e["hours_sum"])
                                _per_month = (_calc_total / len(_months_for_dist)) if _months_for_dist else 0.0
                                for _ym in _months_for_dist:
                                    db.upsert_plan_value(_new_tid, _ym, float(_per_month), current_user)
                                    db.add_plan_audit(project_id, _new_tid, _e["task_name"], _ym, 0, float(_per_month), "import", current_user)
                                    _imp_vals += 1
                                db.upsert_plan_value(_new_tid, _total_key, float(_calc_total), current_user)
                                db.add_plan_audit(project_id, _new_tid, _e["task_name"], _total_key, 0, float(_calc_total), "import", current_user)
                                _imp_vals += 1
                            else:
                                for _ym, _v in _e["months"].items():
                                    if _v != 0:
                                        db.upsert_plan_value(_new_tid, _ym, float(_v), current_user)
                                        db.add_plan_audit(project_id, _new_tid, _e["task_name"], _ym, 0, float(_v), "import", current_user)
                                        _imp_vals += 1

                                _direct_total = float(sum(float(_v or 0.0) for _v in _e["months"].values()))
                                db.upsert_plan_value(_new_tid, _total_key, _direct_total, current_user)
                                db.add_plan_audit(project_id, _new_tid, _e["task_name"], _total_key, 0, _direct_total, "import", current_user)
                                _imp_vals += 1

                            if _imp_hours_col != "(none)" and _e["hours_sum"] != 0:
                                db.upsert_plan_value(_new_tid, _hours_key, float(_e["hours_sum"]), current_user)
                                db.add_plan_audit(project_id, _new_tid, _e["task_name"], _hours_key, 0, float(_e["hours_sum"]), "import", current_user)
                                _imp_vals += 1

                            if _imp_rate_col != "(none)":
                                if _rate_final is not None:
                                    db.upsert_plan_value(_new_tid, _rate_key, float(_rate_final), current_user)
                                    db.add_plan_audit(project_id, _new_tid, _e["task_name"], _rate_key, 0, float(_rate_final), "import", current_user)
                                    _imp_vals += 1

                            if _imp_mcr_no_col != "(none)" and _e["mcr_no"]:
                                db.upsert_plan_text_value(_new_tid, _mcr_no_key, _e["mcr_no"], current_user)
                                db.add_plan_audit(project_id, _new_tid, _e["task_name"], _mcr_no_key, "", _e["mcr_no"], "import", current_user)
                                _imp_vals += 1
                            if _imp_mcr_name_col != "(none)" and _e["mcr_name"]:
                                db.upsert_plan_text_value(_new_tid, _mcr_name_key, _e["mcr_name"], current_user)
                                db.add_plan_audit(project_id, _new_tid, _e["task_name"], _mcr_name_key, "", _e["mcr_name"], "import", current_user)
                                _imp_vals += 1
                            if _imp_wbs_col != "(none)" and _e["wbs"]:
                                db.upsert_plan_text_value(_new_tid, _wbs_key, _e["wbs"], current_user)
                                db.add_plan_audit(project_id, _new_tid, _e["task_name"], _wbs_key, "", _e["wbs"], "import", current_user)
                                _imp_vals += 1
                            if _imp_rg_type_col != "(none)" and _e["rg_type"]:
                                db.upsert_plan_text_value(_new_tid, _rg_type_key, _e["rg_type"], current_user)
                                db.add_plan_audit(project_id, _new_tid, _e["task_name"], _rg_type_key, "", _e["rg_type"], "import", current_user)
                                _imp_vals += 1

                        st.success(f"Imported {_imp_tasks} tasks with {_imp_vals} values from row-based file.")
                        if _skipped_empty:
                            st.warning(f"Skipped {_skipped_empty} rows with empty task.")
                        if _skipped_month:
                            st.warning(f"Skipped {_skipped_month} rows with invalid/out-of-range Year-Month.")
                        st.session_state.pop("plan_data_editor", None)
                        st.rerun()

            except Exception as _imp_ex:
                st.error(f"Import error: {_imp_ex}")

    # ════════════════════════════════════════════════════════════════════════
    # TAB: Baselines
    # ════════════════════════════════════════════════════════════════════════
    with tab_baselines:
        st.markdown(
            "Freeze the current budget plan as a named baseline. "
            "Baselines are read-only snapshots that can be downloaded for MCR or archival."
        )
        with st.expander("🧊 3.3.1 Create / Update Baseline", expanded=True):
            _common_bl = ["BP26", "CF01", "CF02", "CF03", "CF04", "CF05",
                          "CF06", "CF07", "CF08", "CF09", "CF10", "CF11", "CF12", "Custom..."]
            with st.form("create_baseline_form"):
                _bl1, _bl2 = st.columns(2)
                _bl_sel    = _bl1.selectbox("Baseline name", _common_bl, key="bl_name_select")
                _bl_custom = _bl2.text_input("Custom name (if 'Custom...' selected)", key="bl_name_custom")
                if st.form_submit_button("🧊 Freeze Current Plan as Baseline"):
                    if not current_user:
                        st.error("Please set your name for the audit trail.")
                    else:
                        _bl_name = _bl_custom.strip() if _bl_sel == "Custom..." else _bl_sel
                        if not _bl_name:
                            st.error("Baseline name required.")
                        else:
                            _snap = {
                                "created_by": current_user,
                                "project_name": project["name"],
                                "tasks": plan_df.to_dict(orient="records"),
                            }
                            _bl_result = db.create_plan_baseline(
                                project_id, _bl_name, json.dumps(_snap), current_user
                            )
                            db.add_plan_audit(
                                project_id, None, "", "", "", _bl_name, "baseline", current_user
                            )
                            _verb = "Created" if _bl_result == "created" else "Updated"
                            st.success(f"✅ {_verb} baseline: **{_bl_name}**")
                            st.rerun()

        _baselines = db.get_plan_baselines(project_id)
        if _baselines:
            st.markdown(f"**3.3.2 Existing baselines ({len(_baselines)}):**")
            for _bl in _baselines:
                with st.expander(
                    f"🧊 {_bl['baseline_name']} — {_bl['created_at'][:10]} by {_bl['created_by'] or '(unknown)'}"
                ):
                    _full_bl = db.get_plan_baseline(project_id, _bl["baseline_name"])
                    if _full_bl:
                        try:
                            _snap_data = json.loads(_full_bl["snapshot_json"])
                            _snap_tasks = _snap_data.get("tasks", [])
                            _snap_df = (
                                pd.DataFrame(_snap_tasks).drop(columns=["_task_id"], errors="ignore")
                                if _snap_tasks else pd.DataFrame()
                            )
                            if not _snap_df.empty:
                                st.dataframe(_snap_df, hide_index=True, use_container_width=True)
                                _bl_buf = BytesIO()
                                with pd.ExcelWriter(_bl_buf, engine="openpyxl") as _bl_ew:
                                    _snap_df.to_excel(_bl_ew, index=False, sheet_name=_bl["baseline_name"][:31])
                                st.download_button(
                                    f"📥 Download {_bl['baseline_name']} as Excel",
                                    data=_bl_buf.getvalue(),
                                    file_name=f"baseline_{_bl['baseline_name']}_{project['name'].replace(' ', '_')}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    key=f"dl_baseline_{_bl['id']}",
                                )
                            else:
                                st.info("Empty snapshot.")
                        except Exception as _bl_ex:
                            st.error(f"Error loading baseline: {_bl_ex}")
                    if st.button(
                        f"🗑️ Delete baseline '{_bl['baseline_name']}'", key=f"del_bl_{_bl['id']}"
                    ):
                        db.delete_plan_baseline(_bl["id"])
                        st.success("Baseline deleted.")
                        st.rerun()
        else:
            st.info("No baselines yet. Use the form above to freeze the current plan.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB: Columns
    # ════════════════════════════════════════════════════════════════════════
    with tab_columns:
        st.markdown(
            "Month columns are derived automatically from the project start/end dates. "
            "Add custom columns below (e.g. headcount, contingency %, notes)."
        )
        st.markdown(f"**3.4.1 Month columns:** {', '.join(f'`{_month_label(m)}`' for m in all_months)}")
        st.markdown("---")
        st.markdown("**3.4.2 Custom columns:**")
        _dtype_options = ["decimal", "integer", "currency_eur", "text"]
        _dtype_labels = {
            "decimal": "decimal",
            "integer": "integer",
            "currency_eur": "currency (EUR)",
            "text": "text",
        }

        def _format_dtype(_dtype_key):
            return _dtype_labels.get(_dtype_key, _dtype_key)

        with st.form("add_col_form"):
            _cc1, _cc2, _cc3 = st.columns(3)
            _new_cl = _cc1.text_input("Column Label *", placeholder="e.g. Headcount FTE")
            _new_ck = _cc2.text_input("Column key (auto if blank)", placeholder="e.g. headcount_fte")
            _new_dt = _cc3.selectbox(
                "Data Type",
                _dtype_options,
                key="new_col_data_type",
                format_func=_format_dtype,
            )
            if st.form_submit_button("➕ Add Column"):
                if not _new_cl.strip():
                    st.error("Column label is required.")
                else:
                    _ck_auto = default_plan_column_key(_new_cl.strip(), _new_ck.strip())
                    db.add_plan_column(project_id, _ck_auto, _new_cl.strip(), data_type=_new_dt)
                    st.success(f"Added column: **{_new_cl.strip()}**")
                    st.rerun()

        if _custom_cols_meta:
            for _cc in _custom_cols_meta:
                _cca, _ccb, _ccc, _ccd = st.columns([4, 2, 1, 1])
                _cca.write(f"**{_cc['col_label']}** (key: `{_cc['col_key']}`)")
                _dtype_val = (_cc.get("data_type") or "decimal").lower()
                _dtype_idx = _dtype_options.index(_dtype_val) if _dtype_val in _dtype_options else 0
                _new_dtype_val = _ccb.selectbox(
                    "Data Type",
                    _dtype_options,
                    index=_dtype_idx,
                    key=f"col_dtype_{_cc['id']}",
                    label_visibility="collapsed",
                    format_func=_format_dtype,
                )
                if _ccc.button("💾 Type", key=f"save_col_dtype_{_cc['id']}"):
                    db.update_plan_column_data_type(_cc["id"], _new_dtype_val)
                    st.success(f"Updated data type: {_cc['col_label']} -> {_new_dtype_val}")
                    st.rerun()
                if _ccd.button("🗑️ Delete", key=f"del_col_{_cc['id']}"):
                    db.delete_plan_column(_cc["id"])
                    st.success(f"Deleted column: {_cc['col_label']}")
                    st.rerun()
        else:
            st.info("No custom columns added yet.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB: Audit Log
    # ════════════════════════════════════════════════════════════════════════
    with tab_audit:
        st.markdown("3.5.1 Full history of all plan changes — who changed what and when.")
        _audit_rows = db.get_plan_audit_log(project_id, limit=500)
        if _audit_rows:
            _df_audit = pd.DataFrame(_audit_rows)[
                ["changed_at", "changed_by", "action", "task_name", "col_key", "old_value", "new_value"]
            ].rename(columns={
                "changed_at": "When",
                "changed_by": "Who",
                "action": "Action",
                "task_name": "Task",
                "col_key": "Field / Column",
                "old_value": "Old Value",
                "new_value": "New Value",
            })
            _al_f1, _al_f2 = st.columns(2)
            _al_users   = sorted(_df_audit["Who"].dropna().unique().tolist())
            _al_actions = sorted(_df_audit["Action"].dropna().unique().tolist())
            _sel_users   = _al_f1.multiselect("Filter by Who", _al_users, default=_al_users, key="audit_who")
            _sel_actions = _al_f2.multiselect("Filter by Action", _al_actions, default=_al_actions, key="audit_action")
            _df_audit_f = _df_audit[
                _df_audit["Who"].isin(_sel_users) & _df_audit["Action"].isin(_sel_actions)
            ]
            st.dataframe(_df_audit_f, hide_index=True, use_container_width=True)
        else:
            st.info("No audit log entries yet for this project.")
# ════════════════════════════════════════════════════════════════════════════
# 4. ACTUALS
# ════════════════════════════════════════════════════════════════════════════
elif page == "📥 Actuals":
    st.title("📥 4. Actuals Entry")
    st.caption("Reference: 4.1 Manual Entry | 4.2 Import | 4.3 Mapping | 4.4 View Actuals")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    tab1, tab2, tab3, tab4 = st.tabs([
        "✏️ 4.1 Manual Entry",
        "📂 4.2 Import from CSV/Excel (MCR)",
        "🗺️ 4.3 Mapping",
        "👀 4.4 View Actuals",
    ])

    with tab1:
        with st.form("add_actual_form"):
            c1, c2, c3 = st.columns(3)
            category = c1.selectbox("Category", CATEGORIES)
            amount = c2.number_input("Amount (€)", min_value=0.0, step=10.0)
            date = c3.date_input("Date", value=datetime.date.today())
            description = st.text_input("Description")
            if st.form_submit_button("Add Actual"):
                db.add_actual(project_id, category, description, amount, str(date), "Manual")
                st.success("Actual entry added!")
                st.rerun()

    with tab2:
        import_cost_tab, import_labour_tab = st.tabs([
            "💳 4.2.1 Other Cost Actuals Import",
            "👷 4.2.2 Employee (Labour) Actuals Import",
        ])

        with import_cost_tab:
            st.markdown("Upload non-labour cost actuals file (materials, travel, services, software, etc.).")
            uploaded_cost = st.file_uploader("Upload cost actuals file", type=["csv", "xlsx", "xls"], key="cost_actuals_upload")
            if uploaded_cost:
                try:
                    df_up = pd.read_csv(uploaded_cost) if uploaded_cost.name.lower().endswith(".csv") else pd.read_excel(uploaded_cost)
                    st.write("Preview (first 5 rows):", df_up.head())

                    cols = ["(none)"] + list(df_up.columns)
                    default_amount = guess_column(df_up.columns, ["amount", "cost", "value", "actuals", "actual amount", "booked amount"])
                    default_task_name = guess_column(df_up.columns, ["task", "task name", "work package", "activity"])
                    default_booking_year = guess_column(df_up.columns, ["booking year", "year", "fiscal year"])
                    default_booking_month = guess_column(df_up.columns, ["booking month", "month", "fiscal month", "period month"])
                    default_fin_document = guess_column(df_up.columns, ["financial document", "document", "document number", "invoice", "invoice number"])
                    default_fin_posting_date = guess_column(df_up.columns, ["financial document posting date", "posting date", "document date", "invoice date", "issue date"])
                    default_project_no = guess_column(df_up.columns, ["project no", "project_no", "project", "project number"])
                    default_category = guess_column(df_up.columns, ["category", "cost category", "cost type", "type"])
                    default_wbs_number = guess_column(df_up.columns, ["wbs", "wbs number", "wbs element", "wbs no"])
                    default_rg = guess_column(df_up.columns, ["resource group", "resource_group", "rg", "team", "cost center"])

                    profiles = db.get_import_profiles("actuals_cost")
                    profile_names = [p["profile_name"] for p in profiles]
                    selected_profile = st.selectbox("Cost import profile", ["(none)"] + profile_names, key="cost_profile_select")
                    selected_settings = next((p["settings"] for p in profiles if p["profile_name"] == selected_profile), {}) if selected_profile != "(none)" else {}

                    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
                    col_map = {}
                    pref_amount = selected_settings.get("col_map", {}).get("amount", default_amount)
                    pref_task_name = selected_settings.get("col_map", {}).get("task_name", default_task_name)
                    pref_booking_year = selected_settings.get("col_map", {}).get("booking_year", default_booking_year)
                    pref_booking_month = selected_settings.get("col_map", {}).get("booking_month", default_booking_month)
                    pref_fin_document = selected_settings.get("col_map", {}).get("financial_document", default_fin_document)
                    pref_fin_posting_date = selected_settings.get("col_map", {}).get("financial_document_posting_date", default_fin_posting_date)
                    pref_project_no = selected_settings.get("col_map", {}).get("project_no", default_project_no)
                    pref_category = selected_settings.get("col_map", {}).get("category", default_category)
                    pref_wbs_number = selected_settings.get("col_map", {}).get("wbs_number", default_wbs_number)
                    pref_rg = selected_settings.get("col_map", {}).get("resource_group", default_rg)

                    col_map["resource_group"] = mc1.selectbox("Resource group", cols, index=cols.index(pref_rg) if pref_rg in cols else 0, key="cost_map_rg")
                    col_map["project_no"] = mc2.selectbox("Project no.", cols, index=cols.index(pref_project_no) if pref_project_no in cols else 0, key="cost_map_project")
                    col_map["wbs_number"] = mc3.selectbox("WBS Number", cols, index=cols.index(pref_wbs_number) if pref_wbs_number in cols else 0, key="cost_map_wbs")
                    col_map["task_name"] = mc4.selectbox("Task name", cols, index=cols.index(pref_task_name) if pref_task_name in cols else 0, key="cost_map_task")
                    col_map["amount"] = mc5.selectbox("Cost (EUR)", cols, index=cols.index(pref_amount) if pref_amount in cols else 0, key="cost_map_amt")

                    oc1, oc2, oc3, oc4, oc5 = st.columns(5)
                    col_map["booking_year"] = oc1.selectbox("Booking year", cols, index=cols.index(pref_booking_year) if pref_booking_year in cols else 0, key="cost_map_book_year")
                    col_map["booking_month"] = oc2.selectbox("Booking month", cols, index=cols.index(pref_booking_month) if pref_booking_month in cols else 0, key="cost_map_book_month")
                    col_map["financial_document"] = oc3.selectbox("Financial document", cols, index=cols.index(pref_fin_document) if pref_fin_document in cols else 0, key="cost_map_fin_doc")
                    col_map["financial_document_posting_date"] = oc4.selectbox("Financial document posting date", cols, index=cols.index(pref_fin_posting_date) if pref_fin_posting_date in cols else 0, key="cost_map_fin_posting_date")
                    col_map["category"] = oc5.selectbox("Category", cols, index=cols.index(pref_category) if pref_category in cols else 0, key="cost_map_category")

                    cc1, cc2 = st.columns(2)
                    default_category_if_missing = cc1.selectbox("Default category if missing", CATEGORIES, index=CATEGORIES.index(selected_settings.get("default_category_if_missing", "Other")) if selected_settings.get("default_category_if_missing", "Other") in CATEGORIES else CATEGORIES.index("Other"), key="cost_default_cat")
                    amount_mode = cc2.selectbox("Amount sign handling", ["Keep as in file", "Convert to positive (absolute)", "Invert sign"], index=["Keep as in file", "Convert to positive (absolute)", "Invert sign"].index(selected_settings.get("amount_mode", "Keep as in file")) if selected_settings.get("amount_mode", "Keep as in file") in ["Keep as in file", "Convert to positive (absolute)", "Invert sign"] else 0, key="cost_amt_mode")

                    pc1, pc2, pc3 = st.columns([2, 1, 1])
                    profile_name_input = pc1.text_input("Cost profile name", value=selected_profile if selected_profile != "(none)" else "", key="cost_profile_name")
                    if pc2.button("💾 Save Cost Profile", key="save_cost_profile"):
                        if profile_name_input.strip():
                            db.save_import_profile(profile_name_input.strip(), {"col_map": col_map, "default_category_if_missing": default_category_if_missing, "amount_mode": amount_mode}, "actuals_cost")
                            st.success("Cost profile saved.")
                            st.rerun()
                    if pc3.button("🗑️ Delete Cost Profile", key="del_cost_profile") and selected_profile != "(none)":
                        db.delete_import_profile(selected_profile, "actuals_cost")
                        st.success("Cost profile deleted.")
                        st.rerun()

                    preview_rows = []
                    for _, row in df_up.iterrows():
                        rg_val = str(row[col_map["resource_group"]]).strip() if col_map["resource_group"] != "(none)" else ""
                        project_no_val = str(row[col_map["project_no"]]).strip() if col_map["project_no"] != "(none)" else ""
                        wbs_number_val = str(row[col_map["wbs_number"]]).strip() if col_map["wbs_number"] != "(none)" else ""
                        task_name_val = str(row[col_map["task_name"]]).strip() if col_map["task_name"] != "(none)" else ""
                        booking_year_val = str(row[col_map["booking_year"]]).strip() if col_map["booking_year"] != "(none)" else ""
                        booking_month_val = str(row[col_map["booking_month"]]).strip() if col_map["booking_month"] != "(none)" else ""
                        financial_document_val = str(row[col_map["financial_document"]]).strip() if col_map["financial_document"] != "(none)" else ""
                        financial_document_posting_date_val = str(row[col_map["financial_document_posting_date"]]).strip() if col_map["financial_document_posting_date"] != "(none)" else ""
                        category_val = str(row[col_map["category"]]).strip() if col_map["category"] != "(none)" else ""
                        rg_val = "" if rg_val.lower() == "nan" else rg_val
                        project_no_val = "" if project_no_val.lower() == "nan" else project_no_val
                        wbs_number_val = "" if wbs_number_val.lower() == "nan" else wbs_number_val
                        task_name_val = "" if task_name_val.lower() == "nan" else task_name_val
                        booking_year_val = "" if booking_year_val.lower() == "nan" else booking_year_val
                        booking_month_val = "" if booking_month_val.lower() == "nan" else booking_month_val
                        financial_document_val = "" if financial_document_val.lower() == "nan" else financial_document_val
                        financial_document_posting_date_val = "" if financial_document_posting_date_val.lower() == "nan" else financial_document_posting_date_val
                        category_val = "" if category_val.lower() == "nan" else category_val
                        category_val = category_val or default_category_if_missing

                        amt = parse_amount(row[col_map["amount"]]) if col_map["amount"] != "(none)" else None
                        if amt is not None:
                            if amount_mode == "Convert to positive (absolute)":
                                amt = abs(amt)
                            elif amount_mode == "Invert sign":
                                amt = -amt

                        dt = str(datetime.date.today())
                        try:
                            by = int(float(booking_year_val)) if booking_year_val else None
                            bm = int(float(booking_month_val)) if booking_month_val else None
                            if by and bm and 1 <= bm <= 12:
                                dt = f"{by:04d}-{bm:02d}-01"
                        except Exception:
                            dt = str(datetime.date.today())

                        desc_parts = []
                        if task_name_val:
                            desc_parts.append(task_name_val)
                        if financial_document_val:
                            desc_parts.append(f"FinDoc: {financial_document_val}")
                        if financial_document_posting_date_val:
                            desc_parts.append(f"PostingDate: {financial_document_posting_date_val}")
                        desc = " | ".join(desc_parts)

                        preview_rows.append({
                            "Date": dt,
                            "Task name": task_name_val,
                            "Resource group": rg_val,
                            "Project no.": project_no_val,
                            "WBS Number": wbs_number_val,
                            "Cost (EUR)": amt,
                            "Booking year": booking_year_val,
                            "Booking month": booking_month_val,
                            "Financial document": financial_document_val,
                            "Financial document posting date": financial_document_posting_date_val,
                            "Category": category_val,
                            "Description": desc,
                        })

                    edited_preview = st.data_editor(
                        pd.DataFrame(preview_rows),
                        use_container_width=True,
                        hide_index=True,
                        num_rows="dynamic",
                        key="cost_import_preview",
                        column_config={
                            "Date": st.column_config.TextColumn("Date (YYYY-MM-DD)"),
                            "Task name": st.column_config.TextColumn("Task name"),
                            "Resource group": st.column_config.TextColumn("Resource group"),
                            "Project no.": st.column_config.TextColumn("Project no."),
                            "WBS Number": st.column_config.TextColumn("WBS Number"),
                            "Cost (EUR)": st.column_config.NumberColumn("Cost (EUR)", step=1.0),
                            "Booking year": st.column_config.TextColumn("Booking year"),
                            "Booking month": st.column_config.TextColumn("Booking month"),
                            "Financial document": st.column_config.TextColumn("Financial document"),
                            "Financial document posting date": st.column_config.TextColumn("Financial document posting date"),
                            "Category": st.column_config.TextColumn("Category"),
                            "Description": st.column_config.TextColumn("Description"),
                        },
                    )
                    import_only_positive = st.checkbox("Import only positive amounts", value=True, key="cost_positive_only")

                    if st.button("Import Cost Preview Rows", key="import_cost_preview"):
                        imported, skipped = 0, 0
                        db.delete_actuals_by_source(project_id, "MCR Import (Cost)")
                        edited_preview["Cost (EUR)"] = pd.to_numeric(edited_preview["Cost (EUR)"], errors="coerce")
                        for _, prow in edited_preview.iterrows():
                            amt = prow["Cost (EUR)"]
                            if pd.isna(amt) or (import_only_positive and float(amt) <= 0):
                                skipped += 1
                                continue
                            cat = str(prow.get("Category", "") or "").strip()
                            if not cat or cat.lower() == "nan":
                                cat = default_category_if_missing
                            dt = parse_date(prow["Date"])
                            task_name_val = "" if pd.isna(prow.get("Task name", "")) else str(prow.get("Task name", "")).strip()
                            rg_val = "" if pd.isna(prow.get("Resource group", "")) else str(prow.get("Resource group", "")).strip()
                            project_no_val = "" if pd.isna(prow.get("Project no.", "")) else str(prow.get("Project no.", "")).strip()
                            wbs_number_val = "" if pd.isna(prow.get("WBS Number", "")) else str(prow.get("WBS Number", "")).strip()
                            financial_document_val = "" if pd.isna(prow.get("Financial document", "")) else str(prow.get("Financial document", "")).strip()
                            financial_document_posting_date_val = "" if pd.isna(prow.get("Financial document posting date", "")) else str(prow.get("Financial document posting date", "")).strip()
                            desc = "" if pd.isna(prow.get("Description", "")) else str(prow.get("Description", "")).strip()
                            if not desc:
                                desc_parts = []
                                if task_name_val:
                                    desc_parts.append(task_name_val)
                                if financial_document_val:
                                    desc_parts.append(f"FinDoc: {financial_document_val}")
                                if financial_document_posting_date_val:
                                    desc_parts.append(f"PostingDate: {financial_document_posting_date_val}")
                                desc = " | ".join(desc_parts)
                            db.add_actual(
                                project_id,
                                cat,
                                desc,
                                float(amt),
                                dt,
                                "MCR Import (Cost)",
                                task_name=task_name_val or None,
                                resource_group=rg_val or None,
                                project_no=project_no_val or None,
                                import_file=uploaded_cost.name if uploaded_cost else None,
                                wbs_number=wbs_number_val or None,
                                financial_document=financial_document_val or None,
                                financial_document_posting_date=financial_document_posting_date_val or None,
                            )
                            imported += 1
                        st.success(f"Imported {imported} cost rows.")
                        if skipped:
                            st.warning(f"Skipped {skipped} rows.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Cost import error: {e}")

        with import_labour_tab:
            st.markdown("Upload labour actuals file. This flow uses employee/resource-group/project mapping to derive task/description.")
            last_labour_import = db.get_last_import_run(project_id, "labour_actuals")
            if last_labour_import:
                st.info(
                    f"Last labour file import: {last_labour_import.get('file_name') or '-'} | "
                    f"Rows: {last_labour_import.get('row_count', 0)} | "
                    f"Imported at: {last_labour_import.get('imported_at')}"
                )
            else:
                st.caption("No labour file imported yet for this project.")
            uploaded = st.file_uploader("Upload labour actuals file", type=["csv", "xlsx", "xls"], key="labour_actuals_upload")
            if uploaded:
                try:
                    df_up = pd.read_csv(uploaded) if uploaded.name.lower().endswith(".csv") else pd.read_excel(uploaded)
                    st.write("Preview (first 5 rows):", df_up.head())

                    col_map = {}
                    st.markdown("**4.2.2.1 Map your labour file columns to app input fields:**")
                    cols = ["(none)"] + list(df_up.columns)
                    default_amount = guess_column(df_up.columns, ["cost (eur)", "cost", "amount", "value", "actual amount", "booked amount"])
                    default_booking_year = guess_column(df_up.columns, ["booking year", "year", "fiscal year"])
                    default_booking_month = guess_column(df_up.columns, ["booking month", "month", "fiscal month", "period month"])
                    default_general_comment = guess_column(df_up.columns, ["general comment", "comment", "note", "text"])
                    default_reason_corr = guess_column(df_up.columns, ["reason for correction", "correction reason", "reason"])
                    default_project_no = guess_column(df_up.columns, ["project no", "project_no", "project", "project number", "wbs", "wbs element"])
                    default_employee = guess_column(df_up.columns, ["employee", "employee name", "name", "resource", "person"])
                    default_rg = guess_column(df_up.columns, ["resource group", "resource_group", "rg", "team", "cost center"])

                    profiles = db.get_import_profiles("actuals_labour")
                    profile_names = [p["profile_name"] for p in profiles]
                    selected_profile = st.selectbox("Labour import profile", ["(none)"] + profile_names, index=0, key="labour_profile_select")
                    selected_settings = next((p["settings"] for p in profiles if p["profile_name"] == selected_profile), {}) if selected_profile != "(none)" else {}

                    mc1, mc2, mc3, mc4 = st.columns(4)
                    pref_amount = selected_settings.get("col_map", {}).get("amount", default_amount)
                    pref_booking_year = selected_settings.get("col_map", {}).get("booking_year", default_booking_year)
                    pref_booking_month = selected_settings.get("col_map", {}).get("booking_month", default_booking_month)
                    pref_general_comment = selected_settings.get("col_map", {}).get("general_comment", default_general_comment)
                    pref_reason_corr = selected_settings.get("col_map", {}).get("reason_for_correction", default_reason_corr)
                    pref_project_no = selected_settings.get("col_map", {}).get("project_no", default_project_no)
                    pref_employee = selected_settings.get("col_map", {}).get("employee", default_employee)
                    pref_rg = selected_settings.get("col_map", {}).get("resource_group", default_rg)

                    col_map["employee"] = mc1.selectbox("Employee", cols, index=cols.index(pref_employee) if pref_employee in cols else 0, key="lab_map_employee")
                    col_map["resource_group"] = mc2.selectbox("Resource group", cols, index=cols.index(pref_rg) if pref_rg in cols else 0, key="lab_map_rg")
                    col_map["project_no"] = mc3.selectbox("Project no.", cols, index=cols.index(pref_project_no) if pref_project_no in cols else 0, key="lab_map_project")
                    col_map["amount"] = mc4.selectbox("Cost (EUR)", cols, index=cols.index(pref_amount) if pref_amount in cols else 0, key="lab_map_amt")

                    ec1, ec2, ec3 = st.columns(3)
                    col_map["booking_year"] = ec1.selectbox("Booking year", cols, index=cols.index(pref_booking_year) if pref_booking_year in cols else 0, key="lab_map_book_year")
                    col_map["booking_month"] = ec2.selectbox("Booking month", cols, index=cols.index(pref_booking_month) if pref_booking_month in cols else 0, key="lab_map_book_month")
                    col_map["general_comment"] = ec3.selectbox("General comment", cols, index=cols.index(pref_general_comment) if pref_general_comment in cols else 0, key="lab_map_gen_comment")

                    col_map["reason_for_correction"] = st.selectbox(
                        "Reason for correction",
                        cols,
                        index=cols.index(pref_reason_corr) if pref_reason_corr in cols else 0,
                        key="lab_map_reason_corr",
                    )

                    oc1 = st.columns(1)[0]
                    pref_amount_mode = selected_settings.get("amount_mode", "Keep as in file")
                    amount_mode = oc1.selectbox("Amount sign handling", ["Keep as in file", "Convert to positive (absolute)", "Invert sign"], index=["Keep as in file", "Convert to positive (absolute)", "Invert sign"].index(pref_amount_mode) if pref_amount_mode in ["Keep as in file", "Convert to positive (absolute)", "Invert sign"] else 0, key="lab_amt_mode")

                    pc1, pc2, pc3 = st.columns([2, 1, 1])
                    profile_name_input = pc1.text_input("Labour profile name", value=selected_profile if selected_profile != "(none)" else "", key="lab_profile_name")
                    save_profile_clicked = pc2.button("💾 Save Labour Profile", key="save_lab_profile")
                    delete_profile_clicked = pc3.button("🗑️ Delete Labour Profile", key="del_lab_profile")

                    current_profile_settings = {
                        "col_map": {
                            "employee": col_map["employee"],
                            "resource_group": col_map["resource_group"],
                            "project_no": col_map["project_no"],
                            "amount": col_map["amount"],
                            "booking_year": col_map["booking_year"],
                            "booking_month": col_map["booking_month"],
                            "general_comment": col_map["general_comment"],
                            "reason_for_correction": col_map["reason_for_correction"],
                        },
                        "amount_mode": amount_mode,
                    }

                    if save_profile_clicked:
                        if profile_name_input.strip():
                            db.save_import_profile(profile_name_input.strip(), current_profile_settings, "actuals_labour")
                            st.success(f"Profile '{profile_name_input.strip()}' saved.")
                            st.rerun()
                        else:
                            st.error("Enter a profile name before saving.")
                    if delete_profile_clicked and selected_profile != "(none)":
                        db.delete_import_profile(selected_profile, "actuals_labour")
                        st.success(f"Profile '{selected_profile}' deleted.")
                        st.rerun()

                    st.info("🗺️ Employee-to-task mapping has moved to the **Mapping** tab.")

                    preview_rows = []
                    for _, row in df_up.iterrows():
                        employee_val = str(row[col_map["employee"]]).strip() if col_map["employee"] != "(none)" else ""
                        project_no_val = str(row[col_map["project_no"]]).strip() if col_map["project_no"] != "(none)" else ""
                        rg_val = str(row[col_map["resource_group"]]).strip() if col_map["resource_group"] != "(none)" else ""
                        booking_year_val = str(row[col_map["booking_year"]]).strip() if col_map["booking_year"] != "(none)" else ""
                        booking_month_val = str(row[col_map["booking_month"]]).strip() if col_map["booking_month"] != "(none)" else ""
                        general_comment_val = str(row[col_map["general_comment"]]).strip() if col_map["general_comment"] != "(none)" else ""
                        reason_corr_val = str(row[col_map["reason_for_correction"]]).strip() if col_map["reason_for_correction"] != "(none)" else ""
                        employee_val = "" if employee_val.lower() == "nan" else employee_val
                        project_no_val = "" if project_no_val.lower() == "nan" else project_no_val
                        rg_val = "" if rg_val.lower() == "nan" else rg_val
                        booking_year_val = "" if booking_year_val.lower() == "nan" else booking_year_val
                        booking_month_val = "" if booking_month_val.lower() == "nan" else booking_month_val
                        general_comment_val = "" if general_comment_val.lower() == "nan" else general_comment_val
                        reason_corr_val = "" if reason_corr_val.lower() == "nan" else reason_corr_val
                        amt = parse_amount(row[col_map["amount"]]) if col_map["amount"] != "(none)" else None
                        if amt is not None:
                            if amount_mode == "Convert to positive (absolute)":
                                amt = abs(amt)
                            elif amount_mode == "Invert sign":
                                amt = -amt

                        # Date is derived from booking year/month where possible.
                        dt = str(datetime.date.today())
                        try:
                            by = int(float(booking_year_val)) if booking_year_val else None
                            bm = int(float(booking_month_val)) if booking_month_val else None
                            if by and bm and 1 <= bm <= 12:
                                dt = f"{by:04d}-{bm:02d}-01"
                        except Exception:
                            dt = str(datetime.date.today())

                        desc = ""
                        mapped_task_name, mapped_task_desc, mapping_status = "", "", "Not mapped"
                        if employee_val:
                            mapping_row = db.resolve_labour_task_mapping(project_no_val, employee_val, rg_val)
                            if mapping_row:
                                mapped_task_name = mapping_row.get("task_name", "") or ""
                                mapped_task_desc = mapping_row.get("task_description", "") or ""
                                mapping_status = "Mapped"

                        desc_parts = []
                        if mapped_task_name and mapped_task_desc:
                            desc_parts.append(f"{mapped_task_name} - {mapped_task_desc}")
                        elif mapped_task_name or mapped_task_desc:
                            desc_parts.append(mapped_task_name or mapped_task_desc)
                        if general_comment_val:
                            desc_parts.append(f"Comment: {general_comment_val}")
                        if reason_corr_val:
                            desc_parts.append(f"Correction: {reason_corr_val}")
                        desc = " | ".join(desc_parts)

                        preview_rows.append({
                            "Date": dt,
                            "Employee": employee_val,
                            "Resource group": rg_val,
                            "Project no.": project_no_val,
                            "Cost (EUR)": amt,
                            "Booking year": booking_year_val,
                            "Booking month": booking_month_val,
                            "General comment": general_comment_val,
                            "Reason for correction": reason_corr_val,
                            "Task Name": mapped_task_name,
                            "Task Description": mapped_task_desc,
                            "Description": desc,
                            "Mapping Status": mapping_status,
                        })

                    preview_df = pd.DataFrame(preview_rows)
                    st.markdown("**4.2.2.2 Import Preview (editable before save):**")
                    edited_preview = st.data_editor(
                        preview_df,
                        use_container_width=True,
                        hide_index=True,
                        num_rows="dynamic",
                        column_config={
                            "Date": st.column_config.TextColumn("Date (YYYY-MM-DD)"),
                            "Employee": st.column_config.TextColumn("Employee"),
                            "Resource group": st.column_config.TextColumn("Resource group"),
                            "Project no.": st.column_config.TextColumn("Project no."),
                            "Cost (EUR)": st.column_config.NumberColumn("Cost (EUR)", step=1.0),
                            "Booking year": st.column_config.TextColumn("Booking year"),
                            "Booking month": st.column_config.TextColumn("Booking month"),
                            "General comment": st.column_config.TextColumn("General comment"),
                            "Reason for correction": st.column_config.TextColumn("Reason for correction"),
                            "Task Name": st.column_config.TextColumn("Task Name"),
                            "Task Description": st.column_config.TextColumn("Task Description"),
                            "Description": st.column_config.TextColumn("Description"),
                            "Mapping Status": st.column_config.TextColumn("Mapping Status"),
                        },
                        key="actuals_import_preview",
                    )

                    c_imp1, c_imp2 = st.columns(2)
                    import_only_positive = c_imp1.checkbox("Import only positive amounts", value=True, key="lab_positive_only")
                    skip_blank_desc = c_imp2.checkbox("Allow blank descriptions", value=True, key="lab_allow_blank_desc")

                    if st.button("Import Labour Preview Rows", key="import_lab_preview"):
                        imported, skipped = 0, 0
                        edited_preview["Cost (EUR)"] = pd.to_numeric(edited_preview["Cost (EUR)"], errors="coerce")

                        # Overwrite behaviour: each new labour file replaces previous labour imports.
                        db.delete_actuals_by_source(project_id, "MCR Import (Labour)")

                        for _, prow in edited_preview.iterrows():
                            amt = prow["Cost (EUR)"]
                            if pd.isna(amt) or (import_only_positive and float(amt) <= 0):
                                skipped += 1
                                continue
                            task_name = "" if pd.isna(prow.get("Task Name", "")) else str(prow.get("Task Name", "")).strip()
                            task_desc = "" if pd.isna(prow.get("Task Description", "")) else str(prow.get("Task Description", "")).strip()
                            base_desc = "" if pd.isna(prow.get("Description", "")) else str(prow.get("Description", "")).strip()
                            final_desc = f"{task_name} - {task_desc}" if task_name and task_desc else (task_name or task_desc or base_desc)
                            if not skip_blank_desc and not final_desc:
                                skipped += 1
                                continue
                            cat = "Labour"
                            dt = parse_date(prow["Date"])
                            employee_name = "" if pd.isna(prow.get("Employee", "")) else str(prow.get("Employee", "")).strip()
                            resource_group = "" if pd.isna(prow.get("Resource group", "")) else str(prow.get("Resource group", "")).strip()
                            project_no = "" if pd.isna(prow.get("Project no.", "")) else str(prow.get("Project no.", "")).strip()
                            db.add_actual(
                                project_id,
                                cat,
                                final_desc,
                                float(amt),
                                dt,
                                "MCR Import (Labour)",
                                task_name=task_name or None,
                                employee_name=employee_name or None,
                                resource_group=resource_group or None,
                                project_no=project_no or None,
                                import_file=uploaded.name if uploaded else None,
                            )
                            imported += 1
                        db.log_import_run(project_id, "labour_actuals", uploaded.name if uploaded else "", imported)
                        st.success(f"Imported {imported} labour rows.")
                        if skipped:
                            st.warning(f"Skipped {skipped} rows due to validation rules.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Labour import error: {e}")

            st.markdown("---")
            with st.expander("4.2.3 Raw actual entries"):
                raw_actuals = db.get_actuals(project_id)
                if not raw_actuals:
                    st.info("No actual entries yet.")
                else:
                    df_raw = pd.DataFrame(raw_actuals)
                    raw_cols = [c for c in ["date", "category", "task_name", "employee_name", "resource_group", "project_no", "wbs_number", "financial_document", "financial_document_posting_date", "description", "amount", "source", "import_file"] if c in df_raw.columns]
                    df_display = df_raw[raw_cols].rename(columns={
                        "date": "Date",
                        "category": "Category",
                        "task_name": "Task Name",
                        "employee_name": "Employee",
                        "resource_group": "Resource Group",
                        "project_no": "Project no.",
                        "wbs_number": "WBS Number",
                        "financial_document": "Financial document",
                        "financial_document_posting_date": "Financial document posting date",
                        "description": "Description",
                        "amount": "Amount (€)",
                        "source": "Source",
                        "import_file": "Import File",
                    })
                    raw_filter_cols = [c for c in ["Category", "Task Name", "Employee", "Resource Group", "Source", "Import File"] if c in df_display.columns]
                    raw_total_cols = [c for c in ["Amount (€)"] if c in df_display.columns]
                    render_table_view(
                        df_display,
                        key_prefix="actuals_raw_entries",
                        filter_columns=raw_filter_cols,
                        total_columns=raw_total_cols,
                        show_totals=True,
                        hide_index=True,
                        currency_columns=raw_total_cols,
                    )

    # ── Show actuals table ──
    actuals = db.get_actuals(project_id)
    if actuals:
        st.markdown("---")
    with tab3:
        st.subheader("🗺️ 4.3.1 Employee to Task Mapping")
        st.caption("Map employees (and optionally project no. / resource group) to task names. Applied automatically during labour import.")
        if st.session_state.get("labour_mapping_save_msg"):
            st.success(st.session_state.pop("labour_mapping_save_msg"))
        st.caption("Use this when labour exports do not include direct task/description. Mapping is applied during import preview.")
        mappings = db.get_labour_task_mappings(active_only=False)
        if mappings:
            map_df = pd.DataFrame(mappings)[["id", "project_no", "employee_name", "resource_group", "task_name", "task_description", "is_active", "updated_at"]].rename(columns={"id": "ID", "project_no": "Project no.", "employee_name": "Employee Name", "resource_group": "Resource Group", "task_name": "Task Name", "task_description": "Task Description", "is_active": "Active", "updated_at": "Updated"})

            _map_filter_df = map_df.copy()
            _map_filter_df["Resource Group"] = _map_filter_df["Resource Group"].fillna("").astype(str).str.strip()
            _map_filter_df["Task Name"] = _map_filter_df["Task Name"].fillna("").astype(str).str.strip()
            _map_filter_df["Resource Group (filter)"] = _map_filter_df["Resource Group"].replace("", "(blank)")
            _map_filter_df["Task Name (filter)"] = _map_filter_df["Task Name"].replace("", "(blank)")

            _all_rg = sorted(_map_filter_df["Resource Group (filter)"].unique().tolist())
            _all_task_names = sorted(_map_filter_df["Task Name (filter)"].unique().tolist())
            _f1, _f2 = st.columns(2)
            _sel_rg = _f1.multiselect(
                "Filter by Resource group",
                _all_rg,
                default=_all_rg,
                key="labour_mapping_filter_resource_group",
            )
            _sel_task_names = _f2.multiselect(
                "Filter by Task name",
                _all_task_names,
                default=_all_task_names,
                key="labour_mapping_filter_task_name",
            )

            if _sel_rg:
                _map_filter_df = _map_filter_df[_map_filter_df["Resource Group (filter)"].isin(_sel_rg)].copy()
            else:
                _map_filter_df = _map_filter_df.iloc[0:0].copy()

            if _sel_task_names:
                _map_filter_df = _map_filter_df[_map_filter_df["Task Name (filter)"].isin(_sel_task_names)].copy()
            else:
                _map_filter_df = _map_filter_df.iloc[0:0].copy()

            map_editor_df = _map_filter_df[["ID", "Project no.", "Employee Name", "Resource Group", "Task Name", "Task Description", "Active", "Updated"]].copy()
            map_editor_df["Delete"] = False
            st.caption("Edit cells directly. Tick Delete for rows to remove, then click Apply Table Changes.")
            edited_map_df = st.data_editor(
                map_editor_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=["ID", "Updated"],
                column_config={
                    "ID": st.column_config.NumberColumn("ID"),
                    "Project no.": st.column_config.TextColumn("Project no."),
                    "Employee Name": st.column_config.TextColumn("Employee Name"),
                    "Resource Group": st.column_config.TextColumn("Resource Group"),
                    "Task Name": st.column_config.TextColumn("Task Name"),
                    "Task Description": st.column_config.TextColumn("Task Description"),
                    "Active": st.column_config.CheckboxColumn("Active"),
                    "Updated": st.column_config.TextColumn("Updated"),
                    "Delete": st.column_config.CheckboxColumn("Delete"),
                },
                key="labour_mapping_editor",
            )

            if st.button("💾 Apply Table Changes", key="apply_mapping_table_changes"):
                try:
                    original_by_id = {int(r["ID"]): r for _, r in map_df.iterrows()}
                    updated_count, deleted_count, backfilled_count, skipped_count = 0, 0, 0, 0

                    for _, row in edited_map_df.iterrows():
                        mapping_id = int(row["ID"])
                        if bool(row.get("Delete", False)):
                            db.delete_labour_task_mapping(mapping_id)
                            deleted_count += 1
                            continue

                        employee_name = "" if pd.isna(row.get("Employee Name", "")) else str(row.get("Employee Name", "")).strip()
                        task_name = "" if pd.isna(row.get("Task Name", "")) else str(row.get("Task Name", "")).strip()
                        if not employee_name or not task_name:
                            skipped_count += 1
                            continue

                        project_no = "" if pd.isna(row.get("Project no.", "")) else str(row.get("Project no.", "")).strip()
                        resource_group = "" if pd.isna(row.get("Resource Group", "")) else str(row.get("Resource Group", "")).strip()
                        task_desc = "" if pd.isna(row.get("Task Description", "")) else str(row.get("Task Description", "")).strip()
                        is_active = bool(row.get("Active", False))

                        orig = original_by_id.get(mapping_id)
                        changed = (
                            str(orig.get("Project no.", "") or "").strip() != project_no
                            or str(orig.get("Employee Name", "") or "").strip() != employee_name
                            or str(orig.get("Resource Group", "") or "").strip() != resource_group
                            or str(orig.get("Task Name", "") or "").strip() != task_name
                            or str(orig.get("Task Description", "") or "").strip() != task_desc
                            or bool(orig.get("Active", False)) != is_active
                        )
                        if not changed:
                            continue

                        old_project_no = str(orig.get("Project no.", "") or "").strip()
                        old_employee_name = str(orig.get("Employee Name", "") or "").strip()
                        old_resource_group = str(orig.get("Resource Group", "") or "").strip()
                        old_task_name = str(orig.get("Task Name", "") or "").strip()

                        db.update_labour_task_mapping(mapping_id, project_no, employee_name, resource_group, task_name, task_desc, is_active=is_active)
                        # 1) Remap rows that used the old mapping task (task rename/edit case).
                        backfilled_count += db.apply_labour_mapping_to_actuals(
                            old_project_no,
                            old_employee_name,
                            old_resource_group,
                            task_name,
                            previous_task_name=old_task_name,
                        )
                        # 2) Also apply to currently unmapped rows on the new key.
                        backfilled_count += db.apply_labour_mapping_to_actuals(
                            project_no,
                            employee_name,
                            resource_group,
                            task_name,
                        )
                        updated_count += 1

                    if updated_count or deleted_count:
                        st.success(f"Applied changes. Updated: {updated_count}, Deleted: {deleted_count}")
                    else:
                        st.info("No table changes detected.")
                    if backfilled_count:
                        st.info(f"Applied mappings to {backfilled_count} existing labour actual row(s).")
                    if skipped_count:
                        st.warning(f"Skipped {skipped_count} row(s) with missing Employee Name or Task Name.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to apply table changes: {e}")
        else:
            st.info("No labour mappings yet.")
        if st.session_state.get("labour_mapping_save_msg"):
            st.success(st.session_state.pop("labour_mapping_save_msg"))

        st.markdown("**4.3.2 Import mapping table (first pass):**")
        mapping_upload = st.file_uploader("Upload labour mapping file", type=["xlsx", "xls", "csv"], key="labour_mapping_upload", help="Expected fields: Project no., Employee, Resource group, Task name.")
        if mapping_upload:
            try:
                map_src_df = pd.read_csv(mapping_upload) if mapping_upload.name.lower().endswith(".csv") else pd.read_excel(mapping_upload)
                st.write("Mapping file preview:", map_src_df.head())
                map_cols = ["(none)"] + list(map_src_df.columns)
                d_project = guess_column(map_src_df.columns, ["project no", "project_no", "project", "project number", "wbs"])
                d_emp = guess_column(map_src_df.columns, ["employee", "employee name", "name", "resource"])
                d_rg = guess_column(map_src_df.columns, ["resource group", "resource_group", "rg", "team"])
                d_task = guess_column(map_src_df.columns, ["task name", "task", "work package"])
                d_desc = guess_column(map_src_df.columns, ["task description", "description", "comment", "note"])
                im1, im2, im3, im4, im5 = st.columns(5)
                map_project_col = im1.selectbox("Project no. column", map_cols, index=map_cols.index(d_project) if d_project in map_cols else 0, key="map_project_col")
                map_emp_col = im2.selectbox("Employee Name column", map_cols, index=map_cols.index(d_emp) if d_emp in map_cols else 0, key="map_emp_col")
                map_rg_col = im3.selectbox("Resource Group column", map_cols, index=map_cols.index(d_rg) if d_rg in map_cols else 0, key="map_rg_col")
                map_task_col = im4.selectbox("Task Name column", map_cols, index=map_cols.index(d_task) if d_task in map_cols else 0, key="map_task_col")
                map_desc_col = im5.selectbox("Task Description column", map_cols, index=map_cols.index(d_desc) if d_desc in map_cols else 0, key="map_desc_col")
                import_active = st.checkbox("Imported mappings are active", value=True, key="map_import_active")
                if st.button("⬇️ Import Mapping Table", key="import_mapping_btn"):
                    imported_map, skipped_map, backfilled_total = 0, 0, 0
                    for _, mr in map_src_df.iterrows():
                        project_no = "" if map_project_col == "(none)" else str(mr[map_project_col]).strip()
                        employee_name = "" if map_emp_col == "(none)" else str(mr[map_emp_col]).strip()
                        resource_group = "" if map_rg_col == "(none)" else str(mr[map_rg_col]).strip()
                        task_name = "" if map_task_col == "(none)" else str(mr[map_task_col]).strip()
                        task_desc = "" if map_desc_col == "(none)" else str(mr[map_desc_col]).strip()
                        project_no = "" if project_no.lower() == "nan" else project_no
                        employee_name = "" if employee_name.lower() == "nan" else employee_name
                        resource_group = "" if resource_group.lower() == "nan" else resource_group
                        task_name = "" if task_name.lower() == "nan" else task_name
                        task_desc = "" if task_desc.lower() == "nan" else task_desc
                        if not employee_name or not task_name:
                            skipped_map += 1
                            continue
                        db.upsert_labour_task_mapping(project_no, employee_name, resource_group, task_name, task_desc, 0, 0, 0, import_active)
                        backfilled_total += db.apply_labour_mapping_to_actuals(project_no, employee_name, resource_group, task_name)
                        imported_map += 1
                    st.success(f"Imported/updated {imported_map} mapping rows.")
                    if backfilled_total:
                        st.info(f"Applied mappings to {backfilled_total} existing unmapped labour actual row(s).")
                    if skipped_map:
                        st.warning(f"Skipped {skipped_map} rows (missing Employee Name or Task Name).")
                    st.rerun()
            except Exception as map_ex:
                st.error(f"Error reading mapping file: {map_ex}")

        st.markdown("**4.3.3 Manual labour mapping**")
        with st.form("labour_mapping_form"):
            lm1, lm2, lm3 = st.columns(3)
            map_project_no = lm1.text_input("Project no. (optional)")
            map_employee = lm2.text_input("Employee Name *")
            map_rg = lm3.text_input("Resource Group (optional)")
            lm4, lm5 = st.columns(2)
            map_task = lm4.text_input("Task Name *")
            map_task_desc = lm5.text_input("Task Description")
            map_active = st.checkbox("Active", value=True)
            if st.form_submit_button("💾 Save Labour Mapping"):
                if not map_employee.strip() or not map_task.strip():
                    st.error("Employee Name and Task Name are required.")
                else:
                    try:
                        _result = db.upsert_labour_task_mapping(
                            map_project_no.strip(),
                            map_employee.strip(),
                            map_rg.strip(),
                            map_task.strip(),
                            map_task_desc.strip(),
                            is_active=map_active,
                        )
                        _backfilled = db.apply_labour_mapping_to_actuals(map_project_no.strip(), map_employee.strip(), map_rg.strip(), map_task.strip())
                        _verb = "Added" if _result == "inserted" else "Updated"
                        st.session_state["labour_mapping_save_msg"] = f"✅ {_verb}: {map_employee.strip()} → {map_task.strip()} | Updated {_backfilled} existing actual row(s)"
                        st.session_state["labour_mapping_expander_open"] = True
                        st.rerun()
                    except Exception as _e:
                        st.error(f"Failed to save mapping: {_e}")

        st.markdown("---")
        st.subheader("⚠️ 4.3.4 Unmapped Labour Actuals")
        st.caption("These labour actual entries are missing a task assignment. Use mapping above to assign them to tasks.")
        
        # Get all labour actuals and filter for unmapped ones
        all_actuals = db.get_actuals(project_id)
        if all_actuals:
            df_all = pd.DataFrame(all_actuals)
            df_labour = df_all[df_all["category"].fillna("").str.lower() == "labour"].copy()
            
            # Filter for unmapped: task_name is null, empty, or contains default text
            if not df_labour.empty:
                df_unmapped = df_labour[
                    (df_labour["task_name"].isna()) | 
                    (df_labour["task_name"].astype(str).str.strip() == "") |
                    (df_labour["task_name"].astype(str).str.strip() == "[Unmapped Task]")
                ].copy()
                
                if not df_unmapped.empty:
                    df_unmapped["date"] = pd.to_datetime(df_unmapped["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
                    unmapped_cols = [c for c in ["date", "employee_name", "resource_group", "project_no", "description", "amount", "source"] if c in df_unmapped.columns]
                    df_display_unmapped = df_unmapped[unmapped_cols].rename(columns={
                        "date": "Date",
                        "employee_name": "Employee",
                        "resource_group": "Resource Group",
                        "project_no": "Project no.",
                        "description": "Description",
                        "amount": "Amount (€)",
                        "source": "Source",
                    })
                    
                    # Display with summary
                    unmapped_count = len(df_unmapped)
                    unmapped_total = df_unmapped["amount"].astype(float).sum()
                    st.metric(f"Unmapped Labour Actuals", f"{unmapped_count} entries | € {unmapped_total:,.2f}")
                    st.dataframe(
                        df_display_unmapped.sort_values("Date", ascending=False),
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Date": st.column_config.TextColumn("Date"),
                            "Employee": st.column_config.TextColumn("Employee"),
                            "Resource Group": st.column_config.TextColumn("Resource Group"),
                            "Project no.": st.column_config.TextColumn("Project no."),
                            "Description": st.column_config.TextColumn("Description"),
                            "Amount (€)": st.column_config.NumberColumn("Amount (€)", format="€ %,.2f"),
                            "Source": st.column_config.TextColumn("Source"),
                        }
                    )

                    st.markdown("**4.3.4.1 Create mapping rule from unmapped actual**")
                    _df_unmapped_sel = df_unmapped.copy()
                    for _c in ["employee_name", "resource_group", "project_no", "description"]:
                        if _c in _df_unmapped_sel.columns:
                            _df_unmapped_sel[_c] = _df_unmapped_sel[_c].fillna("").astype(str)
                            _df_unmapped_sel.loc[_df_unmapped_sel[_c].str.lower() == "nan", _c] = ""
                    _df_unmapped_sel["amount"] = pd.to_numeric(_df_unmapped_sel.get("amount", 0), errors="coerce").fillna(0.0)
                    _df_unmapped_sel = _df_unmapped_sel.sort_values("date", ascending=False).reset_index(drop=True)

                    _row_options = []
                    for _idx, _r in _df_unmapped_sel.iterrows():
                        _row_options.append(
                            (
                                f"[{_idx+1}] {_r.get('date','')} | {_r.get('employee_name','(no employee)')} | "
                                f"{_r.get('project_no','-')} | {_r.get('resource_group','-')} | € {float(_r.get('amount', 0.0)):,.2f}",
                                _idx,
                            )
                        )

                    _sel_lbl = st.selectbox(
                        "Select unmapped actual row",
                        [x[0] for x in _row_options],
                        key="unmapped_map_row_select",
                    )
                    _sel_idx = next((x[1] for x in _row_options if x[0] == _sel_lbl), 0)
                    _sel_row = _df_unmapped_sel.iloc[_sel_idx]

                    _src_employee = str(_sel_row.get("employee_name", "") or "").strip()
                    _src_rg = str(_sel_row.get("resource_group", "") or "").strip()
                    _src_project_no = str(_sel_row.get("project_no", "") or "").strip()
                    _src_desc = str(_sel_row.get("description", "") or "").strip()

                    st.caption("Selected row preview (auto-updated):")
                    _mf1, _mf2, _mf3 = st.columns(3)
                    _mf1.markdown(f"**Employee:** {_src_employee or '-'}")
                    _mf2.markdown(f"**Project no.:** {_src_project_no or '-'}")
                    _mf3.markdown(f"**Resource group:** {_src_rg or '-'}")
                    if _src_desc:
                        st.caption(f"Source description: {_src_desc}")

                    _task1, _task2, _task3 = st.columns([2, 2, 1])
                    _new_task_name = _task1.text_input("Task Name *", key="unmapped_new_task_name")
                    _new_task_desc = _task2.text_input("Task Description", key="unmapped_new_task_desc")
                    _new_task_active = _task3.checkbox("Active", value=True, key="unmapped_new_task_active")

                    if st.button("➕ Create Mapping Rule", key="create_unmapped_mapping_btn"):
                        if not _src_employee:
                            st.error("Selected row has no Employee. Cannot create mapping rule.")
                        elif not _new_task_name.strip():
                            st.error("Task Name is required to create the mapping rule.")
                        else:
                            try:
                                _res = db.upsert_labour_task_mapping(
                                    _src_project_no,
                                    _src_employee,
                                    _src_rg,
                                    _new_task_name.strip(),
                                    _new_task_desc.strip(),
                                    is_active=_new_task_active,
                                )
                                _bf = db.apply_labour_mapping_to_actuals(
                                    _src_project_no,
                                    _src_employee,
                                    _src_rg,
                                    _new_task_name.strip(),
                                )
                                _verb = "Created" if _res == "inserted" else "Updated"
                                st.success(f"✅ {_verb} mapping rule and updated {_bf} existing labour actual row(s).")
                                st.rerun()
                            except Exception as _map_ex:
                                st.error(f"Failed to create mapping rule: {_map_ex}")
                else:
                    st.success("✓ All labour actuals are mapped to tasks!")
            else:
                st.info("No labour actuals in this project.")
        else:
            st.info("No actuals in this project.")

    with tab4:
        st.subheader("4.4.1 Actuals summary")
        df_a = pd.DataFrame(actuals)
        if df_a.empty:
            st.info("No actuals in this project.")
        else:
            df_a["amount"] = pd.to_numeric(df_a["amount"], errors="coerce").fillna(0.0)
            st.markdown(f"**Total Actuals: € {df_a['amount'].sum():,.2f}**")

            _df_summary = df_a.copy()
            _df_summary["Cost Type"] = _df_summary["category"].fillna("").astype(str).str.strip().str.lower().apply(
                lambda x: "Labor actuals" if x == "labour" else "Other actuals"
            )

            _sum_by_type = _df_summary.groupby("Cost Type", as_index=False)["amount"].sum().rename(columns={"amount": "Amount (€)"})
            _pie = px.pie(
                _sum_by_type,
                names="Cost Type",
                values="Amount (€)",
                title="Total Actuals Split",
                color="Cost Type",
                color_discrete_map={"Labor actuals": "#4C78A8", "Other actuals": "#F58518"},
            )
            _pie.update_traces(textposition="inside", texttemplate="%{label}<br>€ %{value:,.0f} (%{percent})")
            _pie.update_layout(height=300, margin=dict(l=10, r=10, t=45, b=10), legend_title_text="")

            _df_summary["Month"] = pd.to_datetime(_df_summary.get("date"), errors="coerce").dt.strftime("%Y-%m")
            _df_summary = _df_summary[_df_summary["Month"].notna()].copy()
            _month_stack = pd.DataFrame()
            _scol1, _scol2 = st.columns(2)
            with _scol1:
                st.plotly_chart(_pie, use_container_width=True)
            with _scol2:
                if _df_summary.empty:
                    st.info("No dated actuals available for monthly chart.")
                else:
                    _df_summary["MonthDate"] = pd.to_datetime(_df_summary["Month"] + "-01", errors="coerce")
                    _month_stack = (
                        _df_summary.groupby(["MonthDate", "Cost Type"], as_index=False)["amount"]
                        .sum()
                        .rename(columns={"amount": "Amount (€)"})
                    )
                    _month_stack = _month_stack[_month_stack["MonthDate"].notna()].copy()
                    _month_stack = _month_stack.sort_values("MonthDate")
                    _month_stack["Month"] = _month_stack["MonthDate"].dt.strftime("%b-%y")
                    _stack = px.bar(
                        _month_stack,
                        x="Month",
                        y="Amount (€)",
                        color="Cost Type",
                        barmode="stack",
                        title="Actuals by Month (Labor vs Other)",
                        color_discrete_map={"Labor actuals": "#4C78A8", "Other actuals": "#F58518"},
                    )
                    _stack.update_layout(
                        xaxis_title="Month",
                        yaxis_title="Amount (€)",
                        height=300,
                        margin=dict(l=10, r=10, t=45, b=10),
                        legend_title_text="",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
                    )
                    st.plotly_chart(_stack, use_container_width=True)

            if not _month_stack.empty:
                _cum = _month_stack.pivot_table(
                    index="MonthDate",
                    columns="Cost Type",
                    values="Amount (€)",
                    aggfunc="sum",
                    fill_value=0.0,
                ).sort_index()
                for _ct in ["Labor actuals", "Other actuals"]:
                    if _ct not in _cum.columns:
                        _cum[_ct] = 0.0
                _cum = _cum[["Labor actuals", "Other actuals"]].cumsum().reset_index()
                _cum["Month"] = _cum["MonthDate"].dt.strftime("%b-%y")
                _cum_long = _cum.melt(
                    id_vars=["MonthDate", "Month"],
                    value_vars=["Labor actuals", "Other actuals"],
                    var_name="Cost Type",
                    value_name="Cumulative (€)",
                )
                _cum_chart = px.bar(
                    _cum_long,
                    x="Month",
                    y="Cumulative (€)",
                    color="Cost Type",
                    title="Cumulative Actuals by Month (Labor vs Other)",
                    color_discrete_map={"Labor actuals": "#4C78A8", "Other actuals": "#F58518"},
                )
                _cum_chart.update_layout(
                    xaxis_title="Month",
                    yaxis_title="Cumulative Amount (€)",
                    barmode="stack",
                    height=280,
                    margin=dict(l=10, r=10, t=45, b=10),
                    legend_title_text="",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
                )
                st.plotly_chart(_cum_chart, use_container_width=True)

            st.markdown("---")

            st.markdown("**4.4.2 Other Actuals by Task and Month (€):**")
            _df_other = df_a[df_a["category"].fillna("").str.lower() != "labour"].copy()
            if _df_other.empty:
                st.info("No non-labour actuals available.")
            elif "task_name" not in _df_other.columns:
                st.info("Task metadata is not available for Other actuals.")
            else:
                _df_other["Category"] = _df_other.get("category", "").fillna("").astype(str).str.strip()
                _df_other.loc[_df_other["Category"].eq(""), "Category"] = "(blank)"
                _all_categories = sorted(_df_other["Category"].astype(str).unique().tolist())
                _sel_categories = st.multiselect(
                    "Filter Other Category",
                    _all_categories,
                    default=_all_categories,
                    key="actuals_other_task_category_filter",
                )
                if _sel_categories:
                    _df_other = _df_other[_df_other["Category"].astype(str).isin(_sel_categories)].copy()
                else:
                    _df_other = _df_other.iloc[0:0].copy()

                if not _df_other.empty:
                    _df_other["Task"] = _df_other["task_name"].fillna("").replace("", "[Unmapped Task]")
                    _df_other["Month"] = pd.to_datetime(_df_other["date"], errors="coerce").dt.strftime("%Y-%m")
                    _df_other["Month"] = _df_other["Month"].fillna("Unknown")

                    _other_task_month = (
                        _df_other.groupby(["Task", "Month"], as_index=False)["amount"]
                        .sum()
                        .rename(columns={"amount": "Amount (€)"})
                    )
                    _other_task_pivot = _other_task_month.pivot_table(
                        index="Task",
                        columns="Month",
                        values="Amount (€)",
                        aggfunc="sum",
                        fill_value=0,
                    )
                    if not _other_task_pivot.empty:
                        _other_sorted_cols = sorted(_other_task_pivot.columns)
                        _other_task_pivot = _other_task_pivot[_other_sorted_cols]
                        _other_task_pivot["Total (€)"] = _other_task_pivot.sum(axis=1)
                        _other_task_table = _other_task_pivot.reset_index().rename(columns={"Task": "Task"})
                        if "actuals_other_task_drill_open" not in st.session_state:
                            st.session_state["actuals_other_task_drill_open"] = {}
                        _other_open_state = st.session_state["actuals_other_task_drill_open"]

                        _other_task_table = _other_task_table.sort_values("Total (€)", ascending=False).reset_index(drop=True)

                        _other_proj_col = "project_no" if "project_no" in _df_other.columns else None
                        _other_rg_col = "resource_group" if "resource_group" in _df_other.columns else None

                        _other_task_project_map = {}
                        _other_task_rg_map = {}
                        for _t, _grp in _df_other.groupby("Task"):
                            if _other_proj_col:
                                _other_task_project_map[_t] = set(_grp[_other_proj_col].dropna().astype(str).unique())
                            if _other_rg_col:
                                _other_task_rg_map[_t] = set(_grp[_other_rg_col].dropna().astype(str).unique())

                        _other_filter_tasks = sorted(_other_task_table["Task"].astype(str).unique().tolist())
                        _other_selected_tasks = st.multiselect(
                            "Filter Other Task",
                            _other_filter_tasks,
                            default=_other_filter_tasks,
                            key="actuals_other_task_month_filter_table",
                        )

                        _other_all_projects = sorted({p for ps in _other_task_project_map.values() for p in ps}) if _other_task_project_map else []
                        _other_fcols = st.columns(2)
                        if _other_all_projects:
                            _other_sel_projects = _other_fcols[0].multiselect(
                                "Filter Other Project no.",
                                _other_all_projects,
                                default=_other_all_projects,
                                key="actuals_other_task_proj_filter",
                            )
                        else:
                            _other_sel_projects = []

                        _other_all_rgs = sorted({r for rs in _other_task_rg_map.values() for r in rs}) if _other_task_rg_map else []
                        if _other_all_rgs:
                            _other_sel_rgs = _other_fcols[1].multiselect(
                                "Filter Other Resource group",
                                _other_all_rgs,
                                default=_other_all_rgs,
                                key="actuals_other_task_rg_filter",
                            )
                        else:
                            _other_sel_rgs = []

                        def _other_task_matches(t):
                            if t not in _other_selected_tasks:
                                return False
                            if _other_sel_projects and not _other_task_project_map.get(t, set()).intersection(_other_sel_projects):
                                return False
                            if _other_sel_rgs and not _other_task_rg_map.get(t, set()).intersection(_other_sel_rgs):
                                return False
                            return True

                        _other_filtered_tasks = _other_task_table[_other_task_table["Task"].map(_other_task_matches)].copy()
                        _other_all_month_cols = [c for c in _other_task_table.columns if c not in ["Task", "Total (€)"]]
                        _other_ym_cols = [c for c in _other_all_month_cols if re.match(r"^\d{4}-\d{2}$", str(c))]
                        _other_other_time_cols = [c for c in _other_all_month_cols if c not in _other_ym_cols]
                        _other_years = sorted({str(c)[:4] for c in _other_ym_cols})

                        _other_tf1, _other_tf2 = st.columns(2)
                        _other_sel_years = _other_tf1.multiselect(
                            "Filter Other Year",
                            _other_years,
                            default=_other_years,
                            key="actuals_other_task_year_filter",
                        )
                        _other_month_options = [m for m in _other_ym_cols if str(m)[:4] in _other_sel_years] + _other_other_time_cols
                        _other_sel_months = _other_tf2.multiselect(
                            "Filter Other Month",
                            _other_month_options,
                            default=_other_month_options,
                            key="actuals_other_task_month_column_filter",
                        )
                        _other_month_cols = _other_sel_months.copy()

                        _other_combined_rows = []
                        for _, _trow in _other_filtered_tasks.iterrows():
                            _task_name = _trow["Task"]
                            _task_open = bool(_other_open_state.get(_task_name, False))
                            _arrow = "\u25bc " if _task_open else "\u25ba "
                            _task_projs = _other_task_project_map.get(_task_name, set())
                            _task_rgs = _other_task_rg_map.get(_task_name, set())
                            _task_row = {
                                "Task": f"{_arrow}{_task_name}",
                                "Project no.": ", ".join(sorted(_task_projs)) if _task_projs else "",
                                "Resource group": "Various" if len(_task_rgs) > 1 else (next(iter(_task_rgs)) if _task_rgs else ""),
                                "Toggle": _task_open,
                                "_row_type": "task",
                                "_task": _task_name,
                            }
                            for _c in _other_month_cols:
                                _task_row[_c] = float(_trow[_c])
                            _task_row["Total (€)"] = sum(float(_trow[_c]) for _c in _other_month_cols) if _other_month_cols else 0.0
                            _other_combined_rows.append(_task_row)

                            if _task_open:
                                _df_task = _df_other[_df_other["Task"] == _task_name].copy()
                                _df_task["Month"] = pd.to_datetime(_df_task["date"], errors="coerce").dt.strftime("%Y-%m").fillna("Unknown")
                                _cat_month = (
                                    _df_task.groupby(["Category", "Month"], as_index=False)["amount"]
                                    .sum()
                                    .rename(columns={"amount": "Amount (€)"})
                                )
                                _cat_pivot = _cat_month.pivot_table(
                                    index="Category",
                                    columns="Month",
                                    values="Amount (€)",
                                    aggfunc="sum",
                                    fill_value=0,
                                )
                                if not _cat_pivot.empty:
                                    for _mc in _other_month_cols:
                                        if _mc not in _cat_pivot.columns:
                                            _cat_pivot[_mc] = 0.0
                                    _cat_pivot = _cat_pivot[_other_month_cols]
                                    _cat_pivot["Total (€)"] = _cat_pivot.sum(axis=1)
                                    for _cat_name, _crow in _cat_pivot.iterrows():
                                        _cat_rows = _df_task[_df_task["Category"] == _cat_name]
                                        _cat_proj = ", ".join(sorted(_cat_rows[_other_proj_col].dropna().astype(str).unique())) if _other_proj_col else ""
                                        _cat_rg = ", ".join(sorted(_cat_rows[_other_rg_col].dropna().astype(str).unique())) if _other_rg_col else ""
                                        _child_row = {
                                            "Task": f"\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u21b3 {_cat_name}",
                                            "Project no.": _cat_proj,
                                            "Resource group": _cat_rg,
                                            "Toggle": False,
                                            "_row_type": "category",
                                            "_task": _task_name,
                                        }
                                        for _c in _other_month_cols:
                                            _child_row[_c] = float(_crow[_c])
                                        _child_row["Total (€)"] = sum(float(_crow[_c]) for _c in _other_month_cols) if _other_month_cols else 0.0
                                        _other_combined_rows.append(_child_row)

                        _other_combined_df = pd.DataFrame(_other_combined_rows) if _other_combined_rows else pd.DataFrame(columns=["Task", "Project no.", "Resource group", "Toggle"] + _other_month_cols + ["Total (€)", "_row_type", "_task"])

                        _other_total_row = {"Task": "TOTAL", "Project no.": "", "Resource group": "", "Toggle": False, "_row_type": "total", "_task": ""}
                        for _c in _other_month_cols:
                            _other_total_row[_c] = pd.to_numeric(_other_filtered_tasks[_c], errors="coerce").fillna(0).sum() if _c in _other_filtered_tasks.columns else 0.0
                        _other_total_row["Total (€)"] = sum(float(_other_total_row[_c]) for _c in _other_month_cols) if _other_month_cols else 0.0
                        _other_combined_df = pd.concat([_other_combined_df, pd.DataFrame([_other_total_row])], ignore_index=True)

                        _other_display_df = _other_combined_df[["Task", "Project no.", "Resource group"] + _other_month_cols + ["Total (€)"]].copy()
                        import hashlib as _hl
                        _other_open_sig = "_".join(f"{k}={v}" for k, v in sorted(_other_open_state.items()))
                        _other_table_key = "aotm_" + _hl.md5(_other_open_sig.encode()).hexdigest()[:8]

                        st.caption("Click a task row to expand / collapse its category breakdown.")
                        _other_sel_result = st.dataframe(
                            _other_display_df,
                            hide_index=True,
                            use_container_width=True,
                            on_select="rerun",
                            selection_mode="single-row",
                            column_config={
                                "Task": st.column_config.TextColumn("Task"),
                                "Project no.": st.column_config.TextColumn("Project no."),
                                "Resource group": st.column_config.TextColumn("Resource group"),
                                **{c: st.column_config.NumberColumn(c, format="€ %,.2f") for c in _other_month_cols + ["Total (€)"]},
                            },
                            key=_other_table_key,
                        )
                        _other_sel_rows = _other_sel_result.selection.get("rows", []) if _other_sel_result and hasattr(_other_sel_result, "selection") else []
                        if _other_sel_rows:
                            _other_clicked_idx = _other_sel_rows[0]
                            if _other_clicked_idx < len(_other_combined_df):
                                _other_clicked_type = _other_combined_df.iloc[_other_clicked_idx]["_row_type"]
                                _other_clicked_task = _other_combined_df.iloc[_other_clicked_idx]["_task"]
                                if _other_clicked_type == "task" and _other_clicked_task:
                                    _other_open_state[_other_clicked_task] = not _other_open_state.get(_other_clicked_task, False)
                                    st.rerun()
                    else:
                        st.info("No Other task rows available after filters.")
                else:
                    st.info("No non-labour actuals in this project.")

            st.markdown("---")

            if "task_name" in df_a.columns:
                df_lab = df_a[df_a["category"].fillna("").str.lower() == "labour"].copy()
                if not df_lab.empty:
                    df_lab["Employee"] = df_lab.get("employee_name", "").fillna("").replace("", "(unknown employee)")
                    _all_employees = sorted(df_lab["Employee"].astype(str).unique().tolist())
                    _sel_employees = st.multiselect(
                        "Filter Employee",
                        _all_employees,
                        default=_all_employees,
                        key="actuals_task_employee_filter",
                    )
                    if _sel_employees:
                        df_lab = df_lab[df_lab["Employee"].astype(str).isin(_sel_employees)].copy()
                    else:
                        df_lab = df_lab.iloc[0:0].copy()

                if not df_lab.empty:
                    df_lab["Task"] = df_lab["task_name"].fillna("").replace("", "[Unmapped Task]")
                    df_lab["Month"] = pd.to_datetime(df_lab["date"], errors="coerce").dt.strftime("%Y-%m")
                    df_lab["Month"] = df_lab["Month"].fillna("Unknown")

                    task_month = (
                        df_lab.groupby(["Task", "Month"], as_index=False)["amount"]
                        .sum()
                        .rename(columns={"amount": "Amount (€)"})
                    )
                    task_pivot = task_month.pivot_table(
                        index="Task",
                        columns="Month",
                        values="Amount (€)",
                        aggfunc="sum",
                        fill_value=0,
                    )
                    if not task_pivot.empty:
                        sorted_cols = sorted(task_pivot.columns)
                        task_pivot = task_pivot[sorted_cols]
                        task_pivot["Total (€)"] = task_pivot.sum(axis=1)
                        st.markdown("**4.4.3 Labour Actuals by Task and Month (€):**")
                        task_table = task_pivot.reset_index().rename(columns={"Task": "Task"})
                        if "actuals_task_drill_open" not in st.session_state:
                            st.session_state["actuals_task_drill_open"] = {}
                        open_state = st.session_state["actuals_task_drill_open"]

                        task_table = task_table.sort_values("Total (€)", ascending=False).reset_index(drop=True)

                        _proj_col = "project_no" if "project_no" in df_lab.columns else None
                        _rg_col = "resource_group" if "resource_group" in df_lab.columns else None

                        task_project_map = {}
                        task_rg_map = {}
                        for _t, _grp in df_lab.groupby("Task"):
                            if _proj_col:
                                task_project_map[_t] = set(_grp[_proj_col].dropna().astype(str).unique())
                            if _rg_col:
                                task_rg_map[_t] = set(_grp[_rg_col].dropna().astype(str).unique())

                        filter_tasks = sorted(task_table["Task"].astype(str).unique().tolist())
                        selected_tasks = st.multiselect(
                            "Filter Task",
                            filter_tasks,
                            default=filter_tasks,
                            key="actuals_task_month_filter_table",
                        )

                        all_projects = sorted({p for ps in task_project_map.values() for p in ps}) if task_project_map else []
                        _fcols = st.columns(2)
                        if all_projects:
                            sel_projects = _fcols[0].multiselect(
                                "Filter Project no.",
                                all_projects,
                                default=all_projects,
                                key="actuals_task_proj_filter",
                            )
                        else:
                            sel_projects = []

                        all_rgs = sorted({r for rs in task_rg_map.values() for r in rs}) if task_rg_map else []
                        if all_rgs:
                            sel_rgs = _fcols[1].multiselect(
                                "Filter Resource group",
                                all_rgs,
                                default=all_rgs,
                                key="actuals_task_rg_filter",
                            )
                        else:
                            sel_rgs = []

                        def _task_matches(t):
                            if t not in selected_tasks:
                                return False
                            if sel_projects and not task_project_map.get(t, set()).intersection(sel_projects):
                                return False
                            if sel_rgs and not task_rg_map.get(t, set()).intersection(sel_rgs):
                                return False
                            return True

                        filtered_tasks = task_table[task_table["Task"].map(_task_matches)].copy()
                        _all_month_cols = [c for c in task_table.columns if c not in ["Task", "Total (€)"]]
                        _ym_cols = [c for c in _all_month_cols if re.match(r"^\d{4}-\d{2}$", str(c))]
                        _other_time_cols = [c for c in _all_month_cols if c not in _ym_cols]
                        _years = sorted({str(c)[:4] for c in _ym_cols})

                        _tf1, _tf2 = st.columns(2)
                        sel_years = _tf1.multiselect(
                            "Filter Year",
                            _years,
                            default=_years,
                            key="actuals_task_year_filter",
                        )
                        _month_options = [m for m in _ym_cols if str(m)[:4] in sel_years] + _other_time_cols
                        sel_months = _tf2.multiselect(
                            "Filter Month",
                            _month_options,
                            default=_month_options,
                            key="actuals_task_month_column_filter",
                        )
                        month_cols = sel_months.copy()

                        combined_rows = []
                        for _, trow in filtered_tasks.iterrows():
                            task_name = trow["Task"]
                            task_open = bool(open_state.get(task_name, False))
                            arrow = "\u25bc " if task_open else "\u25ba "
                            _task_projs = task_project_map.get(task_name, set())
                            _task_rgs = task_rg_map.get(task_name, set())
                            task_row = {
                                "Task": f"{arrow}{task_name}",
                                "Project no.": ", ".join(sorted(_task_projs)) if _task_projs else "",
                                "Resource group": "Various" if len(_task_rgs) > 1 else (next(iter(_task_rgs)) if _task_rgs else ""),
                                "Toggle": task_open,
                                "_row_type": "task",
                                "_task": task_name,
                            }
                            for c in month_cols:
                                task_row[c] = float(trow[c])
                            task_row["Total (€)"] = sum(float(trow[c]) for c in month_cols) if month_cols else 0.0
                            combined_rows.append(task_row)

                            if task_open:
                                df_task = df_lab[df_lab["Task"] == task_name].copy()
                                df_task["Month"] = pd.to_datetime(df_task["date"], errors="coerce").dt.strftime("%Y-%m").fillna("Unknown")
                                _emp_rg_col = "resource_group" if "resource_group" in df_task.columns else None
                                _emp_proj_col = "project_no" if "project_no" in df_task.columns else None
                                emp_month = (
                                    df_task.groupby(["Employee", "Month"], as_index=False)["amount"]
                                    .sum()
                                    .rename(columns={"amount": "Amount (€)"})
                                )
                                emp_pivot = emp_month.pivot_table(
                                    index="Employee",
                                    columns="Month",
                                    values="Amount (€)",
                                    aggfunc="sum",
                                    fill_value=0,
                                )
                                if not emp_pivot.empty:
                                    for mc in month_cols:
                                        if mc not in emp_pivot.columns:
                                            emp_pivot[mc] = 0.0
                                    emp_pivot = emp_pivot[month_cols]
                                    emp_pivot["Total (€)"] = emp_pivot.sum(axis=1)
                                    for emp_name, erow in emp_pivot.iterrows():
                                        _emp_rows = df_task[df_task["Employee"] == emp_name]
                                        _emp_proj = ", ".join(sorted(_emp_rows[_emp_proj_col].dropna().astype(str).unique())) if _emp_proj_col else ""
                                        _emp_rg = ", ".join(sorted(_emp_rows[_emp_rg_col].dropna().astype(str).unique())) if _emp_rg_col else ""
                                        child_row = {
                                            "Task": f"\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0\u21b3 {emp_name}",
                                            "Project no.": _emp_proj,
                                            "Resource group": _emp_rg,
                                            "Toggle": False,
                                            "_row_type": "employee",
                                            "_task": task_name,
                                        }
                                        for c in month_cols:
                                            child_row[c] = float(erow[c])
                                        child_row["Total (€)"] = sum(float(erow[c]) for c in month_cols) if month_cols else 0.0
                                        combined_rows.append(child_row)

                        combined_df = pd.DataFrame(combined_rows) if combined_rows else pd.DataFrame(columns=["Task", "Project no.", "Resource group", "Toggle"] + month_cols + ["Total (€)", "_row_type", "_task"])

                        total_row = {"Task": "TOTAL", "Project no.": "", "Resource group": "", "Toggle": False, "_row_type": "total", "_task": ""}
                        for c in month_cols:
                            total_row[c] = pd.to_numeric(filtered_tasks[c], errors="coerce").fillna(0).sum() if c in filtered_tasks.columns else 0.0
                        total_row["Total (€)"] = sum(float(total_row[c]) for c in month_cols) if month_cols else 0.0
                        combined_df = pd.concat([combined_df, pd.DataFrame([total_row])], ignore_index=True)

                        display_df = combined_df[["Task", "Project no.", "Resource group"] + month_cols + ["Total (€)"]].copy()
                        import hashlib as _hl
                        _open_sig = "_".join(f"{k}={v}" for k, v in sorted(open_state.items()))
                        _table_key = "atme_" + _hl.md5(_open_sig.encode()).hexdigest()[:8]

                        # Add expand/collapse all buttons
                        _btn_col1, _btn_col2, _btn_col3 = st.columns([1, 1, 2])
                        if _btn_col1.button("▼ Expand All", key="expand_all_tasks_443", use_container_width=True):
                            for task in filtered_tasks["Task"].unique():
                                open_state[str(task)] = True
                            st.rerun()
                        if _btn_col2.button("▶ Collapse All", key="collapse_all_tasks_443", use_container_width=True):
                            for task in filtered_tasks["Task"].unique():
                                open_state[str(task)] = False
                            st.rerun()

                        st.caption("Click a task row to expand / collapse its employee breakdown.")
                        sel_result = st.dataframe(
                            display_df,
                            hide_index=True,
                            use_container_width=True,
                            on_select="rerun",
                            selection_mode="single-row",
                            column_config={
                                "Task": st.column_config.TextColumn("Task"),
                                "Project no.": st.column_config.TextColumn("Project no."),
                                "Resource group": st.column_config.TextColumn("Resource group"),
                                **{c: st.column_config.NumberColumn(c, format="€ %,.2f") for c in month_cols + ["Total (€)"]},
                            },
                            key=_table_key,
                        )
                        _sel_rows = sel_result.selection.get("rows", []) if sel_result and hasattr(sel_result, "selection") else []
                        if _sel_rows:
                            _clicked_idx = _sel_rows[0]
                            if _clicked_idx < len(combined_df):
                                _clicked_type = combined_df.iloc[_clicked_idx]["_row_type"]
                                _clicked_task = combined_df.iloc[_clicked_idx]["_task"]
                                if _clicked_type == "task" and _clicked_task:
                                    open_state[_clicked_task] = not open_state.get(_clicked_task, False)
                                    st.rerun()

            st.markdown("---")
            with st.expander("📊 4.4.5 Labour Actuals by Project No and Month (€)"):
                _df_lab_pno = df_a[df_a["category"].fillna("").str.lower() == "labour"].copy()
                if _df_lab_pno.empty:
                    st.info("No labour actuals available.")
                else:
                    _df_lab_pno["Project no."] = _df_lab_pno.get("project_no", "").fillna("").astype(str).str.strip()
                    _df_lab_pno.loc[_df_lab_pno["Project no."].eq(""), "Project no."] = "(blank)"
                    _df_lab_pno["Month"] = pd.to_datetime(_df_lab_pno.get("date"), errors="coerce").dt.strftime("%Y-%m")
                    _df_lab_pno = _df_lab_pno[_df_lab_pno["Month"].notna()].copy()

                    if _df_lab_pno.empty:
                        st.info("No dated labour actuals available for monthly grouping.")
                    else:
                        _all_proj_no = sorted(_df_lab_pno["Project no."].astype(str).unique().tolist())
                        _sel_proj_no = st.multiselect(
                            "Filter Project no.",
                            _all_proj_no,
                            default=_all_proj_no,
                            key="actuals_projno_filter_445",
                        )
                        if _sel_proj_no:
                            _df_lab_pno = _df_lab_pno[_df_lab_pno["Project no."].isin(_sel_proj_no)].copy()
                        else:
                            _df_lab_pno = _df_lab_pno.iloc[0:0].copy()

                        _all_months = sorted(_df_lab_pno["Month"].astype(str).unique().tolist()) if not _df_lab_pno.empty else []
                        _years = sorted({m[:4] for m in _all_months}) if _all_months else []
                        _y1, _y2 = st.columns(2)
                        _sel_years = _y1.multiselect(
                            "Filter Year",
                            _years,
                            default=_years,
                            key="actuals_projno_year_filter_445",
                        )
                        _month_opts = [m for m in _all_months if (not _sel_years or m[:4] in _sel_years)]
                        _sel_months = _y2.multiselect(
                            "Filter Month",
                            _month_opts,
                            default=_month_opts,
                            key="actuals_projno_month_filter_445",
                        )
                        if _sel_months:
                            _df_lab_pno = _df_lab_pno[_df_lab_pno["Month"].isin(_sel_months)].copy()
                        else:
                            _df_lab_pno = _df_lab_pno.iloc[0:0].copy()

                        if _df_lab_pno.empty:
                            st.info("No rows after filters.")
                        else:
                            _grp = (
                                _df_lab_pno.groupby(["Project no.", "Month"], as_index=False)["amount"]
                                .sum()
                                .rename(columns={"amount": "Amount (€)"})
                            )
                            _pivot = _grp.pivot_table(
                                index="Project no.",
                                columns="Month",
                                values="Amount (€)",
                                aggfunc="sum",
                                fill_value=0.0,
                            )
                            _month_cols = sorted(_pivot.columns.tolist()) if len(_pivot.columns) else []
                            if _month_cols:
                                _pivot = _pivot[_month_cols]
                            _pivot["Total (€)"] = _pivot.sum(axis=1)
                            _tbl = _pivot.reset_index().sort_values("Total (€)", ascending=False)

                            _total_row = {"Project no.": "TOTAL"}
                            for _mc in _month_cols:
                                _total_row[_mc] = float(pd.to_numeric(_tbl[_mc], errors="coerce").fillna(0.0).sum())
                            _total_row["Total (€)"] = float(pd.to_numeric(_tbl["Total (€)"], errors="coerce").fillna(0.0).sum())
                            _tbl = pd.concat([_tbl, pd.DataFrame([_total_row])], ignore_index=True)

                            st.dataframe(
                                _tbl,
                                hide_index=True,
                                use_container_width=True,
                                column_config={
                                    "Project no.": st.column_config.TextColumn("Project no."),
                                    **{c: st.column_config.NumberColumn(c, format="€ %,.2f") for c in _month_cols + ["Total (€)"]},
                                },
                            )

            with st.expander("📊 4.4.6 Other Actuals by Project No and Month (€)"):
                _df_other_pno = df_a[df_a["category"].fillna("").str.lower() != "labour"].copy()
                if _df_other_pno.empty:
                    st.info("No other actuals available.")
                else:
                    _df_other_pno["Project no."] = _df_other_pno.get("project_no", "").fillna("").astype(str).str.strip()
                    _df_other_pno.loc[_df_other_pno["Project no."].eq(""), "Project no."] = "(blank)"
                    _df_other_pno["Month"] = pd.to_datetime(_df_other_pno.get("date"), errors="coerce").dt.strftime("%Y-%m")
                    _df_other_pno = _df_other_pno[_df_other_pno["Month"].notna()].copy()

                    if _df_other_pno.empty:
                        st.info("No dated other actuals available for monthly grouping.")
                    else:
                        _all_proj_no = sorted(_df_other_pno["Project no."].astype(str).unique().tolist())
                        _sel_proj_no = st.multiselect(
                            "Filter Other Project no.",
                            _all_proj_no,
                            default=_all_proj_no,
                            key="actuals_projno_filter_446",
                        )
                        if _sel_proj_no:
                            _df_other_pno = _df_other_pno[_df_other_pno["Project no."].isin(_sel_proj_no)].copy()
                        else:
                            _df_other_pno = _df_other_pno.iloc[0:0].copy()

                        _all_months = sorted(_df_other_pno["Month"].astype(str).unique().tolist()) if not _df_other_pno.empty else []
                        _years = sorted({m[:4] for m in _all_months}) if _all_months else []
                        _y1, _y2 = st.columns(2)
                        _sel_years = _y1.multiselect(
                            "Filter Other Year",
                            _years,
                            default=_years,
                            key="actuals_projno_year_filter_446",
                        )
                        _month_opts = [m for m in _all_months if (not _sel_years or m[:4] in _sel_years)]
                        _sel_months = _y2.multiselect(
                            "Filter Other Month",
                            _month_opts,
                            default=_month_opts,
                            key="actuals_projno_month_filter_446",
                        )
                        if _sel_months:
                            _df_other_pno = _df_other_pno[_df_other_pno["Month"].isin(_sel_months)].copy()
                        else:
                            _df_other_pno = _df_other_pno.iloc[0:0].copy()

                        if _df_other_pno.empty:
                            st.info("No rows after filters.")
                        else:
                            _grp = (
                                _df_other_pno.groupby(["Project no.", "Month"], as_index=False)["amount"]
                                .sum()
                                .rename(columns={"amount": "Amount (€)"})
                            )
                            _pivot = _grp.pivot_table(
                                index="Project no.",
                                columns="Month",
                                values="Amount (€)",
                                aggfunc="sum",
                                fill_value=0.0,
                            )
                            _month_cols = sorted(_pivot.columns.tolist()) if len(_pivot.columns) else []
                            if _month_cols:
                                _pivot = _pivot[_month_cols]
                            _pivot["Total (€)"] = _pivot.sum(axis=1)
                            _tbl = _pivot.reset_index().sort_values("Total (€)", ascending=False)

                            _total_row = {"Project no.": "TOTAL"}
                            for _mc in _month_cols:
                                _total_row[_mc] = float(pd.to_numeric(_tbl[_mc], errors="coerce").fillna(0.0).sum())
                            _total_row["Total (€)"] = float(pd.to_numeric(_tbl["Total (€)"], errors="coerce").fillna(0.0).sum())
                            _tbl = pd.concat([_tbl, pd.DataFrame([_total_row])], ignore_index=True)

                            st.dataframe(
                                _tbl,
                                hide_index=True,
                                use_container_width=True,
                                column_config={
                                    "Project no.": st.column_config.TextColumn("Project no."),
                                    **{c: st.column_config.NumberColumn(c, format="€ %,.2f") for c in _month_cols + ["Total (€)"]},
                                },
                            )

            with st.expander("🗑️ 4.4.4 Delete an actual entry"):
                del_options = {}
                for r in actuals:
                    _emp = str(r.get("employee_name") or "").strip() or "-"
                    _rg = str(r.get("resource_group") or "").strip() or "-"
                    _label = (
                        f"[{r['id']}] {r['date']} - {r['category']} | "
                        f"Emp: {_emp} | RG: {_rg} | (€{r['amount']:,.0f})"
                    )
                    del_options[_label] = r["id"]
                sel = st.selectbox("Select entry to delete", list(del_options.keys()))
                if st.button("Delete selected entry"):
                    db.delete_actual(del_options[sel])
                    st.success("Deleted.")
                    st.rerun()

# ════════════════════════════════════════════════════════════════════════════
# 5. FORECAST
# ════════════════════════════════════════════════════════════════════════════
elif page == "🔮 Forecast":
    st.title("🔮 5. Forecast (Estimate to Complete)")
    st.caption("Reference: 5.1 Estimated to complete adjustment | 5.2 Financial risks")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    with st.expander("🛠️ 5.1 Estimated to complete adjustment", expanded=True):
        st.caption(
            "Pivot-style table showing actuals for completed months and plan for remaining months. "
            "All columns represent project months with a total column and total row."
        )

        _fc_tasks = db.get_plan_tasks(project_id)
        if not _fc_tasks:
            st.info("No budget plan tasks found for this project.")
        else:
            _fc_vmap = db.get_plan_values_for_project(project_id)
            _fc_tvmap = db.get_plan_text_values_for_project(project_id)
            _fc_cols = db.get_plan_columns(project_id)
            _fc_actuals = db.get_actuals(project_id)
            
            import db_task_mappings
            # Build two-tier mapping dict: (task, mcr) -> plan_task  and  task -> plan_task (catch-all)
            _task_mappings = db_task_mappings.get_all_task_mappings(project_id)
            _task_mapping_exact   = {(m["actual_task_name"], m["actual_mcr"]): m["plan_task_name"] for m in _task_mappings if m["actual_mcr"]}
            _task_mapping_catchall = {m["actual_task_name"]: m["plan_task_name"] for m in _task_mappings if not m["actual_mcr"]}

            _fc_mcr_group_col = _select_best_project_mcr_col(
                _fc_cols,
                _fc_tasks,
                _fc_tvmap,
                _fc_vmap,
            )

            _fc_col_keys = [c.get("col_key") for c in _fc_cols]
            _fc_rg_type_col = next(
                (
                    k for k in _fc_col_keys
                    if _norm_key_tokens(k) in {"sp rg type", "sp rg type t", "rg type"}
                    or "sp rg type" in _norm_key_tokens(k)
                ),
                None,
            )

            if not _fc_mcr_group_col:
                st.info("No Project MCR / Project No. column found for this project.")
            else:
                try:
                    _fs = datetime.date.fromisoformat(project.get("start_date") or "")
                    _fe = datetime.date.fromisoformat(project.get("end_date") or "")
                except Exception:
                    _fs = datetime.date.today().replace(day=1)
                    _fe = _fs.replace(year=_fs.year + 1)

                _months = []
                _cur = _fs.replace(day=1)
                while _cur <= _fe.replace(day=1):
                    _months.append(_cur.strftime("%Y-%m"))
                    if _cur.month == 12:
                        _cur = _cur.replace(year=_cur.year + 1, month=1)
                    else:
                        _cur = _cur.replace(month=_cur.month + 1)

                _financial_risk_key = "Financial risks"
                _financial_risk_bundle = db.compute_project_financial_risk_monthly(project_id, _months)
                _financial_risk_monthly = _financial_risk_bundle.get("monthly_totals", {})
                if _financial_risk_bundle.get("errors"):
                    st.warning("Financial risk validation: " + " | ".join(_financial_risk_bundle.get("errors", [])))

                def _fc_canonical_mcr(_raw):
                    _txt = str(_raw or "").strip()
                    if not _txt:
                        return ""
                    _m = re.search(r"([A-Za-z]{2,}-\d+_\d+)", _txt)
                    return _m.group(1) if _m else _txt

                _rows = []
                _task_lookup = {}
                for _t in _fc_tasks:
                    _tid = int(_t["id"])
                    _mcr_val = str(_fc_tvmap.get((_tid, _fc_mcr_group_col), "") or "").strip()
                    if not _mcr_val:
                        _mcr_val = str(_fc_vmap.get((_tid, _fc_mcr_group_col), "") or "").strip()
                    _mcr_val = _fc_canonical_mcr(_mcr_val)
                    if not _mcr_val:
                        _mcr_val = "(blank)"

                    _rg_type_val = ""
                    if _fc_rg_type_col:
                        _rg_type_val = str(_fc_tvmap.get((_tid, _fc_rg_type_col), "") or "").strip()
                        if not _rg_type_val:
                            _rg_type_val = str(_fc_vmap.get((_tid, _fc_rg_type_col), "") or "").strip()

                    _tn = str(_t.get("task_name") or "")
                    _rows.append(
                        {
                            "Project MCR #": _mcr_val,
                            "Task Name": _tn,
                            "RG type": _rg_type_val,
                            "Resource group": str(_t.get("resource_group") or ""),
                            "__task_id": _tid,
                        }
                    )
                    _task_lookup[_tn] = _tid

                _src_df = pd.DataFrame(_rows)
                _today = datetime.date.today()
                _current_ym = _today.strftime("%Y-%m")
                _month_index = {m: i for i, m in enumerate(_months)}

                _task_mcr_lookup = {}
                for _, _sr in _src_df.iterrows():
                    _tn = str(_sr.get("Task Name") or "").strip()
                    _mv = str(_sr.get("Project MCR #") or "").strip() or "(blank)"
                    if _tn and _tn not in _task_mcr_lookup:
                        _task_mcr_lookup[_tn] = _mv

                # Build monthly actuals data
                # For BM-00110021_004 and BM-00110021_005: aggregate by MCR only (task name mismatch issue)
                # For others: try task-based matching first, applying task name mappings
                _mcrs_without_task_match = {"BM-00110021_004", "BM-00110021_005"}
                _monthly_by_key = {}  # (MCR, task) -> month -> amount
                _monthly_by_mcr = {}  # MCR -> month -> amount (for MCRs without task match)
                
                for _a in _fc_actuals:
                    _dt = pd.to_datetime(_a.get("date"), errors="coerce")
                    if pd.isna(_dt):
                        continue
                    _ym = _dt.strftime("%Y-%m")
                    if _ym not in _month_index or _ym >= _current_ym:
                        continue
                    _amt = float(_a.get("amount", 0.0) or 0.0)
                    _task_name_raw = str(_a.get("task_name") or "").strip()
                    _mcr_from_actual = _fc_canonical_mcr(_a.get("project_no"))
                    # Apply task name mapping: exact (task+MCR) takes priority over catch-all (task only)
                    _task_name_key = (
                        _task_mapping_exact.get((_task_name_raw, _mcr_from_actual))
                        or _task_mapping_catchall.get(_task_name_raw)
                        or _task_name_raw
                        or "(unmapped task)"
                    )
                    if not _mcr_from_actual:
                        _mcr_from_actual = "(blank)"
                    
                    # If this MCR has known task mismatch, aggregate by MCR only
                    if _mcr_from_actual in _mcrs_without_task_match:
                        _monthly_by_mcr.setdefault(_mcr_from_actual, {}).setdefault(_ym, 0.0)
                        _monthly_by_mcr[_mcr_from_actual][_ym] = _monthly_by_mcr[_mcr_from_actual][_ym] + _amt
                    else:
                        # Try task-based matching for other MCRs
                        _mcr_key = _mcr_from_actual or _task_mcr_lookup.get(_task_name_key, "(blank)")
                        _key = (_mcr_key, _task_name_key)
                        _monthly_by_key.setdefault(_key, {})[_ym] = _monthly_by_key.get(_key, {}).get(_ym, 0.0) + _amt

                # MCR expand/collapse state (only for MCRs that have task-level data)
                _mcr_values = sorted(set(_src_df["Project MCR #"].astype(str).unique().tolist()))
                _mcr_values_all = _mcr_values + [_financial_risk_key]
                _mcr_expandable = [m for m in _mcr_values if m not in _mcrs_without_task_match]
                _mcr_expanded_key = f"forecast_pivot_expanded__{project_id}"
                if _mcr_expanded_key not in st.session_state:
                    st.session_state[_mcr_expanded_key] = {}

                # Show controls only if there are expandable MCRs
                if _mcr_expandable:
                    _g1, _g2, _g3, _g4 = st.columns([3, 1, 1, 3])
                    _sel_mcr = _g1.selectbox("Project MCR # (expandable only)", _mcr_expandable, key=f"{_mcr_expanded_key}__pick")
                    if _g2.button("Toggle", key=f"{_mcr_expanded_key}__toggle"):
                        _emap = st.session_state[_mcr_expanded_key]
                        _emap[_sel_mcr] = not bool(_emap.get(_sel_mcr, False))
                        st.session_state[_mcr_expanded_key] = _emap
                        st.rerun()
                    if _g3.button("Expand All", key=f"{_mcr_expanded_key}__expand_all"):
                        st.session_state[_mcr_expanded_key] = {k: True for k in _mcr_expandable}
                        st.rerun()
                    if _g4.button("Collapse All", key=f"{_mcr_expanded_key}__collapse_all"):
                        st.session_state[_mcr_expanded_key] = {}
                        st.rerun()
                
                # Info message for non-expandable MCRs
                if _mcrs_without_task_match:
                    st.info(f"📌 **MCRs without task-level breakdown**: {', '.join(sorted(_mcrs_without_task_match))} (shown at aggregated level only, no expand/collapse toggle)")

                # Build pivot table rows
                _pivot_rows = []
                _col_headers = ["Project MCR #", "Task Name", "RG type", "Resource group"] + _months + ["Estimated at Completion (EAC)", "Total Plan", "Δ vs Total Plan"]

                for _mcr in _mcr_values:
                    _is_non_expandable = _mcr in _mcrs_without_task_match
                    _mdf = _src_df[_src_df["Project MCR #"] == _mcr].copy()

                    _task_rows = []
                    # Only build task rows if this MCR allows task-level breakdown
                    if not _is_non_expandable:
                        _task_agg = _mdf.groupby("Task Name", dropna=False, as_index=False).agg({
                            "RG type": lambda s: ", ".join(sorted({str(v).strip() for v in s if str(v).strip()})),
                            "Resource group": lambda s: ", ".join(sorted({str(v).strip() for v in s if str(v).strip()})),
                            "__task_id": lambda s: [int(v) for v in s if pd.notna(v)],
                        }).sort_values("Task Name")

                        for _, _tr in _task_agg.iterrows():
                            _tn = str(_tr.get("Task Name") or "").strip()
                            _task_name_key = _tn or "(unmapped task)"
                            _task_ids = [int(v) for v in (_tr.get("__task_id") or []) if int(v) > 0]
                            _tid = _task_ids[0] if _task_ids else 0

                            _row = {
                                "Project MCR #": "",
                                "Task Name": f"↳ {_tn}",
                                "RG type": str(_tr.get("RG type") or ""),
                                "Resource group": str(_tr.get("Resource group") or ""),
                                "__mcr_key": _mcr,
                                "__task_id": _tid,
                            }

                            _row_total = 0.0
                            _total_plan = 0.0
                            for _m in _months:
                                if _m < _current_ym:
                                    # Use task-based matching
                                    _key = (_mcr, _task_name_key)
                                    _val = float(_monthly_by_key.get(_key, {}).get(_m, 0.0) or 0.0)
                                else:
                                    # For future months: sum plan amounts across all plan rows sharing this task name.
                                    _val = 0.0
                                    for _task_id in _task_ids:
                                        _val += float(_fc_vmap.get((_task_id, _m), 0.0) or 0.0)
                                _row[_m] = _val
                                _row_total += _val
                                # Total Plan: sum of all plan values across all months
                                for _task_id in _task_ids:
                                    _total_plan += float(_fc_vmap.get((_task_id, _m), 0.0) or 0.0)
                            _row["Estimated at Completion (EAC)"] = _row_total
                            _row["Total Plan"] = _total_plan
                            _row["Δ vs Total Plan"] = _row_total - _total_plan
                            _task_rows.append(_row)

                    # Build MCR-level row with appropriate totals
                    _mcr_row = {
                        "Project MCR #": _mcr,
                        "Task Name": "(MCR Total)" if _is_non_expandable else "",
                        "RG type": "",
                        "Resource group": "",
                        "__mcr_key": _mcr,
                        "__task_id": None,
                    }

                    _mcr_total = 0.0
                    _mcr_total_plan = 0.0
                    for _m in _months:
                        if _is_non_expandable:
                            # Non-expandable MCR: use MCR-level aggregation for all months
                            if _m < _current_ym:
                                # Past months: use actuals from _monthly_by_mcr
                                _m_total = float(_monthly_by_mcr.get(_mcr, {}).get(_m, 0.0) or 0.0)
                            else:
                                # Future months: sum plan values across all tasks for this MCR
                                _m_total = 0.0
                                for _, _sr in _mdf.iterrows():
                                    _tid = int(_sr.get("__task_id") or 0)
                                    _m_total += float(_fc_vmap.get((_tid, _m), 0.0) or 0.0)
                        else:
                            # Expandable MCR: sum from task rows (completed months) or from plan (future months)
                            _m_total = 0.0
                            for _task_row in _task_rows:
                                _m_total += _task_row.get(_m, 0.0)
                        _mcr_row[_m] = _m_total
                        _mcr_total += _m_total
                        # Total Plan: sum of all task plan totals for this MCR
                        for _, _sr in _mdf.iterrows():
                            _tid = int(_sr.get("__task_id") or 0)
                            _mcr_total_plan += float(_fc_vmap.get((_tid, _m), 0.0) or 0.0)
                    _mcr_row["Estimated at Completion (EAC)"] = _mcr_total
                    _mcr_row["Total Plan"] = _mcr_total_plan
                    _mcr_row["Δ vs Total Plan"] = _mcr_total - _mcr_total_plan
                    _pivot_rows.append(_mcr_row)

                    # Only add task rows and expand/collapse if this MCR is expandable
                    if not _is_non_expandable:
                        if bool(st.session_state[_mcr_expanded_key].get(_mcr, False)):
                            _pivot_rows.extend(_task_rows)

                # Dedicated row for project-level financial risks (residual amounts only).
                _risk_row = {
                    "Project MCR #": _financial_risk_key,
                    "Task Name": "",
                    "RG type": "",
                    "Resource group": "",
                    "__mcr_key": _financial_risk_key,
                    "__task_id": None,
                }
                _risk_total = 0.0
                for _m in _months:
                    _rv = float(_financial_risk_monthly.get(_m, 0.0) or 0.0)
                    _risk_row[_m] = _rv
                    _risk_total += _rv
                _risk_row["Estimated at Completion (EAC)"] = _risk_total
                _risk_row["Total Plan"] = 0.0
                _risk_row["Δ vs Total Plan"] = _risk_total
                _pivot_rows.append(_risk_row)

                # Total row
                _total_row = {
                    "Project MCR #": "TOTAL",
                    "Task Name": "",
                    "RG type": "",
                    "Resource group": "",
                    "__mcr_key": "TOTAL",
                    "__task_id": None,
                }
                _grand_total = 0.0
                _grand_total_plan = 0.0
                for _m in _months:
                    _col_total = 0.0
                    for _row in _pivot_rows:
                        _col_total += _row.get(_m, 0.0)
                    _total_row[_m] = _col_total
                    _grand_total += _col_total
                    # Total Plan: sum of all plan values across all MCRs for each month
                    for _mcr in _mcr_values:
                        _mdf = _src_df[_src_df["Project MCR #"] == _mcr]
                        for _, _sr in _mdf.iterrows():
                            _tid = int(_sr.get("__task_id") or 0)
                            _grand_total_plan += float(_fc_vmap.get((_tid, _m), 0.0) or 0.0)
                _total_row["Estimated at Completion (EAC)"] = _grand_total
                _total_row["Total Plan"] = _grand_total_plan
                _total_row["Δ vs Total Plan"] = _grand_total - _grand_total_plan
                _pivot_rows.append(_total_row)

                _pivot_df = pd.DataFrame(_pivot_rows)
                for _c in _col_headers:
                    if _c not in _pivot_df.columns:
                        _pivot_df[_c] = ""
                if "__mcr_key" not in _pivot_df.columns:
                    _pivot_df["__mcr_key"] = _pivot_df["Project MCR #"].astype(str)
                if "__task_id" not in _pivot_df.columns:
                    _pivot_df["__task_id"] = None
                _pivot_df = _pivot_df[_col_headers + ["__mcr_key", "__task_id"]]

                # Create indicator row showing Actual vs Plan for each month
                _indicator_row = {
                    "Project MCR #": "Data Type",
                    "Task Name": "",
                    "RG type": "",
                    "Resource group": "",
                }
                for _m in _months:
                    if _m < _current_ym:
                        _indicator_row[_m] = "📊 Actual"
                    else:
                        _indicator_row[_m] = "📈 Plan"
                _indicator_row["Estimated at Completion (EAC)"] = ""
                _indicator_row["Total Plan"] = ""
                _indicator_row["Δ vs Total Plan"] = ""

                # Keep indicator row separate from the editor to avoid mixed dtypes in month columns.
                _indicator_df = pd.DataFrame([_indicator_row])[_col_headers]
                st.dataframe(
                    _indicator_df,
                    hide_index=True,
                    use_container_width=True,
                )

                # Load existing overrides and build override lookup
                import db_forecast_overrides
                import db_adjustment_reasons

                # Load existing reasons: (mcr, task_id) -> reason_text
                _reason_map = db_adjustment_reasons.get_reason_map(project_id)
                _all_overrides = db_forecast_overrides.get_all_forecast_overrides(project_id)
                _mcr_has_task0 = set(
                    str(_mcr).strip()
                    for _mcr, _grp in _src_df.groupby("Project MCR #")
                    if any(int(pd.to_numeric(_r.get("__task_id"), errors="coerce") or 0) == 0 for _, _r in _grp.iterrows())
                )
                _override_map = {}  # (row_mcr, row_task_id, month) -> override_amount
                for _ov in _all_overrides:
                    _ov_task_id_raw = _ov.get("task_id")
                    if _ov_task_id_raw is None:
                        _ov_task_id = -1
                    else:
                        _ov_task_id = int(_ov_task_id_raw)
                    # Backward compatibility: historical MCR-level rows may be persisted as 0.
                    if _ov_task_id == 0 and str(_ov.get("mcr") or "").strip() not in _mcr_has_task0:
                        _ov_task_id = -1
                    _key = (_ov["mcr"], _ov_task_id, _ov["month_year"])
                    _override_map[_key] = _ov["override_amount"]

                # MCR-level overrides are only valid for non-expandable MCRs.
                _expandable_mcrs = set(_mcr_values) - set(_mcrs_without_task_match)
                _stale_mcr_override_keys = [
                    (_mcr, _tid, _m)
                    for (_mcr, _tid, _m) in _override_map.keys()
                    if _tid == -1 and _mcr in _expandable_mcrs
                ]
                if _stale_mcr_override_keys:
                    for _mcr, _tid, _m in _stale_mcr_override_keys:
                        _override_map.pop((_mcr, _tid, _m), None)
                        # Cleanup legacy invalid entries created by previous buggy save behavior.
                        db_forecast_overrides.delete_forecast_override(project_id, _tid, _mcr, _m)

                _editor_version_key = f"forecast_editor_version_{project_id}"
                _editor_live_df_key = f"forecast_editor_live_df_{project_id}"
                _editor_live_keys_key = f"forecast_editor_live_keys_{project_id}"
                if _editor_version_key not in st.session_state:
                    st.session_state[_editor_version_key] = 0

                # Create original copy for change detection
                _pivot_df_original = _pivot_df.copy()

                # Apply overrides to the dataframe (only for future months)
                for (_ov_mcr, _ov_task_id, _ov_month), _ov_amount in _override_map.items():
                    if _ov_month < _current_ym:
                        continue
                    # Find matching row: look for row where "Project MCR #" == _ov_mcr
                    for _idx, _row in _pivot_df.iterrows():
                        _row_mcr = str(_row.get("__mcr_key", "")).strip()
                        if _row_mcr == _ov_mcr and _ov_month in _pivot_df.columns:
                            # task_id -1 denotes MCR-level override
                            if _ov_task_id == -1:
                                if _row.get("Task Name") == "(MCR Total)":
                                    _pivot_df.at[_idx, _ov_month] = _ov_amount
                            else:
                                # Task-level override - match by task_id
                                _row_task_id = _row.get("__task_id")
                                _row_task_id_norm = int(_row_task_id) if pd.notna(_row_task_id) else -1
                                if _row_task_id_norm == _ov_task_id:
                                    _pivot_df.at[_idx, _ov_month] = _ov_amount

                # Recalculate row-level EAC after overrides so task rows reflect edited plan values.
                for _idx, _row in _pivot_df.iterrows():
                    if str(_row.get("Project MCR #") or "").strip() == "TOTAL":
                        continue
                    _row_eac = 0.0
                    for _m in _months:
                        _row_eac += float(pd.to_numeric(_pivot_df.at[_idx, _m], errors="coerce") or 0.0)
                    _pivot_df.at[_idx, "Estimated at Completion (EAC)"] = _row_eac

                # Recalculate MCR rollups and TOTAL so task-level overrides are reflected in aggregate rows.
                _mcr_row_idx = {
                    str(_r.get("Project MCR #") or "").strip(): _i
                    for _i, _r in _pivot_df.iterrows()
                    if str(_r.get("Project MCR #") or "").strip() not in {"", "TOTAL"}
                }
                _total_idx_list = _pivot_df.index[_pivot_df["Project MCR #"].astype(str).str.strip() == "TOTAL"].tolist()
                _total_idx = _total_idx_list[0] if _total_idx_list else None

                for _mcr in _mcr_values_all:
                    if _mcr not in _mcr_row_idx:
                        continue
                    _mcr_idx = _mcr_row_idx[_mcr]
                    _mcr_sum_all_months = 0.0

                    if _mcr == _financial_risk_key:
                        _risk_sum_all_months = 0.0
                        for _m in _months:
                            _rv = float(_financial_risk_monthly.get(_m, 0.0) or 0.0)
                            _pivot_df.at[_mcr_idx, _m] = _rv
                            _risk_sum_all_months += _rv
                        _pivot_df.at[_mcr_idx, "Estimated at Completion (EAC)"] = _risk_sum_all_months
                        _pivot_df.at[_mcr_idx, "Total Plan"] = 0.0
                        _pivot_df.at[_mcr_idx, "Δ vs Total Plan"] = _risk_sum_all_months
                        continue

                    _mdf_for_rollup = _src_df[_src_df["Project MCR #"] == _mcr]

                    for _m in _months:
                        if _m < _current_ym:
                            _m_total = float(pd.to_numeric(_pivot_df.at[_mcr_idx, _m], errors="coerce") or 0.0)
                        else:
                            # MCR-level override takes precedence when present.
                            if (_mcr, -1, _m) in _override_map:
                                _m_total = float(_override_map.get((_mcr, -1, _m), 0.0) or 0.0)
                            else:
                                _m_total = 0.0
                                for _, _sr in _mdf_for_rollup.iterrows():
                                    _tid = int(_sr.get("__task_id") or 0)
                                    _base_val = float(_fc_vmap.get((_tid, _m), 0.0) or 0.0)
                                    _m_total += float(_override_map.get((_mcr, _tid, _m), _base_val) or 0.0)

                        _pivot_df.at[_mcr_idx, _m] = _m_total
                        _mcr_sum_all_months += _m_total

                    _pivot_df.at[_mcr_idx, "Estimated at Completion (EAC)"] = _mcr_sum_all_months

                if _total_idx is not None:
                    _grand_total_all_months = 0.0
                    for _m in _months:
                        _col_total = 0.0
                        for _mcr in _mcr_values_all:
                            _idx = _mcr_row_idx.get(_mcr)
                            if _idx is None:
                                continue
                            _col_total += float(pd.to_numeric(_pivot_df.at[_idx, _m], errors="coerce") or 0.0)
                        _pivot_df.at[_total_idx, _m] = _col_total
                        _grand_total_all_months += _col_total
                    _pivot_df.at[_total_idx, "Estimated at Completion (EAC)"] = _grand_total_all_months
                    db.set_section_5_1_eac_cache(project_id, _grand_total_all_months)

                # Keep deviation column in sync after all EAC/rollup recalculations.
                for _idx, _row in _pivot_df.iterrows():
                    _eac_val = float(pd.to_numeric(_row.get("Estimated at Completion (EAC)"), errors="coerce") or 0.0)
                    _plan_val = float(pd.to_numeric(_row.get("Total Plan"), errors="coerce") or 0.0)
                    _pivot_df.at[_idx, "Δ vs Total Plan"] = _eac_val - _plan_val

                # Inject Adjustment Reason column for task rows (read-only display)
                _pivot_df["Adjustment Reason"] = ""
                for _idx, _row in _pivot_df.iterrows():
                    _rid_task = _row.get("__task_id")
                    _rid_mcr  = str(_row.get("__mcr_key") or "").strip()
                    if _rid_mcr in {"", "TOTAL", _financial_risk_key}:
                        continue
                    _tid_int = int(_rid_task) if pd.notna(_rid_task) else -1
                    _pivot_df.at[_idx, "Adjustment Reason"] = _reason_map.get(
                        (_rid_mcr, _tid_int),
                        _reason_map.get((_rid_mcr, 0), "") if _tid_int == -1 else "",
                    )

                # Determine which columns are editable (future months + reason text)
                _edit_cols = [c for c in _months if c >= _current_ym]
                _editor_df = _pivot_df.drop(columns=["__task_id", "__mcr_key"], errors="ignore")
                _readonly_cols = [c for c in _editor_df.columns if c not in _edit_cols and c != "Adjustment Reason"]

                # Stable row identity to protect live-cache restore from shape/order drift.
                _current_live_keys = []
                for _i in range(len(_pivot_df)):
                    _kmcr = str(_pivot_df.iloc[_i].get("__mcr_key") or "").strip()
                    _ktid_raw = _pivot_df.iloc[_i].get("__task_id")
                    _ktid = int(_ktid_raw) if pd.notna(_ktid_raw) else -1
                    _ktn = str(_pivot_df.iloc[_i].get("Task Name") or "").strip()
                    _current_live_keys.append((_kmcr, _ktid, _ktn))

                def _recompute_live_metrics(_df_in):
                    _df_out = _df_in.copy()

                    def _f(_v):
                        return float(pd.to_numeric(_v, errors="coerce") or 0.0)

                    for _ri in range(len(_df_out)):
                        _row_eac = 0.0
                        for _m in _months:
                            _row_eac += _f(_df_out.iloc[_ri].get(_m))
                        _df_out.at[_ri, "Estimated at Completion (EAC)"] = _row_eac
                        _plan_v = _f(_df_out.iloc[_ri].get("Total Plan"))
                        _df_out.at[_ri, "Δ vs Total Plan"] = _row_eac - _plan_v

                    # Live rollup for expandable MCR rows from their currently visible task rows.
                    _mcr_row_idx = {}
                    for _ri in range(len(_df_out)):
                        _mcr = str(_df_out.iloc[_ri].get("Project MCR #") or "").strip()
                        if _mcr and _mcr not in {"TOTAL", "Data Type"}:
                            _mcr_row_idx[_mcr] = _ri

                    for _mcr, _mcr_idx in _mcr_row_idx.items():
                        if _mcr == _financial_risk_key:
                            continue

                        _task_indices = []
                        _j = _mcr_idx + 1
                        while _j < len(_df_out):
                            _next_mcr = str(_df_out.iloc[_j].get("Project MCR #") or "").strip()
                            _next_task = str(_df_out.iloc[_j].get("Task Name") or "")
                            if _next_mcr:
                                break
                            if _next_task.strip().startswith("↳"):
                                _task_indices.append(_j)
                                _j += 1
                                continue
                            break

                        # Only roll up when task rows are visible (expanded state).
                        if _task_indices:
                            for _m in _months:
                                _sum_m = 0.0
                                for _ti in _task_indices:
                                    _sum_m += _f(_df_out.iloc[_ti].get(_m))
                                _df_out.at[_mcr_idx, _m] = _sum_m

                            _mcr_plan = 0.0
                            for _ti in _task_indices:
                                _mcr_plan += _f(_df_out.iloc[_ti].get("Total Plan"))
                            _df_out.at[_mcr_idx, "Total Plan"] = _mcr_plan

                            _mcr_eac = 0.0
                            for _m in _months:
                                _mcr_eac += _f(_df_out.iloc[_mcr_idx].get(_m))
                            _df_out.at[_mcr_idx, "Estimated at Completion (EAC)"] = _mcr_eac
                            _df_out.at[_mcr_idx, "Δ vs Total Plan"] = _mcr_eac - _mcr_plan

                    # Live rollup for TOTAL row from all MCR rows (including Financial risks).
                    _total_idx = None
                    for _ri in range(len(_df_out)):
                        if str(_df_out.iloc[_ri].get("Project MCR #") or "").strip() == "TOTAL":
                            _total_idx = _ri
                            break
                    if _total_idx is not None:
                        for _m in _months:
                            _col_total = 0.0
                            for _mcr, _mcr_idx in _mcr_row_idx.items():
                                _col_total += _f(_df_out.iloc[_mcr_idx].get(_m))
                            _df_out.at[_total_idx, _m] = _col_total

                        _total_plan_sum = 0.0
                        for _mcr, _mcr_idx in _mcr_row_idx.items():
                            _total_plan_sum += _f(_df_out.iloc[_mcr_idx].get("Total Plan"))
                        _df_out.at[_total_idx, "Total Plan"] = _total_plan_sum

                        _total_eac = 0.0
                        for _m in _months:
                            _total_eac += _f(_df_out.iloc[_total_idx].get(_m))
                        _df_out.at[_total_idx, "Estimated at Completion (EAC)"] = _total_eac
                        _df_out.at[_total_idx, "Δ vs Total Plan"] = _total_eac - _total_plan_sum

                    return _df_out

                # Rehydrate latest unsaved edits so computed columns stay live in the same grid.
                _live_df = st.session_state.get(_editor_live_df_key)
                _live_keys = st.session_state.get(_editor_live_keys_key)
                _can_restore_live = (
                    isinstance(_live_df, pd.DataFrame)
                    and isinstance(_live_keys, list)
                    and _live_keys == _current_live_keys
                    and len(_live_df) == len(_editor_df)
                )
                if _can_restore_live:
                    for _c in _edit_cols + ["Adjustment Reason"]:
                        if _c in _editor_df.columns and _c in _live_df.columns:
                            _editor_df[_c] = _live_df[_c]
                else:
                    st.session_state.pop(_editor_live_df_key, None)
                    st.session_state.pop(_editor_live_keys_key, None)
                _editor_df = _recompute_live_metrics(_editor_df)

                st.write("**Modify Plan Values for Future Months (Cells with orange background were modified):**")
                _currency_cols = _months + ["Estimated at Completion (EAC)", "Total Plan", "Δ vs Total Plan"]

                # Build column config: currency format for numeric cols, text for Adjustment Reason
                _col_config = {c: st.column_config.NumberColumn(format="€ %,.0f") for c in _currency_cols}
                _col_config["Adjustment Reason"] = st.column_config.TextColumn(
                    "Adjustment Reason", help="Reason for adjusting this task's plan values"
                )

                # Display editable dataframe
                _edited_df = st.data_editor(
                    _editor_df,
                    hide_index=True,
                    use_container_width=True,
                    disabled=_readonly_cols,  # Only plan months are editable
                    key=f"forecast_editor_{project_id}__v{st.session_state[_editor_version_key]}",
                    num_rows="fixed",
                    column_config=_col_config,
                )

                # Capture latest edit-state and trigger a lightweight rerun so computed columns refresh live.
                if _edited_df is not None:
                    _next_live_df = _recompute_live_metrics(_edited_df)
                    _prev_live_df = st.session_state.get(_editor_live_df_key)
                    _prev_live_keys = st.session_state.get(_editor_live_keys_key)
                    _has_changed = not (
                        isinstance(_prev_live_df, pd.DataFrame)
                        and _next_live_df.equals(_prev_live_df)
                        and isinstance(_prev_live_keys, list)
                        and _prev_live_keys == _current_live_keys
                    )
                    if _has_changed:
                        st.session_state[_editor_live_df_key] = _next_live_df
                        st.session_state[_editor_live_keys_key] = _current_live_keys
                        st.rerun()

                # Keep primary save action directly below the input table.
                _save_clicked = st.button("Save Plan Overrides", key=f"forecast_save_{project_id}")

                # Explicit cell-level revert controls for existing overrides
                _override_options = []
                for _idx in range(len(_pivot_df)):
                    _row_mcr = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()
                    if _row_mcr in {"", "TOTAL", _financial_risk_key}:
                        continue
                    _row_task_name = str(_pivot_df.iloc[_idx].get("Task Name", "") or "").strip()
                    _row_task_id = _pivot_df.iloc[_idx].get("__task_id")
                    if pd.isna(_row_task_id):
                        _row_task_id = -1
                    else:
                        _row_task_id = int(_row_task_id)
                    for _m in _edit_cols:
                        if (_row_mcr, _row_task_id, _m) in _override_map:
                            _label = f"{_row_mcr} | {_row_task_name or '(MCR Total)'} | {_m}"
                            _override_options.append((
                                _label,
                                (_row_mcr, _row_task_id, _m)
                            ))

                if _override_options:
                    _choice_map = {lbl: key for lbl, key in _override_options}
                    st.write("Revert one modified plan cell to default")
                    _revert_pick_col, _revert_btn_col = st.columns([6, 2])
                    with _revert_pick_col:
                        _selected_override_label = st.selectbox(
                            "Revert one modified plan cell to default",
                            options=list(_choice_map.keys()),
                            key=f"forecast_revert_pick_{project_id}",
                            label_visibility="collapsed",
                        )
                    with _revert_btn_col:
                        if st.button("Revert Selected Cell", key=f"forecast_revert_btn_{project_id}"):
                            _revert_mcr, _revert_task_id, _revert_month = _choice_map[_selected_override_label]
                            db_forecast_overrides.delete_forecast_override(
                                project_id, _revert_task_id, _revert_mcr, _revert_month
                            )
                            st.success("Selected cell reverted to default plan value.")
                            st.session_state[_editor_version_key] = st.session_state[_editor_version_key] + 1
                            st.session_state.pop(_editor_live_df_key, None)
                            st.session_state.pop(_editor_live_keys_key, None)
                            st.rerun()

                # ── Reason capture panel ──────────────────────────────────────────────
                # Build list of adjusted tasks (have at least one future-month override)
                _adjusted_task_keys = set()  # (mcr, task_id, task_name)
                _mcr_rows_with_explicit_total = set(
                    str(_r.get("__mcr_key") or "").strip()
                    for _, _r in _pivot_df.iterrows()
                    if str(_r.get("Task Name") or "").strip() == "(MCR Total)"
                )
                for (_ov_mcr, _ov_tid, _ov_month) in _override_map.keys():
                    if _ov_month >= _current_ym:
                        if _ov_tid == -1:
                            if _ov_mcr in _mcr_rows_with_explicit_total:
                                _adjusted_task_keys.add((_ov_mcr, -1, "(MCR Total)"))
                        else:
                            # Find task_name from pivot
                            for _, _pr in _pivot_df.iterrows():
                                _pr_tid = int(_pr.get("__task_id")) if pd.notna(_pr.get("__task_id")) else -1
                                if str(_pr.get("__mcr_key") or "").strip() == _ov_mcr and _pr_tid == _ov_tid:
                                    _tn_snap = str(_pr.get("Task Name") or "").strip().lstrip("↳").strip()
                                    _adjusted_task_keys.add((_ov_mcr, int(_ov_tid), _tn_snap))
                                    break

                if _adjusted_task_keys:
                    _reason_map_panel = dict(_reason_map)
                    if _edited_df is not None and "Adjustment Reason" in _edited_df.columns:
                        for _idx in range(len(_edited_df)):
                            _row_mcr_raw = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()
                            if _row_mcr_raw in {"", "Data Type", "TOTAL", _financial_risk_key}:
                                continue
                            _task_id_chk = _pivot_df.iloc[_idx].get("__task_id")
                            _tid_chk = int(_task_id_chk) if pd.notna(_task_id_chk) else -1
                            _edited_reason = str(_edited_df.iloc[_idx].get("Adjustment Reason", "") or "").strip()
                            _reason_map_panel[(_row_mcr_raw, _tid_chk)] = _edited_reason

                    def _panel_reason(_mcr, _tid):
                        _v = str(_reason_map_panel.get((_mcr, _tid), "") or "").strip()
                        if _v:
                            return _v
                        if _tid == -1:
                            return str(_reason_map_panel.get((_mcr, 0), "") or "").strip()
                        return ""

                    _reason_tasks_sorted = sorted(_adjusted_task_keys, key=lambda x: (x[0], x[2]))
                    _reason_task_labels = [
                        f"{_mcr} | {_tn}" for _mcr, _tid, _tn in _reason_tasks_sorted
                    ]
                    _reason_label_to_key = {
                        f"{_mcr} | {_tn}": (_mcr, _tid, _tn)
                        for _mcr, _tid, _tn in _reason_tasks_sorted
                    }

                    with st.expander("📝 Adjustment Reasons (required for all adjusted tasks)", expanded=True):
                        _reason_col1, _reason_col2 = st.columns([3, 5])
                        with _reason_col1:
                            _sel_reason_label = st.selectbox(
                                "Select adjusted task",
                                _reason_task_labels,
                                key=f"reason_task_sel_{project_id}",
                            )
                            _sel_reason_key = _reason_label_to_key[_sel_reason_label]
                            _sel_mcr, _sel_tid, _sel_tn = _sel_reason_key
                            _existing_reason = _panel_reason(_sel_mcr, _sel_tid)
                            _missing_reasons = [
                                f"{_mcr} | {_tn}"
                                for _mcr, _tid, _tn in _reason_tasks_sorted
                                if not _panel_reason(_mcr, _tid)
                            ]
                            if _missing_reasons:
                                st.warning(f"⚠️ Missing reason for {len(_missing_reasons)} task(s): " + ", ".join(_missing_reasons))
                            else:
                                st.success("✅ All adjusted tasks have reasons.")

                        with _reason_col2:
                            _reason_categories = ["", "Scope change", "Resource change", "Timeline shift", "Cost correction", "Risk provision", "Other"]
                            _existing_cat = ""
                            _existing_reason_rec = db_adjustment_reasons.get_reason(project_id, _sel_mcr, _sel_tid)
                            if _existing_reason_rec:
                                _existing_cat = _existing_reason_rec.get("reason_category", "")
                            _sel_category = st.selectbox(
                                "Category (optional)",
                                _reason_categories,
                                index=_reason_categories.index(_existing_cat) if _existing_cat in _reason_categories else 0,
                                key=f"reason_cat_{project_id}_{_sel_mcr}_{_sel_tid}",
                            )
                            _reason_input = st.text_area(
                                "Reason for adjustment",
                                value=_existing_reason,
                                height=80,
                                placeholder="Explain why the plan values for this task were adjusted...",
                                key=f"reason_text_{project_id}_{_sel_mcr}_{_sel_tid}",
                            )
                            _reason_btn_col1, _reason_btn_col2 = st.columns([2, 3])
                            with _reason_btn_col1:
                                if st.button("Save Reason", key=f"reason_save_{project_id}_{_sel_mcr}_{_sel_tid}"):
                                    if not _reason_input.strip():
                                        st.error("Reason text cannot be empty.")
                                    else:
                                        db_adjustment_reasons.upsert_reason(
                                            project_id, _sel_mcr, _sel_tid, _sel_tn,
                                            _reason_input.strip(), _sel_category,
                                        )
                                        st.success(f"Reason saved for {_sel_tn}.")
                                        st.rerun()
                            with _reason_btn_col2:
                                if _existing_reason and st.button("Clear Reason", key=f"reason_clear_{project_id}_{_sel_mcr}_{_sel_tid}"):
                                    db_adjustment_reasons.delete_reason(project_id, _sel_mcr, _sel_tid)
                                    st.info("Reason cleared.")
                                    st.rerun()

                # Detect changes and save to database only on explicit user action
                if _save_clicked and _edited_df is not None:
                    _pivot_df_to_check = _pivot_df.drop(columns=["__task_id", "__mcr_key"], errors="ignore")
                    _pivot_df_default = _pivot_df_original.drop(columns=["__task_id", "__mcr_key"], errors="ignore")
                    _changes_saved = 0
                    _changes_reverted = 0
                    _reasons_saved = 0
                    _reasons_deleted = 0

                    def _is_save_target_row(_idx):
                        _row_mcr_raw = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()
                        if _row_mcr_raw in {"", "Data Type", "TOTAL", _financial_risk_key}:
                            return False
                        _task_name_raw = str(_pivot_df.iloc[_idx].get("Task Name") or "").strip()
                        _task_id_raw = _pivot_df.iloc[_idx].get("__task_id")
                        # Skip computed expandable MCR aggregate rows (Task Name empty, no task_id).
                        if pd.isna(_task_id_raw) and _task_name_raw == "":
                            return False
                        return True

                    # Include inline grid edits for Adjustment Reason in validation context.
                    _reason_map_effective = dict(_reason_map)
                    if "Adjustment Reason" in _edited_df.columns:
                        for _idx in range(len(_edited_df)):
                            if not _is_save_target_row(_idx):
                                continue
                            _row_mcr_raw = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()
                            _task_id_chk = _pivot_df.iloc[_idx].get("__task_id")
                            _tid_chk = int(_task_id_chk) if pd.notna(_task_id_chk) else -1
                            _edited_reason = str(_edited_df.iloc[_idx].get("Adjustment Reason", "") or "").strip()
                            _reason_map_effective[(_row_mcr_raw, _tid_chk)] = _edited_reason

                    # Detect which tasks have new/changed cell edits in this save action
                    _newly_adjusted = {}  # (mcr, task_id) -> task_name
                    for _col in _edit_cols:
                        if _col in _edited_df.columns and _col in _pivot_df_to_check.columns:
                            for _idx in range(len(_edited_df)):
                                if not _is_save_target_row(_idx):
                                    continue
                                _row_mcr_raw = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()
                                _orig_val = float(pd.to_numeric(_pivot_df_to_check.iloc[_idx][_col], errors="coerce") or 0.0)
                                _edit_val = float(pd.to_numeric(_edited_df.iloc[_idx][_col], errors="coerce") or 0.0)
                                _default_val = float(pd.to_numeric(_pivot_df_default.iloc[_idx][_col], errors="coerce") or 0.0)
                                if abs(_orig_val - _edit_val) > 0.01 and abs(_edit_val - _default_val) > 0.01:
                                    _task_id_chk = _pivot_df.iloc[_idx].get("__task_id")
                                    _tid_chk = int(_task_id_chk) if pd.notna(_task_id_chk) else -1
                                    _tn_chk_raw = str(_pivot_df.iloc[_idx].get("Task Name") or "").strip().lstrip("↳").strip()
                                    _tn_chk = _tn_chk_raw or "(MCR Total)"
                                    _newly_adjusted[(_row_mcr_raw, _tid_chk)] = _tn_chk

                    # Validate: all newly-adjusted tasks must have a reason
                    _missing_on_save = [
                        f"{_mcr} | {_tn}"
                        for (_mcr, _tid), _tn in _newly_adjusted.items()
                        if not _reason_map_effective.get((_mcr, _tid), "").strip()
                    ]
                    if _missing_on_save:
                        st.error(
                            "⛔ Cannot save: the following adjusted tasks are missing a reason. "
                            "Please fill in reasons in the table or the Adjustment Reasons panel before saving:\n\n- "
                            + "\n- ".join(_missing_on_save)
                        )
                    else:
                        # Persist inline reason edits from grid.
                        if "Adjustment Reason" in _edited_df.columns:
                            for _idx in range(len(_edited_df)):
                                if not _is_save_target_row(_idx):
                                    continue
                                _row_mcr_raw = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()
                                _task_id_chk = _pivot_df.iloc[_idx].get("__task_id")
                                _tid_chk = int(_task_id_chk) if pd.notna(_task_id_chk) else -1
                                _task_name_chk_raw = str(_pivot_df.iloc[_idx].get("Task Name") or "").strip().lstrip("↳").strip()
                                _task_name_chk = _task_name_chk_raw or "(MCR Total)"

                                _edited_reason = str(_edited_df.iloc[_idx].get("Adjustment Reason", "") or "").strip()
                                _existing_reason = str(_reason_map.get((_row_mcr_raw, _tid_chk), "") or "").strip()
                                if _edited_reason == _existing_reason:
                                    continue

                                if _edited_reason:
                                    _existing_reason_rec = db_adjustment_reasons.get_reason(project_id, _row_mcr_raw, _tid_chk)
                                    _existing_cat = ""
                                    if _existing_reason_rec:
                                        _existing_cat = str(_existing_reason_rec.get("reason_category", "") or "")
                                    db_adjustment_reasons.upsert_reason(
                                        project_id,
                                        _row_mcr_raw,
                                        _tid_chk,
                                        _task_name_chk,
                                        _edited_reason,
                                        _existing_cat,
                                    )
                                    _reasons_saved += 1
                                else:
                                    db_adjustment_reasons.delete_reason(project_id, _row_mcr_raw, _tid_chk)
                                    _reasons_deleted += 1

                        for _col in _edit_cols:
                            if _col in _edited_df.columns and _col in _pivot_df_to_check.columns:
                                for _idx in range(len(_edited_df)):
                                    # Skip non-data rows.
                                    if not _is_save_target_row(_idx):
                                        continue
                                    _row_mcr_raw = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()

                                    _orig_val = float(pd.to_numeric(_pivot_df_to_check.iloc[_idx][_col], errors="coerce") or 0.0)
                                    _edit_val = float(pd.to_numeric(_edited_df.iloc[_idx][_col], errors="coerce") or 0.0)
                                    _default_val = float(pd.to_numeric(_pivot_df_default.iloc[_idx][_col], errors="coerce") or 0.0)
                                    if abs(_orig_val - _edit_val) > 0.01:  # Changed
                                        _row_mcr = _row_mcr_raw
                                        # Get task_id from original pivot_df (with __task_id column)
                                        _task_id = _pivot_df.iloc[_idx].get("__task_id")
                                        if pd.isna(_task_id):
                                            _task_id = -1
                                        else:
                                            _task_id = int(_task_id)

                                        # If edited value equals default plan, remove override (cell-level revert).
                                        if abs(_edit_val - _default_val) <= 0.01:
                                            db_forecast_overrides.delete_forecast_override(
                                                project_id, _task_id, _row_mcr, _col
                                            )
                                            _changes_reverted += 1
                                        else:
                                            db_forecast_overrides.upsert_forecast_override(
                                                project_id, _task_id, _row_mcr, _col, _edit_val
                                            )
                                            _changes_saved += 1

                        if _changes_saved or _changes_reverted or _reasons_saved or _reasons_deleted:
                            st.success(
                                f"Saved {_changes_saved} override(s), reverted {_changes_reverted} cell(s), "
                                f"saved {_reasons_saved} reason(s), deleted {_reasons_deleted} reason(s)."
                            )
                            st.session_state[_editor_version_key] = st.session_state[_editor_version_key] + 1
                            st.session_state.pop(_editor_live_df_key, None)
                            st.session_state.pop(_editor_live_keys_key, None)
                            st.rerun()
                        else:
                            st.info("No plan or reason changes detected to save.")

                # Read-only preview with color coding for modified plan cells.
                # Keep the exact same visible columns/order as the input table for UI alignment.
                _preview_df = _pivot_df.drop(columns=["__task_id", "__mcr_key"], errors="ignore").copy()
                _preview_df = _preview_df[_editor_df.columns]

                def _highlight_overrides(_df):
                    _styles = pd.DataFrame("", index=_df.index, columns=_df.columns)
                    for _idx in _df.index:
                        _row_mcr = str(_pivot_df.iloc[_idx].get("__mcr_key", "") or "").strip()
                        _row_task_id = _pivot_df.iloc[_idx].get("__task_id")
                        if pd.isna(_row_task_id):
                            _row_task_id = -1
                        else:
                            _row_task_id = int(_row_task_id)
                        for _m in _edit_cols:
                            if (_row_mcr, _row_task_id, _m) in _override_map:
                                _styles.loc[_idx, _m] = "background-color: #FFE6CC"
                    return _styles

                st.caption("Preview: orange cells are manually overridden values.")
                _styled_preview = _preview_df.style.apply(_highlight_overrides, axis=None)
                _styled_preview = _styled_preview.format(
                    {c: "€ {:,.0f}" for c in _currency_cols},
                    na_rep="€ 0",
                )
                st.dataframe(_styled_preview, hide_index=True, use_container_width=True)

    with st.expander("⚠️ 5.2 Financial risks", expanded=True):
        st.caption(
            "Register and manage project-level financial risks not yet planned in task-level budget. "
            "Residual (uncovered) risk is distributed monthly and included in section 5.1 as a dedicated row."
        )
        st.markdown(
            """
            <style>
            button[aria-label="Delete risk"],
            button[aria-label="Delete mapping"] {
                background-color: #c62828 !important;
                color: #ffffff !important;
                border: 1px solid #8e1b1b !important;
            }
            button[aria-label="Delete risk"]:hover,
            button[aria-label="Delete mapping"]:hover {
                background-color: #b71c1c !important;
                border-color: #7f1313 !important;
            }
            button[aria-label="Delete risk"]:focus,
            button[aria-label="Delete mapping"]:focus {
                box-shadow: 0 0 0 0.2rem rgba(198, 40, 40, 0.35) !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        try:
            _fr_start = datetime.date.fromisoformat(project.get("start_date") or "")
            _fr_end = datetime.date.fromisoformat(project.get("end_date") or "")
        except Exception:
            _fr_start = datetime.date.today().replace(day=1)
            _fr_end = _fr_start

        _fr_months = []
        _fr_cur = _fr_start.replace(day=1)
        while _fr_cur <= _fr_end.replace(day=1):
            _fr_months.append(_fr_cur.strftime("%Y-%m"))
            if _fr_cur.month == 12:
                _fr_cur = _fr_cur.replace(year=_fr_cur.year + 1, month=1)
            else:
                _fr_cur = _fr_cur.replace(month=_fr_cur.month + 1)

        def _fr_fmt_ym(_ym):
            _s = str(_ym or "").strip()
            if len(_s) == 7 and _s[4] == "-":
                try:
                    return datetime.datetime.strptime(_s, "%Y-%m").strftime("%b-%y")
                except Exception:
                    return _s
            return _s

        def _fr_parse_month_input(_txt):
            _v = str(_txt or "").strip()
            if not _v:
                return ""
            if len(_v) == 7 and _v[4] == "-":
                try:
                    datetime.datetime.strptime(_v, "%Y-%m")
                    return _v
                except Exception:
                    return ""
            for _fmt in ("%b-%y", "%B-%y"):
                try:
                    return datetime.datetime.strptime(_v, _fmt).strftime("%Y-%m")
                except Exception:
                    continue
            return ""

        _fr_rows_raw = db.list_financial_risks(project_id)
        _fr_map_rows = db.list_financial_risk_mappings(project_id)
        _fr_bundle = db.compute_project_financial_risk_monthly(project_id, _fr_months)
        if _fr_bundle.get("errors"):
            st.warning("Validation: " + " | ".join(_fr_bundle.get("errors", [])))

        st.markdown("**5.2.1 Risk register**")
        _risk_options = {"(new)": None}
        for _r in _fr_rows_raw:
            _risk_options[f"[{_r['id']}] {_r.get('risk_name') or ''}"] = int(_r["id"])
        _risk_pick_label = st.selectbox(
            "Select risk to edit",
            list(_risk_options.keys()),
            key=f"fr_risk_pick_{project_id}",
        )
        _risk_pick_id = _risk_options[_risk_pick_label]
        _risk_existing = next((r for r in _fr_rows_raw if int(r["id"]) == int(_risk_pick_id)), None) if _risk_pick_id else None

        with st.form(key=f"fr_risk_form_{project_id}"):
            _fr_c1, _fr_c2 = st.columns([2, 1])
            _risk_name = _fr_c1.text_input("Financial risk (name)", value=str((_risk_existing or {}).get("risk_name") or ""))
            _risk_amount = _fr_c2.number_input(
                "Total Initial Amount (€)",
                min_value=0.0,
                step=0.01,
                format="%.2f",
                value=float((_risk_existing or {}).get("total_initial_amount") or 0.0),
            )
            _risk_desc = st.text_area("Description", value=str((_risk_existing or {}).get("description") or ""), height=80)
            _risk_month_display = st.text_input(
                "Month identified (MMM-YY)",
                value=_fr_fmt_ym((_risk_existing or {}).get("month_identified_ym") or ""),
                placeholder="Jan-26",
            )

            _save_risk = st.form_submit_button("Save risk")
            if _save_risk:
                _errors = []
                _risk_name_s = str(_risk_name or "").strip()
                _risk_ym = _fr_parse_month_input(_risk_month_display)
                if not _risk_name_s:
                    _errors.append("Financial risk (name) is required.")
                if float(_risk_amount or 0.0) <= 0:
                    _errors.append("Total Initial Amount must be greater than 0.")
                if not _risk_ym:
                    _errors.append("Month identified must be a valid MMM-YY (example Jan-26).")
                elif _fr_months and _risk_ym > _fr_months[-1]:
                    _errors.append("Month identified cannot be after project end month.")
                if _errors:
                    st.error(" ".join(_errors))
                else:
                    if _risk_existing:
                        db.update_financial_risk(_risk_existing["id"], _risk_name_s, _risk_amount, _risk_desc, _risk_ym)
                        st.success("Financial risk updated.")
                    else:
                        db.create_financial_risk(project_id, _risk_name_s, _risk_amount, _risk_desc, _risk_ym)
                        st.success("Financial risk created.")
                    st.rerun()

        _fr_table_rows = []
        for _rr in _fr_bundle.get("risk_rows", []):
            _total_initial_amount = float(_rr.get("total_initial_amount") or 0.0)
            _remaining_amount = float(_rr.get("remaining_amount") or 0.0)
            _row = {
                "Financial risk": _rr.get("risk_name") or "",
                "Total Initial Amount": _total_initial_amount,
                "Month identified": _fr_fmt_ym(_rr.get("month_identified_ym") or ""),
                "Risk status": _rr.get("risk_status") or "",
                "Total covered amount": max(0.0, _total_initial_amount - _remaining_amount),
            }
            for _m in _fr_months:
                _row[_m] = float((_rr.get("monthly") or {}).get(_m, 0.0) or 0.0)
            _row["Remaining risk amount (Total)"] = _remaining_amount
            _fr_table_rows.append(_row)

        _show_fr_month_cols = st.toggle(
            "Show monthly columns in risk summary",
            value=False,
            key=f"fr_show_month_cols_{project_id}",
        )

        if _fr_table_rows:
            _fr_df = pd.DataFrame(_fr_table_rows)
            _fr_month_cols = [m for m in _fr_months if m in _fr_df.columns]
            _fr_base_cols = [
                "Financial risk",
                "Total Initial Amount",
                "Month identified",
                "Risk status",
                "Total covered amount",
                "Remaining risk amount (Total)",
            ]
            _fr_display_cols = _fr_base_cols + (_fr_month_cols if _show_fr_month_cols else [])
            _fr_df_view = _fr_df[_fr_display_cols].copy()
            _fr_currency_cols = ["Total Initial Amount", "Total covered amount", "Remaining risk amount (Total)"]
            if _show_fr_month_cols:
                _fr_currency_cols += _fr_month_cols

            _fr_total_row = {c: "" for c in _fr_display_cols}
            _fr_total_row["Financial risk"] = "TOTAL"
            for _c in _fr_currency_cols:
                _fr_total_row[_c] = _fr_df_view[_c].sum() if _c in _fr_df_view.columns else 0.0
            _fr_df_with_total = pd.concat([_fr_df_view, pd.DataFrame([_fr_total_row])], ignore_index=True)
            st.dataframe(
                _fr_df_with_total,
                hide_index=True,
                use_container_width=True,
                column_config={
                    c: st.column_config.NumberColumn(format="€ %,.2f")
                    for c in _fr_currency_cols
                },
            )
        else:
            st.info("No financial risks yet.")

        if _fr_rows_raw:
            _del_risk_options = {f"[{r['id']}] {r.get('risk_name') or ''}": int(r["id"]) for r in _fr_rows_raw}
            _dr_c1, _dr_c2 = st.columns([4, 1])
            _del_risk_pick = _dr_c1.selectbox("Delete risk", list(_del_risk_options.keys()), key=f"fr_del_risk_pick_{project_id}")
            _dr_c2.write("")
            _dr_c2.write("")
            if _dr_c2.button("Delete risk", key=f"fr_del_risk_btn_{project_id}"):
                db.delete_financial_risk(_del_risk_options[_del_risk_pick])
                st.success("Financial risk deleted.")
                st.rerun()

        st.markdown("**5.2.2 Coverage mappings (one risk to many MCR/Task lines)**")
        if not _fr_rows_raw:
            st.info("Create at least one financial risk first.")
        else:
            _fr_name_by_id = {int(r["id"]): str(r.get("risk_name") or "") for r in _fr_rows_raw}
            _map_risk_options = {f"[{r['id']}] {r.get('risk_name') or ''}": int(r["id"]) for r in _fr_rows_raw}
            _map_risk_label = st.selectbox("Risk for mapping", list(_map_risk_options.keys()), key=f"fr_map_risk_{project_id}")
            _map_risk_id = _map_risk_options[_map_risk_label]
            _map_risk_row = next((r for r in _fr_rows_raw if int(r["id"]) == int(_map_risk_id)), None)
            _map_existing = [m for m in _fr_map_rows if int(m.get("risk_id") or 0) == int(_map_risk_id)]

            with st.form(key=f"fr_map_form_{project_id}"):
                _fm_c1, _fm_c2, _fm_c3 = st.columns([2, 2, 1])
                _map_mcr = _fm_c1.text_input("Mapping to MCR Project No.", value="")
                _map_task = _fm_c2.text_input("Mapping to Task", value="")
                _map_amount = _fm_c3.number_input("Covered amount (€)", min_value=0.0, step=0.01, format="%.2f", value=0.0)
                _save_map = st.form_submit_button("Add mapping")
                if _save_map:
                    _errors = []
                    _mcr = str(_map_mcr or "").strip()
                    _task = str(_map_task or "").strip()
                    _covered = round(float(_map_amount or 0.0), 2)
                    if not _mcr:
                        _errors.append("Mapping to MCR Project No. is required.")
                    if not _task:
                        _errors.append("Mapping to Task is required.")
                    if _covered <= 0:
                        _errors.append("Covered amount must be greater than 0.")

                    _initial = round(float((_map_risk_row or {}).get("total_initial_amount") or 0.0), 2)
                    _already = round(sum(float(m.get("covered_amount") or 0.0) for m in _map_existing), 2)
                    if _already + _covered > _initial + 0.0001:
                        _errors.append("Total covered amount cannot exceed Total Initial Amount.")

                    if _errors:
                        st.error(" ".join(_errors))
                    else:
                        db.create_financial_risk_mapping(project_id, _map_risk_id, _mcr, _task, _covered)
                        st.success("Mapping added.")
                        st.rerun()

            st.markdown("All mappings in this project")
            if _fr_map_rows:
                _all_map_df = pd.DataFrame(
                    [
                        {
                            "ID": int(m.get("id") or 0),
                            "Financial risk": _fr_name_by_id.get(int(m.get("risk_id") or 0), ""),
                            "Mapped MCR": str(m.get("mapped_mcr") or ""),
                            "Mapped Task": str(m.get("mapped_task_name") or ""),
                            "Covered amount": float(m.get("covered_amount") or 0.0),
                        }
                        for m in _fr_map_rows
                    ]
                )
                _all_map_total = {"Financial risk": "TOTAL", "Mapped MCR": "", "Mapped Task": "", "Covered amount": _all_map_df["Covered amount"].sum()}
                _all_map_df_with_total = pd.concat([_all_map_df.drop(columns=["ID"]), pd.DataFrame([_all_map_total])], ignore_index=True)
                st.dataframe(
                    _all_map_df_with_total,
                    hide_index=True,
                    use_container_width=True,
                    column_config={"Covered amount": st.column_config.NumberColumn(format="€ %,.2f")},
                )
            else:
                st.info("No coverage mappings yet.")

            if _fr_map_rows:
                _del_map_options = {
                    f"[{int(m.get('id') or 0)}] {_fr_name_by_id.get(int(m.get('risk_id') or 0), '')} | {m.get('mapped_mcr') or ''} | {m.get('mapped_task_name') or ''}": int(m.get("id") or 0)
                    for m in _fr_map_rows
                }
                _dm_c1, _dm_c2 = st.columns([4, 1])
                _del_map_pick = _dm_c1.selectbox("Delete mapping", list(_del_map_options.keys()), key=f"fr_del_map_pick_{project_id}")
                _dm_c2.write("")
                _dm_c2.write("")
                if _dm_c2.button("Delete mapping", key=f"fr_del_map_btn_{project_id}"):
                    db.delete_financial_risk_mapping(_del_map_options[_del_map_pick])
                    st.success("Mapping deleted.")
                    st.rerun()

# ════════════════════════════════════════════════════════════════════════════
elif page == "🔗 Task Mappings":
    st.title("🔗 Task Name Mappings")
    st.caption(
        "Map actuals records to plan task names using both Task Name and MCR (Project No.) for precise control. "
        "Leave MCR blank to create a catch-all mapping for a task name across all MCRs."
    )

    import db_task_mappings

    project_id, project = select_project()
    if not project_id:
        st.stop()

    _plan_tasks = db.get_plan_tasks(project_id)
    _plan_task_names = sorted([str(t.get("task_name") or "").strip() for t in _plan_tasks if t.get("task_name")])

    if not _plan_task_names:
        st.warning("No plan tasks found for this project. Please create budget plan tasks first.")
        st.stop()

    _distinct_mcrs = db_task_mappings.get_distinct_actual_mcrs(project_id)
    _mcr_options_with_blank = [""] + _distinct_mcrs  # "" = catch-all

    # ── 1. Diagnostic Report ──────────────────────────────────────────────
    with st.expander("📊 1. Unmapped Actuals (Task + MCR combinations)", expanded=True):
        st.caption(
            "Shows (Task Name, MCR) combinations from actuals that are not yet mapped "
            "and don't directly match a plan task name."
        )
        _unmapped = db_task_mappings.get_unmapped_actuals_tasks(project_id)
        if not _unmapped:
            st.success("✓ All actuals records are either mapped or match plan tasks!")
        else:
            _unmapped_df = pd.DataFrame(_unmapped)
            _unmapped_df.rename(columns={
                "actual_task_name": "Actuals Task Name",
                "actual_mcr": "MCR (Project No.)",
                "record_count": "Records",
                "total_amount": "Total Amount (€)",
            }, inplace=True)
            _unmapped_df["Total Amount (€)"] = _unmapped_df["Total Amount (€)"].apply(lambda x: f"€ {x:,.2f}")
            st.dataframe(_unmapped_df, use_container_width=True, hide_index=True)
            st.info(f"{len(_unmapped)} unmapped combination(s) found. Map them below.")

    # ── 2. Create / Update Mapping ────────────────────────────────────────
    with st.expander("➕ 2. Create or Update Mapping", expanded=False):
        st.caption(
            "Specify the Actuals Task Name and optionally an MCR. "
            "An MCR-specific mapping takes priority over a catch-all mapping for the same task name."
        )
        _mc1, _mc2, _mc3 = st.columns([3, 2, 3])
        with _mc1:
            _actual_task = st.text_input(
                "Actuals Task Name",
                placeholder="e.g., C-Hub XC Series Care_004",
                key="mapping_actual_task",
            )
        with _mc2:
            _actual_mcr = st.selectbox(
                "MCR (blank = catch-all)",
                _mcr_options_with_blank,
                key="mapping_actual_mcr",
                format_func=lambda x: x if x else "(all MCRs — catch-all)",
            )
        with _mc3:
            _plan_task = st.selectbox(
                "Map to Plan Task Name",
                _plan_task_names,
                key="mapping_plan_task",
            )
        if st.button("Save Mapping", key="save_mapping_btn"):
            if _actual_task and _plan_task:
                db_task_mappings.upsert_task_mapping(project_id, _actual_task, _plan_task, actual_mcr=_actual_mcr)
                _scope = f"MCR '{_actual_mcr}'" if _actual_mcr else "all MCRs (catch-all)"
                st.success(f"✓ '{_actual_task}' [{_scope}] → '{_plan_task}'")
                st.rerun()
            else:
                st.error("Please fill in both Actuals Task Name and Plan Task Name.")

    # ── 3. View & Manage Existing Mappings ───────────────────────────────
    with st.expander("📋 3. View & Manage Existing Mappings", expanded=False):
        st.caption("All saved mappings. An empty MCR column means the mapping applies to all MCRs (catch-all).")
        _mappings = db_task_mappings.get_all_task_mappings(project_id)
        if not _mappings:
            st.info("No mappings defined yet.")
        else:
            _mappings_df = pd.DataFrame(_mappings)
            _mappings_df.rename(columns={
                "actual_task_name": "Actuals Task Name",
                "actual_mcr": "MCR (Project No.)",
                "plan_task_name": "Plan Task Name",
            }, inplace=True)
            st.dataframe(_mappings_df, use_container_width=True, hide_index=True)

            st.markdown("#### Delete a Mapping")
            _del_labels = [
                f"{m['actual_task_name']} | {m['actual_mcr'] or '(all MCRs)'}" for m in _mappings
            ]
            _del_idx = st.selectbox(
                "Select mapping to delete",
                range(len(_mappings)),
                format_func=lambda i: _del_labels[i],
                key="delete_mapping_select",
            )
            if st.button("Delete This Mapping", key="delete_mapping_btn"):
                _del_m = _mappings[_del_idx]
                db_task_mappings.delete_task_mapping(project_id, _del_m["actual_task_name"], _del_m["actual_mcr"])
                st.success(f"✓ Deleted mapping for '{_del_m['actual_task_name']}' | MCR: '{_del_m['actual_mcr'] or '(all)'}'")
                st.rerun()

    # ── 4. Quick Map from Diagnostic ─────────────────────────────────────
    with st.expander("⚡ 4. Quick Map from Diagnostic", expanded=False):
        st.caption(
            "Select an unmapped (Task, MCR) combination and map it in one click. "
            "The MCR is pre-filled from the diagnostic; clear it to create a catch-all instead."
        )
        if not _unmapped:
            st.info("No unmapped records.")
        else:
            _qm_labels = [
                f"{u['actual_task_name']} | {u['actual_mcr'] or '(no MCR)'} — €{u['total_amount']:,.0f}"
                for u in _unmapped
            ]
            _qm_idx = st.selectbox(
                "Select unmapped record",
                range(len(_unmapped)),
                format_func=lambda i: _qm_labels[i],
                key="quick_map_actual",
            )
            _qm = _unmapped[_qm_idx]
            _qm_c1, _qm_c2, _qm_c3 = st.columns([3, 2, 3])
            _qm_task = _qm_c1.text_input("Actuals Task Name", value=_qm["actual_task_name"], key="qm_task_inp")
            _qm_mcr_options = [""] + _distinct_mcrs
            _qm_mcr_default = _qm_mcr_options.index(_qm["actual_mcr"]) if _qm["actual_mcr"] in _qm_mcr_options else 0
            _qm_mcr = _qm_c2.selectbox(
                "MCR (blank = catch-all)",
                _qm_mcr_options,
                index=_qm_mcr_default,
                key="qm_mcr_sel",
                format_func=lambda x: x if x else "(all MCRs — catch-all)",
            )
            _qm_plan = _qm_c3.selectbox("Map to Plan Task", _plan_task_names, key="quick_map_plan")
            if st.button("Quick Map", key="quick_map_btn"):
                db_task_mappings.upsert_task_mapping(project_id, _qm_task, _qm_plan, actual_mcr=_qm_mcr)
                _scope = f"MCR '{_qm_mcr}'" if _qm_mcr else "all MCRs (catch-all)"
                st.success(f"✓ '{_qm_task}' [{_scope}] → '{_qm_plan}'")
                st.rerun()

# ════════════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════════════
# 7. EXPORT
# ════════════════════════════════════════════════════════════════════════════
elif page == "📤 Export":
    st.title("📤 7. Export to Excel")
    st.caption("Reference: 7.1 Export scope | 7.2 Master file export")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    st.markdown(f"7.1 Export all data for **{project['name']}** as an Excel workbook with multiple sheets.")

    if st.button("Generate Excel Report"):
        budget_items = db.get_budget_items(project_id)
        actuals = db.get_actuals(project_id)
        forecasts = db.get_forecasts(project_id)
        summary = db.get_project_summary(project_id)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            # Summary sheet
            df_sum = pd.DataFrame([{
                "Project": project["name"],
                "Status": project["status"],
                "Start": project["start_date"],
                "End": project["end_date"],
                "Total Budget (€)": summary["planned"],
                "Total Actuals (€)": summary["actual"],
                "ETC (€)": summary["etc"],
                "EAC (€)": summary["eac"],
                "Variance (€)": summary["variance"],
            }])
            df_sum.to_excel(writer, sheet_name="Summary", index=False)

            # Budget sheet
            if budget_items:
                pd.DataFrame(budget_items).drop(columns=["id", "project_id"]).to_excel(
                    writer, sheet_name="Budget Plan", index=False)

            # Actuals sheet
            if actuals:
                pd.DataFrame(actuals).drop(columns=["id", "project_id"]).to_excel(
                    writer, sheet_name="Actuals", index=False)

            # Forecast sheet
            if forecasts:
                pd.DataFrame(forecasts).drop(columns=["id", "project_id"]).to_excel(
                    writer, sheet_name="Forecast", index=False)

        output.seek(0)
        st.download_button(
            label="📥 Download Excel File",
            data=output.getvalue(),
            file_name=f"{project['name'].replace(' ', '_')}_budget_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.success("Report ready — click the button above to download.")

    # ── 7.2 Master file export ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("📊 **7.2 Master file export**")
    st.caption(
        "Task-level export combining Plan and Actuals by month. "
        "MCR Project No. and Task taken from Budget Planning (section 3)."
    )

    _exp_projects = {p["name"]: p for p in db.get_projects()}
    _exp_proj_name = st.selectbox(
        "Project",
        list(_exp_projects.keys()),
        key="export_master_project_sel",
    )
    _exp_proj = _exp_projects[_exp_proj_name]
    _exp_pid = _exp_proj["id"]

    if st.button("Generate Master Export", key="export_master_btn"):
        # ── load project date range & months ─────────────────────────────
        try:
            _exp_start = datetime.date.fromisoformat(_exp_proj["start_date"])
            _exp_end   = datetime.date.fromisoformat(_exp_proj["end_date"])
        except Exception:
            _exp_start = datetime.date.today().replace(day=1)
            _exp_end   = _exp_start.replace(year=_exp_start.year + 1)

        _exp_months = []
        _mc = _exp_start.replace(day=1)
        while _mc <= _exp_end.replace(day=1):
            _exp_months.append(_mc.strftime("%Y-%m"))
            _mc = _mc.replace(month=_mc.month + 1) if _mc.month < 12 else _mc.replace(year=_mc.year + 1, month=1)

        def _exp_month_label(ym):
            try:
                return datetime.date.fromisoformat(ym + "-01").strftime("%b %y")
            except Exception:
                return ym

        # ── load plan data ────────────────────────────────────────────────
        _exp_tasks = db.get_plan_tasks(_exp_pid)
        _exp_vmap  = db.get_plan_values_for_project(_exp_pid)
        _exp_tvmap = db.get_plan_text_values_for_project(_exp_pid)
        _exp_cols  = db.get_plan_columns(_exp_pid)

        # resolve MCR, RG-type and Organisation column keys
        _exp_mcr_col = None
        _exp_rg_type_col = None
        _exp_org_col = None
        # prefer 'custom_project_mcr' over 'prj_mcr_number' as it holds the canonical MCR value
        for _c in _exp_cols:
            _ck = (_c.get("col_key") or "").lower()
            _cl = (_c.get("col_label") or "").lower()
            if _ck == "custom_project_mcr":
                _exp_mcr_col = _c["col_key"]
                break
        if not _exp_mcr_col:
            for _c in _exp_cols:
                _ck = (_c.get("col_key") or "").lower()
                _cl = (_c.get("col_label") or "").lower()
                if "project mcr" in _cl or "prj mcr number" in _cl or "mcr" in _ck:
                    _exp_mcr_col = _c["col_key"]
                    break
        for _c in _exp_cols:
            _ck = (_c.get("col_key") or "").lower()
            _cl = (_c.get("col_label") or "").lower()
            if "sp_rg_type" in _ck or "rg type" in _cl or "sp rg type" in _cl:
                _exp_rg_type_col = _c["col_key"]
                break
        for _c in _exp_cols:
            _ck = (_c.get("col_key") or "").lower()
            _cl = (_c.get("col_label") or "").lower()
            if "custom_organisation" in _ck or _cl in ("organisation", "organization"):
                _exp_org_col = _c["col_key"]
                break

        def _get_text(tid, col):
            if not col:
                return ""
            v = str(_exp_tvmap.get((tid, col), "") or "").strip()
            if not v:
                v = str(_exp_vmap.get((tid, col), "") or "").strip()
            return v

        # build task → plan monthly dict
        _plan_rows = {}  # task_id → {month: amount}
        for _t in _exp_tasks:
            tid = int(_t["id"])
            _plan_rows[tid] = {m: float(_exp_vmap.get((tid, m), 0.0) or 0.0) for m in _exp_months}

        # ── load actuals & aggregate by (task_name, mcr, month) ──────────
        import db_task_mappings as _dtm
        _exp_actuals = db.get_actuals(_exp_pid)
        _exp_task_mappings = _dtm.get_all_task_mappings(_exp_pid)
        _exp_map_exact = {
            (m["actual_task_name"], m["actual_mcr"]): m["plan_task_name"]
            for m in _exp_task_mappings if m["actual_mcr"]
        }
        _exp_map_catch = {
            m["actual_task_name"]: m["plan_task_name"]
            for m in _exp_task_mappings if not m["actual_mcr"]
        }

        # build plan task name set for case-insensitive lookup
        _exp_plan_case_map = {}
        _exp_plan_case_map_by_rg = {}
        for _t in _exp_tasks:
            _tn = str(_t.get("task_name") or "").strip()
            _rg = str(_t.get("resource_group") or "").strip()
            if _tn:
                _exp_plan_case_map[_tn.lower()] = _tn
                _exp_plan_case_map_by_rg[(_tn.lower(), _rg.lower())] = _tn

        _xc_mcr_set = {"BM-00110021_004", "BM-00110021_005"}

        # actual amounts keyed by (mcr, resolved_task_name, resource_group, month)
        # plus MCR-level monthly totals used for XC aggregated export rows.
        _actual_by_task_month = {}
        _actual_by_mcr_month = {}
        for _a in _exp_actuals:
            _dt = pd.to_datetime(_a.get("date"), errors="coerce")
            if pd.isna(_dt):
                continue
            _ym = _dt.strftime("%Y-%m")
            if _ym not in _exp_months:
                continue
            _task_raw = str(_a.get("task_name") or "").strip()
            _rg_raw = str(_a.get("resource_group") or "").strip()
            _mcr_raw  = _canonical_project_mcr(_a.get("project_no")) or ""
            _mapped   = (
                _exp_map_exact.get((_task_raw, _mcr_raw))
                or _exp_map_catch.get(_task_raw)
                or _task_raw
            )
            _mapped = _exp_plan_case_map_by_rg.get((_mapped.lower(), _rg_raw.lower()), _exp_plan_case_map.get(_mapped.lower(), _mapped))
            _amt = float(_a.get("amount", 0.0) or 0.0)
            _actual_by_mcr_month[(_mcr_raw, _ym)] = _actual_by_mcr_month.get((_mcr_raw, _ym), 0.0) + _amt
            _key = (_mcr_raw, _mapped, _rg_raw, _ym)
            _actual_by_task_month[_key] = _actual_by_task_month.get(_key, 0.0) + _amt

        # plan monthly totals by MCR for XC aggregated export rows
        _plan_by_mcr_month = {}
        for _t in _exp_tasks:
            _tid = int(_t["id"])
            _mcr = _canonical_project_mcr(_get_text(_tid, _exp_mcr_col)) or ""
            for _m in _exp_months:
                _plan_by_mcr_month[(_mcr, _m)] = _plan_by_mcr_month.get((_mcr, _m), 0.0) + float(_plan_rows[_tid].get(_m, 0.0) or 0.0)

        # ── build export rows ─────────────────────────────────────────────
        _export_rows = []
        for _t in _exp_tasks:
            _tid  = int(_t["id"])
            _tname = str(_t.get("task_name") or "").strip()
            _mcr   = _canonical_project_mcr(_get_text(_tid, _exp_mcr_col)) or ""

            # For MCR 004/005 export only MCR-level aggregation (no task breakdown).
            if _mcr in _xc_mcr_set:
                continue

            _rg    = str(_t.get("resource_group") or "").strip()
            _rg_type = _get_text(_tid, _exp_rg_type_col)
            _org   = _get_text(_tid, _exp_org_col)

            _row = {
                "MCR Project No.": _mcr,
                "Organisation":    _org,
                "Task":            _tname,
                "Resource Group":  _rg,
                "RG Type":         _rg_type,
            }

            _plan_total = 0.0
            _actual_total = 0.0
            _plan_monthly = {}
            _actual_monthly = {}
            for _m in _exp_months:
                _lbl = _exp_month_label(_m)
                _pval = _plan_rows[_tid].get(_m, 0.0)
                _aval = _actual_by_task_month.get((_mcr, _tname, _rg, _m), 0.0)
                _plan_monthly[f"Plan {_lbl}"]   = _pval
                _actual_monthly[f"Actual {_lbl}"] = _aval
                _plan_total   += _pval
                _actual_total += _aval

            _row.update(_plan_monthly)
            _row.update(_actual_monthly)
            _row["Total Plan (€)"]         = _plan_total
            _row["Total Actuals YTD (€)"]  = _actual_total
            _export_rows.append(_row)

        # Append XC aggregated rows (MCR-level only) for BM-00110021_004/005.
        for _mcr in sorted(_xc_mcr_set):
            _row = {
                "MCR Project No.": _mcr,
                "Organisation": "XC",
                "Task": "(MCR aggregation - no task breakdown)",
                "Resource Group": "",
                "RG Type": "",
            }
            _plan_total = 0.0
            _actual_total = 0.0
            _has_data = False
            for _m in _exp_months:
                _lbl = _exp_month_label(_m)
                _pval = float(_plan_by_mcr_month.get((_mcr, _m), 0.0) or 0.0)
                _aval = float(_actual_by_mcr_month.get((_mcr, _m), 0.0) or 0.0)
                if abs(_pval) > 0.0 or abs(_aval) > 0.0:
                    _has_data = True
                _row[f"Plan {_lbl}"] = _pval
                _row[f"Actual {_lbl}"] = _aval
                _plan_total += _pval
                _actual_total += _aval

            if _has_data:
                _row["Total Plan (€)"] = _plan_total
                _row["Total Actuals YTD (€)"] = _actual_total
                _export_rows.append(_row)

        _master_df = pd.DataFrame(_export_rows)

        # ── write to Excel with formatting ────────────────────────────────
        _master_buf = BytesIO()
        with pd.ExcelWriter(_master_buf, engine="openpyxl") as _mew:
            _master_df.to_excel(_mew, sheet_name="Master Export", index=False)
            _ws = _mew.sheets["Master Export"]

            from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            _header_plan_fill   = PatternFill("solid", fgColor="D6E4F0")
            _header_actual_fill = PatternFill("solid", fgColor="D5E8D4")
            _header_total_fill  = PatternFill("solid", fgColor="FFF2CC")
            _bold = Font(bold=True)
            _thin = Side(style="thin")
            _border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

            _num_fmt = '#,##0.00'

            for _col_idx, _col_name in enumerate(_master_df.columns, start=1):
                _cell = _ws.cell(row=1, column=_col_idx)
                _cell.font = _bold
                _cell.alignment = Alignment(horizontal="center", wrap_text=True)
                _cell.border = _border
                _cn = str(_col_name)
                if _cn.startswith("Plan "):
                    _cell.fill = _header_plan_fill
                elif _cn.startswith("Actual "):
                    _cell.fill = _header_actual_fill
                elif "Total" in _cn:
                    _cell.fill = _header_total_fill

            # format numeric cells & auto-width
            _col_widths = {i: len(str(_master_df.columns[i - 1])) for i in range(1, len(_master_df.columns) + 1)}
            for _r_idx, _r in enumerate(_master_df.itertuples(index=False), start=2):
                for _c_idx, _val in enumerate(_r, start=1):
                    _cell = _ws.cell(row=_r_idx, column=_c_idx)
                    _cell.value = _val
                    _cell.border = _border
                    if isinstance(_val, float):
                        _cell.number_format = _num_fmt
                        _cell.alignment = Alignment(horizontal="right")
                    _col_widths[_c_idx] = max(_col_widths.get(_c_idx, 0), len(str(_val or "")))

            for _c_idx, _width in _col_widths.items():
                _ws.column_dimensions[get_column_letter(_c_idx)].width = min(max(_width + 2, 8), 30)

            _ws.freeze_panes = "E2"

        _master_buf.seek(0)
        _safe_name = _exp_proj["name"].replace(" ", "_")
        st.download_button(
            label="📥 Download Master Export",
            data=_master_buf.getvalue(),
            file_name=f"{_safe_name}_master_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.success(f"Master export ready — {len(_export_rows)} tasks, {len(_exp_months)} months.")
        st.dataframe(_master_df, use_container_width=True, hide_index=True)



