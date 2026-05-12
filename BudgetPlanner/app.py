"""
app.py - Budget Planner Main Application
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
    page_title="Budget Planner",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialise DB on first run
db.init_db()
db.ensure_default_import_profiles()

# ── Sidebar navigation ───────────────────────────────────────────────────────
st.sidebar.title("💰 Budget Planner")
st.sidebar.markdown("---")
page = st.sidebar.radio(
    "Navigate",
    ["🏠 Dashboard", "📁 Projects", "📋 Budget Planning", "📥 Actuals", "🔮 Forecast", "📊 Variance Report", "📤 Export"],
)

# Helper: project selector shown on most pages
def select_project(label="Select project"):
    projects = db.get_projects()
    if not projects:
        st.warning("No projects yet. Go to **📁 Projects** to create one.")
        return None, None
    names = [p["name"] for p in projects]
    chosen = st.selectbox(label, names)
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
                column_config[col] = st.column_config.NumberColumn(col, format="€ %,.2f")

    st.dataframe(display_df, use_container_width=True, hide_index=hide_index, column_config=column_config if column_config else None)

# ════════════════════════════════════════════════════════════════════════════
# 1. DASHBOARD
# ════════════════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.title("🏠 Dashboard")
    st.markdown("Overview by project and by month, with Labour vs Other split.")

    projects = db.get_projects()
    if not projects:
        st.info("No projects yet. Head to **📁 Projects** to get started.")
    else:
        rows = []
        for p in projects:
            s = db.get_project_summary(p["id"])
            rag = "🟢" if s["variance"] >= 0 else "🔴"
            rows.append({
                "RAG": rag,
                "Project": p["name"],
                "Status": p["status"],
                "Budget (€)": s["planned"],
                "Actuals (€)": s["actual"],
                "EAC (€)": s["eac"],
                "Variance (€)": s["variance"],
            })

        df = pd.DataFrame(rows)

        # KPI cards
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Projects", len(projects))
        col2.metric("Total Budget", f"€ {df['Budget (€)'].sum():,.0f}")
        col3.metric("Total Actuals", f"€ {df['Actuals (€)'].sum():,.0f}")
        variance_total = df["Variance (€)"].sum()
        col4.metric("Total Variance", f"€ {variance_total:,.0f}", delta=f"{variance_total:,.0f}")

        st.markdown("---")
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
        # Budget vs Actuals bar chart
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Budget", x=df["Project"], y=df["Budget (€)"], marker_color="#4C78A8"))
        fig.add_trace(go.Bar(name="Actuals", x=df["Project"], y=df["Actuals (€)"], marker_color="#F58518"))
        fig.add_trace(go.Bar(name="EAC", x=df["Project"], y=df["EAC (€)"], marker_color="#E45756"))
        fig.update_layout(barmode="group", title="Budget vs Actuals vs EAC", height=400)
        st.plotly_chart(fig, use_container_width=True)

        # ── Monthly view with Labour vs Other split ─────────────────────────
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

            st.markdown("**Monthly Actuals Table (€):**")
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

# ════════════════════════════════════════════════════════════════════════════
# 2. PROJECTS
# ════════════════════════════════════════════════════════════════════════════
elif page == "📁 Projects":
    st.title("📁 Projects")

    # ── Add new project ──
    with st.expander("➕ Add New Project", expanded=False):
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
            with st.expander(f"{p['name']}  |  {p['status']}  |  {p['start_date']} → {p['end_date']}"):
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
    st.title("📋 Budget Planning")
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
    if "plan_user" not in st.session_state:
        st.session_state["plan_user"] = ""
    _pu_col, _ = st.columns([2, 5])
    _plan_user_input = _pu_col.text_input(
        "👤 Your name (for audit trail)",
        value=st.session_state["plan_user"],
        key="plan_user_field",
        placeholder="Enter your name...",
    )
    if _plan_user_input.strip() != st.session_state["plan_user"]:
        st.session_state["plan_user"] = _plan_user_input.strip()
    current_user = st.session_state["plan_user"]
    if not current_user:
        st.warning("⚠️ Enter your name above — required for save and audit trail.")

    # ── Load custom columns ──────────────────────────────────────────────────
    _custom_cols_meta = db.get_plan_columns(project_id)
    _custom_col_keys  = [c["col_key"] for c in _custom_cols_meta]
    _custom_col_labels = {c["col_key"]: c["col_label"] for c in _custom_cols_meta}

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
    _all_value_keys = all_months + _custom_col_keys

    # ── Helper: load plan DataFrame from DB ──────────────────────────────────
    def _load_plan_df():
        tasks = db.get_plan_tasks(project_id)
        vmap  = db.get_plan_values_for_project(project_id)
        base_cols = ["_task_id", "Task Name", "Cost Type", "_calc_mode", "Resource Group", "Employees"]
        if not tasks:
            return pd.DataFrame(columns=base_cols + _all_value_keys + ["Total (€)"])
        records = []
        for t in tasks:
            _is_labor = (t["cost_type"] or "").strip().lower() == "labor cost"
            _rate_val = float(vmap.get((t["id"], _rate_col_key), 0.0) or 0.0) if _rate_col_key else 0.0
            _hours_val = float(vmap.get((t["id"], _hours_col_key), 0.0) or 0.0) if _hours_col_key else 0.0
            _default_mode = "Calculate" if (_is_labor and _rate_val > 0 and _hours_val > 0) else "Direct"
            row = {
                "_task_id": int(t["id"]),
                "Task Name": t["task_name"] or "",
                "Cost Type": t["cost_type"] or "Direct cost",
                "_calc_mode": _default_mode,
                "Resource Group": t["resource_group"] or "",
                "Employees": t["employees"] or "",
            }
            for ck in _all_value_keys:
                _val = vmap.get((t["id"], ck), 0.0)
                row[ck] = float(_val if _val is not None else 0.0)
            # Total (€) reflects annual budget as the sum of monthly allocations only.
            row["Total (€)"] = sum(row[ck] for ck in all_months)
            records.append(row)
        return pd.DataFrame(records)

    plan_df = _load_plan_df()

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab_plan, tab_import, tab_baselines, tab_columns, tab_audit = st.tabs([
        "📋 Plan", "📥 Import from Excel", "🧊 Baselines", "⚙️ Columns", "📜 Audit Log"
    ])

    # ════════════════════════════════════════════════════════════════════════
    # TAB: Budget Plan
    # ════════════════════════════════════════════════════════════════════════
    with tab_plan:
        # Summary + export
        if not plan_df.empty:
            _total_plan = plan_df["Total (€)"].sum()
            _h1, _h2 = st.columns([3, 1])
            _h1.metric("Total Plan", f"€ {_total_plan:,.2f}")
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
            "Total (€)": st.column_config.NumberColumn("Total (€)", format="€ %,.2f", disabled=True, width="medium"),
        }
        for _ck in all_months:
            _col_conf[_ck] = st.column_config.NumberColumn(
                _month_label(_ck), format="€ %,.2f", min_value=0.0, step=100.0
            )
        for _ck in _custom_col_keys:
            _label = _custom_col_labels.get(_ck, _ck)
            if _ck == _rate_col_key:
                _col_conf[_ck] = st.column_config.NumberColumn(
                    _label, format="€ %,.2f", min_value=0.0, step=1.0
                )
            elif _ck == _hours_col_key:
                _col_conf[_ck] = st.column_config.NumberColumn(
                    _label, format="%.2f", min_value=0.0, step=1.0
                )
            else:
                _col_conf[_ck] = st.column_config.NumberColumn(
                    _label, format="€ %,.2f", min_value=0.0, step=100.0
                )
        
        if _rate_col_key and _hours_col_key:
            _col_conf["_calc_mode"] = st.column_config.SelectboxColumn(
                "Auto-Calc Mode (Labor)",
                options=["Calculate", "Direct"],
                width="medium",
                help="Calculate: Rate×Hours→Total. Direct: enter Total via months or direct edit."
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
            db.get_plan_user_preference(project_id, current_user, _plan_col_pref_key, default=[])
            if current_user else []
        )
        _col_order_state_key = f"plan_editor_col_order__{project_id}__{current_user or 'anon'}"
        if _col_order_state_key not in st.session_state:
            st.session_state[_col_order_state_key] = _normalize_editor_col_order(_saved_editor_col_order)
        else:
            st.session_state[_col_order_state_key] = _normalize_editor_col_order(
                st.session_state[_col_order_state_key]
            )

        _editor_state = st.session_state.get("plan_data_editor", {})
        if isinstance(_editor_state, dict):
            _state_col_order = _editor_state.get("column_order")
            if isinstance(_state_col_order, list) and _state_col_order:
                st.session_state[_col_order_state_key] = _normalize_editor_col_order(_state_col_order)

        _active_editor_col_order = st.session_state[_col_order_state_key]

        st.caption(
            "✏️ Edit task metadata and monthly values directly in the table. "
            "Add rows at the bottom (**+**). Delete rows with the row-delete icon (✕ on hover). "
            "Click **💾 Save Changes** to persist."
        )

        with st.expander("🧭 Column Order", expanded=False):
            st.caption("Reorder columns left/right and save this order for your user in this project.")
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
                    st.session_state.pop("plan_data_editor", None)
                    st.rerun()
            if _co3.button("💾 Save Order", key=f"{_col_order_state_key}__save"):
                if current_user:
                    db.set_plan_user_preference(
                        project_id, current_user, _plan_col_pref_key, _active_editor_col_order
                    )
                    st.success("Column order saved for your user.")
                else:
                    st.warning("Enter your name above to save user preferences.")
            if _co4.button("↩️ Reset", key=f"{_col_order_state_key}__reset"):
                st.session_state[_col_order_state_key] = _normalize_editor_col_order([])
                if current_user:
                    db.set_plan_user_preference(
                        project_id, current_user, _plan_col_pref_key, st.session_state[_col_order_state_key]
                    )
                st.session_state.pop("plan_data_editor", None)
                st.rerun()

        if _auto_rate_hours_total:
            _has_labor = not plan_df.empty and any(str(ct).strip().lower() == "labor cost" for ct in plan_df.get("Cost Type", []))
            if _has_labor:
                st.info(
                    "💡 **Auto-Calc Modes** (for Labor cost tasks only):\n"
                    "- **Calculate**: Rate/Hour × Hours(Annual) → Total (auto-distributed to months)\n"
                    "- **Direct**: Set Total directly; edit monthly values to recalc Total as SUM\n"
                    "Select mode in the '_Auto-Calc Mode (Labor)' column."
                )
        _disabled_cols = ["_task_id", "Total (€)"]
        
        edited_plan = st.data_editor(
            plan_df,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_order=_active_editor_col_order,
            disabled=_disabled_cols,
            column_config=_col_conf,
            key="plan_data_editor",
        )

        _sv_col, _ = st.columns([1, 5])
        if _sv_col.button("💾 Save Changes", type="primary", key="save_plan_btn"):
            if not current_user:
                st.error("⚠️ Please enter your name (for audit trail) before saving.")
            else:
                _added = _deleted = _meta_upd = _val_upd = 0

                _save_df = edited_plan.copy()

                def _num_or_zero(v):
                    try:
                        if pd.isna(v):
                            return 0.0
                        return float(v)
                    except Exception:
                        return 0.0

                # Auto-calculate based on mode and cost type.
                if _auto_rate_hours_total and all_months:
                    for _idx, _r in _save_df.iterrows():
                        _is_labor = str(_r.get("Cost Type", "")).strip().lower() == "labor cost"
                        _calc_mode = str(_r.get("_calc_mode", "Direct")).strip()
                        
                        if _is_labor and _calc_mode == "Calculate":
                            # Mode 1: Calculate from Rate × Hours
                            _rate_val = _num_or_zero(_r.get(_rate_col_key, 0.0))
                            _hours_val = _num_or_zero(_r.get(_hours_col_key, 0.0))
                            if _rate_val > 0 and _hours_val > 0:
                                _annual_total = _rate_val * _hours_val
                                if _total_col_key:
                                    _save_df.at[_idx, _total_col_key] = _annual_total
                                _per_month = _annual_total / len(all_months)
                                for _m in all_months:
                                    _save_df.at[_idx, _m] = _per_month
                        elif _is_labor and _calc_mode == "Direct":
                            # Mode 2: Calculate Total as sum of monthly values
                            _month_sum = sum(_num_or_zero(_r.get(_m, 0.0)) for _m in all_months)
                            if _month_sum > 0 and _total_col_key:
                                _save_df.at[_idx, _total_col_key] = _month_sum

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

                for _, _row in _save_df.iterrows():
                    _raw_id = _row.get("_task_id")
                    try:
                        _tid = int(float(_raw_id)) if (_raw_id is not None and not pd.isna(_raw_id)) else 0
                    except (ValueError, TypeError):
                        _tid = 0

                    _tname = str(_row.get("Task Name") or "").strip()
                    _ctype = str(_row.get("Cost Type") or "Direct cost").strip() or "Direct cost"
                    _calc_mo = str(_row.get("_calc_mode", "Direct")).strip()
                    _rg    = str(_row.get("Resource Group") or "").strip()
                    _emps  = str(_row.get("Employees") or "").strip()

                    if not _tname:
                        continue  # skip blank rows

                    if _tid == 0 or _tid not in _orig_ids:
                        # New task
                        _new_tid = db.add_plan_task(project_id, _tname, _ctype, _rg, _emps)
                        db.add_plan_audit(project_id, _new_tid, _tname, "", "", "", "add_task", current_user)
                        _added += 1
                        for _ck in _all_value_keys:
                            _val = float(_row.get(_ck) or 0)
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
                            _old_mo = str(_orow.get("_calc_mode", "Direct")).strip()
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
                                _meta_changed = True
                            if _meta_changed:
                                db.update_plan_task(_tid, _tname, _ctype, _rg, _emps)
                                _meta_upd += 1

                            for _ck in _all_value_keys:
                                _nv = float(_row.get(_ck) or 0)
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
                db.set_plan_user_preference(
                    project_id,
                    current_user,
                    _plan_col_pref_key,
                    st.session_state.get(_col_order_state_key, _active_editor_col_order),
                )
                st.rerun()

        # ── Add Task form ────────────────────────────────────────────────────
        with st.expander("➕ Add New Task", expanded=plan_df.empty):
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
            with st.expander("📅 Enter Annual Total (distribute equally across months)"):
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
                _d_task   = guess_column(df_imp.columns, ["task", "task name", "description", "work package", "wbs"])
                _d_ct     = guess_column(df_imp.columns, ["cost type", "type", "cost_type", "category"])
                _d_rg     = guess_column(df_imp.columns, ["resource group", "resource_group", "rg", "team", "department"])
                _d_emp    = guess_column(df_imp.columns, ["employee", "employees", "resource", "name"])

                st.markdown("**Map metadata columns:**")
                _mc1, _mc2, _mc3, _mc4 = st.columns(4)
                _imp_task_col = _mc1.selectbox("Task Name *", _imp_cols, index=_imp_cols.index(_d_task) if _d_task in _imp_cols else 0, key="imp_task_col")
                _imp_type_col = _mc2.selectbox("Cost Type", _imp_cols, index=_imp_cols.index(_d_ct) if _d_ct in _imp_cols else 0, key="imp_type_col")
                _imp_rg_col   = _mc3.selectbox("Resource Group", _imp_cols, index=_imp_cols.index(_d_rg) if _d_rg in _imp_cols else 0, key="imp_rg_col")
                _imp_emp_col  = _mc4.selectbox("Employees", _imp_cols, index=_imp_cols.index(_d_emp) if _d_emp in _imp_cols else 0, key="imp_emp_col")
                _mc5, _mc6, _mc7 = st.columns(3)
                _imp_hours_col = _mc5.selectbox("Hours (annually)", _imp_cols, index=0, key="imp_hours_col")
                _imp_rate_col  = _mc6.selectbox("Rate / hour (€)", _imp_cols, index=0, key="imp_rate_col")
                _imp_total_col = _mc7.selectbox("Total (€)", _imp_cols, index=0, key="imp_total_col")

                _helper_custom_keys = {"hours_annually", "rate_per_hour", "total"}
                _helper_aliases = {
                    "rate / hour", "rate per hour", "hourly rate", "rate hour", "rate",
                    "hours (annually)", "hours annually", "annual hours", "hours annual", "hours year", "yearly hours",
                    "total", "total annual", "annual total", "total annual cost", "annual cost",
                }
                _helper_alias_norm = {_norm_label(v) for v in _helper_aliases}
                _extra_custom_cols = [
                    c for c in _custom_cols_meta
                    if c["col_key"] not in _helper_custom_keys
                    and _norm_label(c.get("col_label") or c["col_key"]) not in _helper_alias_norm
                ]
                _extra_custom_sel = {}
                if _extra_custom_cols:
                    st.markdown("**Map additional custom columns:**")
                    _extra_per_row = 3
                    for _i, _cc in enumerate(_extra_custom_cols):
                        if _i % _extra_per_row == 0:
                            _crow = st.columns(_extra_per_row)
                        _cidx = _i % _extra_per_row
                        _cc_label = _cc.get("col_label") or _cc["col_key"]
                        _default_col = guess_column(
                            df_imp.columns,
                            [_cc_label, _cc["col_key"], _cc_label.replace("_", " ")],
                        )
                        _extra_custom_sel[_cc["col_key"]] = _crow[_cidx].selectbox(
                            _cc_label,
                            _imp_cols,
                            index=_imp_cols.index(_default_col) if _default_col in _imp_cols else 0,
                            key=f"imp_custom_{_cc['col_key']}",
                        )

                _meta_used = {
                    c
                    for c in [_imp_task_col, _imp_type_col, _imp_rg_col, _imp_emp_col, _imp_hours_col, _imp_rate_col, _imp_total_col]
                    if c != "(none)"
                }
                _meta_used.update(v for v in _extra_custom_sel.values() if v != "(none)")
                _remaining = [c for c in df_imp.columns if c not in _meta_used]

                # Auto-detect month columns
                def _try_parse_ym(col_name):
                    try:
                        return pd.to_datetime(str(col_name).strip()).strftime("%Y-%m")
                    except Exception:
                        return None

                _auto_map = {}
                for _c in _remaining:
                    _ym = _try_parse_ym(_c)
                    if _ym and _ym in all_months:
                        _auto_map[_ym] = _c

                st.markdown("**Map value columns to plan months:**")
                st.caption("Auto-detected where possible. Leave as (none) to skip a month.")
                _val_cols = ["(none)"] + _remaining
                _month_sel = {}
                _mcols_per_row = 6
                for _i, _ym in enumerate(all_months):
                    if _i % _mcols_per_row == 0:
                        _mrow = st.columns(_mcols_per_row)
                    _def = _auto_map.get(_ym, "(none)")
                    _month_sel[_ym] = _mrow[_i % _mcols_per_row].selectbox(
                        _month_label(_ym), _val_cols,
                        index=_val_cols.index(_def) if _def in _val_cols else 0,
                        key=f"imp_month_{_ym}",
                    )

                _def_ctype = st.selectbox(
                    "Default Cost Type (when column is empty/missing)",
                    ["Direct cost", "Labor cost"],
                    key="imp_default_ct",
                )

                if st.button("⬇️ Import to Plan", key="do_plan_import"):
                    if not current_user:
                        st.error("Please set your name for the audit trail.")
                    elif _imp_task_col == "(none)":
                        st.error("Task Name column is required.")
                    else:
                        # Ensure custom columns exist for Hours, Rate, Total
                        _hours_key = "hours_annually"
                        _rate_key = "rate_per_hour"
                        _total_key = "total"
                        if _imp_hours_col != "(none)":
                            db.add_plan_column(project_id, _hours_key, "Hours (annually)", col_type="custom", sort_order=60)
                        if _imp_rate_col != "(none)":
                            db.add_plan_column(project_id, _rate_key, "Rate / hour", col_type="custom", sort_order=59)
                        if _imp_total_col != "(none)":
                            db.add_plan_column(project_id, _total_key, "Total", col_type="custom", sort_order=61)
                        _imp_tasks = _imp_vals = _imp_skip = 0
                        for _, _ir in df_imp.iterrows():
                            _tval = str(_ir[_imp_task_col]).strip() if _imp_task_col != "(none)" else ""
                            if not _tval or _tval.lower() in ("nan", "none", ""):
                                _imp_skip += 1
                                continue
                            _ctval = str(_ir[_imp_type_col]).strip() if _imp_type_col != "(none)" else _def_ctype
                            if _ctval.lower() not in ("labor cost", "direct cost"):
                                _ctval = _def_ctype
                            _rgval  = "" if _imp_rg_col  == "(none)" else str(_ir[_imp_rg_col]).strip()
                            _empval = "" if _imp_emp_col == "(none)" else str(_ir[_imp_emp_col]).strip()
                            _rgval  = "" if _rgval.lower()  in ("nan", "none") else _rgval
                            _empval = "" if _empval.lower() in ("nan", "none") else _empval

                            _new_tid = db.add_plan_task(project_id, _tval, _ctval, _rgval, _empval)
                            db.add_plan_audit(project_id, _new_tid, _tval, "", "", "", "import", current_user)
                            _imp_tasks += 1

                            # Import Hours, Rate, Total if mapped
                            if _imp_hours_col != "(none)" and _imp_hours_col in df_imp.columns:
                                _hours_val = parse_amount(_ir[_imp_hours_col])
                                if _hours_val is not None and _hours_val != 0:
                                    db.upsert_plan_value(_new_tid, _hours_key, _hours_val, current_user)
                                    db.add_plan_audit(project_id, _new_tid, _tval, _hours_key, 0, _hours_val, "import", current_user)
                                    _imp_vals += 1
                            if _imp_rate_col != "(none)" and _imp_rate_col in df_imp.columns:
                                _rate_val = parse_amount(_ir[_imp_rate_col])
                                if _rate_val is not None and _rate_val != 0:
                                    db.upsert_plan_value(_new_tid, _rate_key, _rate_val, current_user)
                                    db.add_plan_audit(project_id, _new_tid, _tval, _rate_key, 0, _rate_val, "import", current_user)
                                    _imp_vals += 1
                            if _imp_total_col != "(none)" and _imp_total_col in df_imp.columns:
                                _total_val = parse_amount(_ir[_imp_total_col])
                                if _total_val is not None and _total_val != 0:
                                    db.upsert_plan_value(_new_tid, _total_key, _total_val, current_user)
                                    db.add_plan_audit(project_id, _new_tid, _tval, _total_key, 0, _total_val, "import", current_user)
                                    _imp_vals += 1
                                    # Distribute total equally across months if no month columns are mapped
                                    _any_month_mapped = any(v != "(none)" for v in _month_sel.values())
                                    if not _any_month_mapped and all_months:
                                        _per_month = _total_val / len(all_months)
                                        for _ym in all_months:
                                            db.upsert_plan_value(_new_tid, _ym, _per_month, current_user)
                                            db.add_plan_audit(project_id, _new_tid, _tval, _ym, 0, _per_month, "import", current_user)
                                            _imp_vals += 1

                            for _ck, _src_col in _extra_custom_sel.items():
                                if _src_col != "(none)" and _src_col in df_imp.columns:
                                    _cv = parse_amount(_ir[_src_col])
                                    if _cv is not None and _cv != 0:
                                        db.upsert_plan_value(_new_tid, _ck, _cv, current_user)
                                        db.add_plan_audit(project_id, _new_tid, _tval, _ck, 0, _cv, "import", current_user)
                                        _imp_vals += 1

                            for _ym, _src in _month_sel.items():
                                if _src != "(none)" and _src in df_imp.columns:
                                    _amt = parse_amount(_ir[_src])
                                    if _amt is not None and _amt != 0:
                                        db.upsert_plan_value(_new_tid, _ym, _amt, current_user)
                                        db.add_plan_audit(
                                            project_id, _new_tid, _tval, _ym, 0, _amt, "import", current_user
                                        )
                                        _imp_vals += 1

                        st.success(f"Imported {_imp_tasks} tasks with {_imp_vals} monthly values.")
                        if _imp_skip:
                            st.warning(f"Skipped {_imp_skip} empty rows.")
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
        with st.expander("🧊 Create / Update Baseline", expanded=True):
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
            st.markdown(f"**Existing baselines ({len(_baselines)}):**")
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
        st.markdown(f"**Month columns:** {', '.join(f'`{_month_label(m)}`' for m in all_months)}")
        st.markdown("---")
        st.markdown("**Custom columns:**")
        with st.form("add_col_form"):
            _cc1, _cc2 = st.columns(2)
            _new_cl = _cc1.text_input("Column Label *", placeholder="e.g. Headcount FTE")
            _new_ck = _cc2.text_input("Column key (auto if blank)", placeholder="e.g. headcount_fte")
            if st.form_submit_button("➕ Add Column"):
                if not _new_cl.strip():
                    st.error("Column label is required.")
                else:
                    _ck_auto = default_plan_column_key(_new_cl.strip(), _new_ck.strip())
                    db.add_plan_column(project_id, _ck_auto, _new_cl.strip())
                    st.success(f"Added column: **{_new_cl.strip()}**")
                    st.rerun()

        if _custom_cols_meta:
            for _cc in _custom_cols_meta:
                _cca, _ccb = st.columns([4, 1])
                _cca.write(f"**{_cc['col_label']}** (key: `{_cc['col_key']}`")
                if _ccb.button("🗑️ Delete", key=f"del_col_{_cc['id']}"):
                    db.delete_plan_column(_cc["id"])
                    st.success(f"Deleted column: {_cc['col_label']}")
                    st.rerun()
        else:
            st.info("No custom columns added yet.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB: Audit Log
    # ════════════════════════════════════════════════════════════════════════
    with tab_audit:
        st.markdown("Full history of all plan changes — who changed what and when.")
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
    st.title("📥 Actuals Entry")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    tab1, tab2, tab3, tab4 = st.tabs(["✏️ Manual Entry", "📂 Import from CSV/Excel (MCR)", "🗺️ Mapping", "👀 View Actuals"])

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
            "💳 Other Cost Actuals Import",
            "👷 Employee (Labour) Actuals Import",
        ])

        with import_cost_tab:
            st.markdown("Upload non-labour cost actuals file (materials, travel, services, software, etc.).")
            uploaded_cost = st.file_uploader("Upload cost actuals file", type=["csv", "xlsx", "xls"], key="cost_actuals_upload")
            if uploaded_cost:
                try:
                    df_up = pd.read_csv(uploaded_cost) if uploaded_cost.name.lower().endswith(".csv") else pd.read_excel(uploaded_cost)
                    st.write("Preview (first 5 rows):", df_up.head())

                    cols = ["(none)"] + list(df_up.columns)
                    default_category = guess_column(df_up.columns, ["category", "cost type", "cost_category", "cost element", "expense type"])
                    default_amount = guess_column(df_up.columns, ["amount", "cost", "value", "actuals", "actual amount", "booked amount"])
                    default_date = guess_column(df_up.columns, ["date", "booking date", "posting date", "document date", "period"])
                    default_desc = guess_column(df_up.columns, ["description", "task", "text", "comment", "note", "item"])

                    profiles = db.get_import_profiles("actuals_cost")
                    profile_names = [p["profile_name"] for p in profiles]
                    selected_profile = st.selectbox("Cost import profile", ["(none)"] + profile_names, key="cost_profile_select")
                    selected_settings = next((p["settings"] for p in profiles if p["profile_name"] == selected_profile), {}) if selected_profile != "(none)" else {}

                    mc1, mc2, mc3, mc4 = st.columns(4)
                    col_map = {}
                    col_map["category"] = mc1.selectbox("Category -> App Category", cols, index=cols.index(selected_settings.get("col_map", {}).get("category", default_category)) if selected_settings.get("col_map", {}).get("category", default_category) in cols else 0, key="cost_map_cat")
                    col_map["amount"] = mc2.selectbox("Amount -> App Amount", cols, index=cols.index(selected_settings.get("col_map", {}).get("amount", default_amount)) if selected_settings.get("col_map", {}).get("amount", default_amount) in cols else 0, key="cost_map_amt")
                    col_map["date"] = mc3.selectbox("Date -> App Date", cols, index=cols.index(selected_settings.get("col_map", {}).get("date", default_date)) if selected_settings.get("col_map", {}).get("date", default_date) in cols else 0, key="cost_map_date")
                    col_map["description"] = mc4.selectbox("Description -> App Description", cols, index=cols.index(selected_settings.get("col_map", {}).get("description", default_desc)) if selected_settings.get("col_map", {}).get("description", default_desc) in cols else 0, key="cost_map_desc")

                    oc1, oc2 = st.columns(2)
                    default_category_if_missing = oc1.selectbox("Default category if missing", CATEGORIES, index=CATEGORIES.index(selected_settings.get("default_category_if_missing", "Other")) if selected_settings.get("default_category_if_missing", "Other") in CATEGORIES else CATEGORIES.index("Other"), key="cost_default_cat")
                    amount_mode = oc2.selectbox("Amount sign handling", ["Keep as in file", "Convert to positive (absolute)", "Invert sign"], index=["Keep as in file", "Convert to positive (absolute)", "Invert sign"].index(selected_settings.get("amount_mode", "Keep as in file")) if selected_settings.get("amount_mode", "Keep as in file") in ["Keep as in file", "Convert to positive (absolute)", "Invert sign"] else 0, key="cost_amt_mode")

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
                        cat = normalize_category(row[col_map["category"]]) if col_map["category"] != "(none)" else default_category_if_missing
                        amt = parse_amount(row[col_map["amount"]]) if col_map["amount"] != "(none)" else None
                        if amt is not None:
                            if amount_mode == "Convert to positive (absolute)":
                                amt = abs(amt)
                            elif amount_mode == "Invert sign":
                                amt = -amt
                        dt = parse_date(row[col_map["date"]]) if col_map["date"] != "(none)" else str(datetime.date.today())
                        desc = str(row[col_map["description"]]).strip() if col_map["description"] != "(none)" else ""
                        if desc.lower() == "nan":
                            desc = ""
                        preview_rows.append({"Date": dt, "Category": cat, "Description": desc, "Amount (€)": amt})

                    edited_preview = st.data_editor(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True, num_rows="dynamic", key="cost_import_preview")
                    import_only_positive = st.checkbox("Import only positive amounts", value=True, key="cost_positive_only")

                    if st.button("Import Cost Preview Rows", key="import_cost_preview"):
                        imported, skipped = 0, 0
                        edited_preview["Amount (€)"] = pd.to_numeric(edited_preview["Amount (€)"], errors="coerce")
                        for _, prow in edited_preview.iterrows():
                            amt = prow["Amount (€)"]
                            if pd.isna(amt) or (import_only_positive and float(amt) <= 0):
                                skipped += 1
                                continue
                            cat = normalize_category(prow["Category"]) if not pd.isna(prow["Category"]) else "Other"
                            dt = parse_date(prow["Date"])
                            desc = "" if pd.isna(prow.get("Description", "")) else str(prow.get("Description", "")).strip()
                            db.add_actual(project_id, cat, desc, float(amt), dt, "MCR Import (Cost)")
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
                    st.markdown("**Map your labour file columns to app input fields:**")
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
                    st.markdown("**Import Preview (editable before save):**")
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
            with st.expander("Raw actual entries"):
                raw_actuals = db.get_actuals(project_id)
                if not raw_actuals:
                    st.info("No actual entries yet.")
                else:
                    df_raw = pd.DataFrame(raw_actuals)
                    raw_cols = [c for c in ["date", "category", "task_name", "employee_name", "resource_group", "project_no", "description", "amount", "source", "import_file"] if c in df_raw.columns]
                    df_display = df_raw[raw_cols].rename(columns={
                        "date": "Date",
                        "category": "Category",
                        "task_name": "Task Name",
                        "employee_name": "Employee",
                        "resource_group": "Resource Group",
                        "project_no": "Project no.",
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
        st.subheader("🗺️ Employee to Task Mapping")
        st.caption("Map employees (and optionally project no. / resource group) to task names. Applied automatically during labour import.")
        if st.session_state.get("labour_mapping_save_msg"):
            st.success(st.session_state.pop("labour_mapping_save_msg"))
        st.caption("Use this when labour exports do not include direct task/description. Mapping is applied during import preview.")
        mappings = db.get_labour_task_mappings(active_only=False)
        if mappings:
            map_df = pd.DataFrame(mappings)[["id", "project_no", "employee_name", "resource_group", "task_name", "task_description", "is_active", "updated_at"]].rename(columns={"id": "ID", "project_no": "Project no.", "employee_name": "Employee Name", "resource_group": "Resource Group", "task_name": "Task Name", "task_description": "Task Description", "is_active": "Active", "updated_at": "Updated"})
            map_editor_df = map_df.copy()
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

        st.markdown("**Import mapping table (first pass):**")
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

        st.markdown("**Manual labour mapping**")
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
        st.subheader("⚠️ Unmapped Labour Actuals")
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
                else:
                    st.success("✓ All labour actuals are mapped to tasks!")
            else:
                st.info("No labour actuals in this project.")
        else:
            st.info("No actuals in this project.")

    with tab4:
        st.subheader("Labour Actuals")
        df_a = pd.DataFrame(actuals)
        df_a["amount"] = pd.to_numeric(df_a["amount"], errors="coerce").fillna(0.0)
        st.markdown(f"**Total Actuals: € {df_a['amount'].sum():,.2f}**")

        # Labour monthly task summary with one-level drill-down to employee.
        if "task_name" in df_a.columns:
            df_lab = df_a[df_a["category"].fillna("").str.lower() == "labour"].copy()
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
                    st.markdown("**Labour Actuals by Task and Month (€):**")
                    task_table = task_pivot.reset_index().rename(columns={"Task": "Task"})
                    if "actuals_task_drill_open" not in st.session_state:
                        st.session_state["actuals_task_drill_open"] = {}
                    open_state = st.session_state["actuals_task_drill_open"]

                    task_table = task_table.sort_values("Total (€)", ascending=False).reset_index(drop=True)

                    # Build per-task lookup maps for Project no. and Resource group
                    _proj_col = "project_no" if "project_no" in df_lab.columns else None
                    _rg_col = "resource_group" if "resource_group" in df_lab.columns else None

                    task_project_map = {}   # task_name -> set of project_no values
                    task_rg_map = {}        # task_name -> set of resource_group values
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

                    # Project no. filter
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

                    # Resource group filter
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

                    # Apply all filters to task list
                    def _task_matches(t):
                        if t not in selected_tasks:
                            return False
                        if sel_projects and not task_project_map.get(t, set()).intersection(sel_projects):
                            return False
                        if sel_rgs and not task_rg_map.get(t, set()).intersection(sel_rgs):
                            return False
                        return True

                    filtered_tasks = task_table[task_table["Task"].map(_task_matches)].copy()
                    month_cols = [c for c in task_table.columns if c not in ["Task", "Total (€)"]]

                    combined_rows = []
                    for _, trow in filtered_tasks.iterrows():
                        task_name = trow["Task"]
                        task_open = bool(open_state.get(task_name, False))

                        arrow = "\u25bc " if task_open else "\u25ba "
                        _task_projs = task_project_map.get(task_name, set())
                        _task_rgs = task_rg_map.get(task_name, set())
                        _task_proj_str = ", ".join(sorted(_task_projs)) if _task_projs else ""
                        _task_rg_str = "Various" if len(_task_rgs) > 1 else (next(iter(_task_rgs)) if _task_rgs else "")
                        task_row = {
                            "Task": f"{arrow}{task_name}",
                            "Project no.": _task_proj_str,
                            "Resource group": _task_rg_str,
                            "Toggle": task_open,
                            "_row_type": "task",
                            "_task": task_name,
                        }
                        for c in month_cols + ["Total (\u20ac)"]:
                            task_row[c] = float(trow[c])
                        combined_rows.append(task_row)

                        if task_open:
                            df_task = df_lab[df_lab["Task"] == task_name].copy()
                            df_task["Employee"] = df_task.get("employee_name", "").fillna("").replace("", "(unknown employee)")
                            df_task["Month"] = pd.to_datetime(df_task["date"], errors="coerce").dt.strftime("%Y-%m").fillna("Unknown")
                            _emp_rg_col = "resource_group" if "resource_group" in df_task.columns else None
                            _emp_proj_col = "project_no" if "project_no" in df_task.columns else None
                            emp_month = (
                                df_task.groupby(["Employee", "Month"], as_index=False)["amount"]
                                .sum()
                                .rename(columns={"amount": "Amount (\u20ac)"})
                            )
                            emp_pivot = emp_month.pivot_table(
                                index="Employee",
                                columns="Month",
                                values="Amount (\u20ac)",
                                aggfunc="sum",
                                fill_value=0,
                            )
                            if not emp_pivot.empty:
                                for mc in month_cols:
                                    if mc not in emp_pivot.columns:
                                        emp_pivot[mc] = 0.0
                                emp_pivot = emp_pivot[month_cols]
                                emp_pivot["Total (\u20ac)"] = emp_pivot.sum(axis=1)
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
                                    for c in month_cols + ["Total (\u20ac)"]:
                                        child_row[c] = float(erow[c])
                                    combined_rows.append(child_row)

                    if combined_rows:
                        combined_df = pd.DataFrame(combined_rows)
                    else:
                        combined_df = pd.DataFrame(columns=["Task", "Project no.", "Resource group", "Toggle"] + month_cols + ["Total (€)", "_row_type", "_task"])

                    total_row = {
                        "Task": "TOTAL",
                        "Project no.": "",
                        "Resource group": "",
                        "Toggle": False,
                        "_row_type": "total",
                        "_task": "",
                    }
                    for c in month_cols + ["Total (€)"]:
                        total_row[c] = pd.to_numeric(filtered_tasks[c], errors="coerce").fillna(0).sum() if c in filtered_tasks.columns else 0.0

                    combined_df = pd.concat([combined_df, pd.DataFrame([total_row])], ignore_index=True)

                    display_df = combined_df[["Task", "Project no.", "Resource group"] + month_cols + ["Total (€)"]].copy()

                    # Dynamic key resets selection whenever table structure changes
                    import hashlib as _hl
                    _open_sig = "_".join(f"{k}={v}" for k, v in sorted(open_state.items()))
                    _table_key = "atme_" + _hl.md5(_open_sig.encode()).hexdigest()[:8]

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

                    # Handle row-click toggle
                    _sel_rows = sel_result.selection.get("rows", []) if sel_result and hasattr(sel_result, "selection") else []
                    if _sel_rows:
                        _clicked_idx = _sel_rows[0]
                        if _clicked_idx < len(combined_df):
                            _clicked_type = combined_df.iloc[_clicked_idx]["_row_type"]
                            _clicked_task = combined_df.iloc[_clicked_idx]["_task"]
                            if _clicked_type == "task" and _clicked_task:
                                open_state[_clicked_task] = not open_state.get(_clicked_task, False)
                                st.rerun()

        with st.expander("🗑️ Delete an actual entry"):
            del_options = {f"[{r['id']}] {r['date']} - {r['category']} (€{r['amount']:,.0f})": r["id"] for r in actuals}
            sel = st.selectbox("Select entry to delete", list(del_options.keys()))
            if st.button("Delete selected entry"):
                db.delete_actual(del_options[sel])
                st.success("Deleted.")
                st.rerun()

# ════════════════════════════════════════════════════════════════════════════
# 5. FORECAST
# ════════════════════════════════════════════════════════════════════════════
elif page == "🔮 Forecast":
    st.title("🔮 Forecast (Estimate to Complete)")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    st.markdown("""
    For each cost category, enter your **Estimate to Complete (ETC)** — 
    the additional spending you expect from today until project end.
    
    **EAC (Estimate at Completion) = Actuals + ETC**
    """)

    actuals = db.get_actuals(project_id)
    df_a = pd.DataFrame(actuals) if actuals else pd.DataFrame(columns=["category", "amount"])
    actuals_by_cat = df_a.groupby("category")["amount"].sum().to_dict() if not df_a.empty else {}

    forecasts = {f["category"]: f for f in db.get_forecasts(project_id)}

    with st.form("forecast_form"):
        st.markdown("**Enter ETC per category:**")
        new_forecasts = {}
        for cat in CATEGORIES:
            actual_val = actuals_by_cat.get(cat, 0.0)
            current_etc = forecasts.get(cat, {}).get("etc", 0.0)
            current_note = forecasts.get(cat, {}).get("note", "")
            c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
            c1.markdown(f"**{cat}**")
            c2.metric("Actuals (€)", f"{actual_val:,.0f}")
            etc_val = c3.number_input(f"ETC (€)", value=float(current_etc), key=f"etc_{cat}", min_value=0.0, step=100.0, label_visibility="collapsed")
            note = c4.text_input("Note", value=current_note, key=f"note_{cat}", label_visibility="collapsed")
            new_forecasts[cat] = (etc_val, note)

        if st.form_submit_button("💾 Save Forecast"):
            for cat, (etc_val, note) in new_forecasts.items():
                db.upsert_forecast(project_id, cat, etc_val, note)
            st.success("Forecast saved!")
            st.rerun()

    # Summary
    summary = db.get_project_summary(project_id)
    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Budget", f"€ {summary['planned']:,.0f}")
    c2.metric("Total Actuals", f"€ {summary['actual']:,.0f}")
    c3.metric("Total ETC", f"€ {summary['etc']:,.0f}")
    delta_color = "normal" if summary["variance"] >= 0 else "inverse"
    c4.metric("EAC vs Budget", f"€ {summary['eac']:,.0f}", delta=f"€ {summary['variance']:,.0f}")

# ════════════════════════════════════════════════════════════════════════════
# 6. VARIANCE REPORT
# ════════════════════════════════════════════════════════════════════════════
elif page == "📊 Variance Report":
    st.title("📊 Variance Report")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    budget_items = db.get_budget_items(project_id)
    actuals = db.get_actuals(project_id)
    forecasts = {f["category"]: f["etc"] for f in db.get_forecasts(project_id)}

    df_b = pd.DataFrame(budget_items) if budget_items else pd.DataFrame(columns=["category", "planned"])
    df_a = pd.DataFrame(actuals) if actuals else pd.DataFrame(columns=["category", "amount"])

    planned_by_cat = df_b.groupby("category")["planned"].sum()
    actuals_by_cat = df_a.groupby("category")["amount"].sum()

    rows = []
    all_cats = set(list(planned_by_cat.index) + list(actuals_by_cat.index) + list(forecasts.keys()))
    for cat in sorted(all_cats):
        plan = planned_by_cat.get(cat, 0.0)
        act = actuals_by_cat.get(cat, 0.0)
        etc = forecasts.get(cat, 0.0)
        eac = act + etc
        var = plan - eac
        rows.append({
            "Category": cat,
            "Budget (€)": plan,
            "Actuals (€)": act,
            "ETC (€)": etc,
            "EAC (€)": eac,
            "Variance (€)": var,
            "% Used": f"{(act/plan*100):.1f}%" if plan > 0 else "N/A",
        })

    if rows:
        df_v = pd.DataFrame(rows)
        # Colour negative variances red
        def color_variance(val):
            try:
                return "color: red; font-weight: bold" if float(val) < 0 else "color: green"
            except Exception:
                return ""

        render_table_view(
            df_v,
            key_prefix="variance_report",
            filter_columns=["Category"],
            total_columns=["Budget (€)", "Actuals (€)", "ETC (€)", "EAC (€)", "Variance (€)"],
            show_totals=True,
            hide_index=True,
            currency_columns=["Budget (€)", "Actuals (€)", "ETC (€)", "EAC (€)", "Variance (€)"],
        )

        # Totals row
        total_plan = df_v["Budget (€)"].sum()
        total_act = df_v["Actuals (€)"].sum()
        total_eac = df_v["EAC (€)"].sum()
        total_var = df_v["Variance (€)"].sum()
        st.markdown(f"**Totals: Budget € {total_plan:,.0f} | Actuals € {total_act:,.0f} | EAC € {total_eac:,.0f} | Variance € {total_var:,.0f}**")

        # Waterfall-style chart
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Budget", x=df_v["Category"], y=df_v["Budget (€)"], marker_color="#4C78A8"))
        fig.add_trace(go.Bar(name="Actuals", x=df_v["Category"], y=df_v["Actuals (€)"], marker_color="#F58518"))
        fig.add_trace(go.Bar(name="EAC", x=df_v["Category"], y=df_v["EAC (€)"], marker_color="#E45756"))
        fig.update_layout(barmode="group", title="Budget vs Actuals vs EAC by Category", height=400)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data yet. Add budget lines and actuals first.")

# ════════════════════════════════════════════════════════════════════════════
# 7. EXPORT
# ════════════════════════════════════════════════════════════════════════════
elif page == "📤 Export":
    st.title("📤 Export to Excel")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    st.markdown(f"Export all data for **{project['name']}** as an Excel workbook with multiple sheets.")

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
            data=output,
            file_name=f"{project['name'].replace(' ', '_')}_budget_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.success("Report ready — click the button above to download.")
