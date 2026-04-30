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

# ════════════════════════════════════════════════════════════════════════════
# 1. DASHBOARD
# ════════════════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.title("🏠 Dashboard")
    st.markdown("Overview of all active projects.")

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
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("---")
        # Budget vs Actuals bar chart
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Budget", x=df["Project"], y=df["Budget (€)"], marker_color="#4C78A8"))
        fig.add_trace(go.Bar(name="Actuals", x=df["Project"], y=df["Actuals (€)"], marker_color="#F58518"))
        fig.add_trace(go.Bar(name="EAC", x=df["Project"], y=df["EAC (€)"], marker_color="#E45756"))
        fig.update_layout(barmode="group", title="Budget vs Actuals vs EAC", height=400)
        st.plotly_chart(fig, use_container_width=True)

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

    # ── Derive project months ────────────────────────────────────────────────
    try:
        p_start = datetime.date.fromisoformat(project["start_date"])
        p_end   = datetime.date.fromisoformat(project["end_date"])
    except Exception:
        p_start = datetime.date.today().replace(day=1)
        p_end   = p_start.replace(year=p_start.year + 1)

    # Build list of YYYY-MM strings covering the project span
    def project_months(start, end):
        months = []
        cur = start.replace(day=1)
        while cur <= end.replace(day=1):
            months.append(cur.strftime("%Y-%m"))
            # advance one month
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)
        return months

    all_months = project_months(p_start, p_end)

    st.markdown(
        f"**Project:** {project['name']} &nbsp;|&nbsp; "
        f"{project['start_date']} → {project['end_date']} &nbsp;|&nbsp; "
        f"**{len(all_months)} months**"
    )
    st.markdown("---")

    # ── Tabs: per-month entry vs bulk annual entry ───────────────────────────
    tab_monthly, tab_bulk, tab_view = st.tabs(
        ["📅 Monthly Entry", "📦 Bulk Annual Distribution", "📊 View / Delete"]
    )

    # ── TAB 1: Monthly entry ─────────────────────────────────────────────────
    with tab_monthly:
        st.markdown(
            "Enter a planned amount for each month individually. "
            "Select the category and task description, then fill in amounts per month."
        )
        with st.form("monthly_budget_form"):
            fc1, fc2 = st.columns(2)
            sel_category = fc1.selectbox("Category", CATEGORIES, key="mb_cat")
            sel_desc     = fc2.text_input("Description / Task", key="mb_desc")

            st.markdown("**Monthly amounts table (€):**")
            month_table = pd.DataFrame({
                "Month": all_months,
                "Amount (€)": [0.0] * len(all_months),
            })
            edited_table = st.data_editor(
                month_table,
                hide_index=True,
                use_container_width=True,
                disabled=["Month"],
                column_config={
                    "Month": st.column_config.TextColumn("Month"),
                    "Amount (€)": st.column_config.NumberColumn("Amount (€)", min_value=0.0, step=100.0, format="%.2f"),
                },
                key="monthly_amounts_table",
            )

            if st.form_submit_button("💾 Save Monthly Plan"):
                saved = 0
                edited_table["Amount (€)"] = pd.to_numeric(edited_table["Amount (€)"], errors="coerce").fillna(0.0)
                for _, row in edited_table.iterrows():
                    m = str(row["Month"])
                    val = float(row["Amount (€)"])
                    if val > 0:
                        db.add_budget_item(project_id, sel_category, sel_desc, val, m)
                        saved += 1
                if saved:
                    st.success(f"Saved {saved} monthly budget entries.")
                    st.rerun()
                else:
                    st.warning("All amounts are zero — nothing saved.")

    # ── TAB 2: Bulk annual distribution ──────────────────────────────────────
    with tab_bulk:
        st.markdown(
            "Enter a total annual amount and it will be **equally distributed** "
            "across all project months automatically."
        )
        with st.form("bulk_budget_form"):
            bc1, bc2, bc3 = st.columns(3)
            bulk_category = bc1.selectbox("Category", CATEGORIES, key="bulk_cat")
            bulk_amount   = bc2.number_input("Total Annual Amount (€)", min_value=0.0, step=500.0, key="bulk_amt")
            bulk_desc     = bc3.text_input("Description / Task", key="bulk_desc")

            n_months = len(all_months)
            if n_months > 0 and bulk_amount > 0:
                per_month = bulk_amount / n_months
                st.info(f"This will create **{n_months} entries** of **€ {per_month:,.2f}** each (months: {all_months[0]} → {all_months[-1]}).")

            if st.form_submit_button("💾 Distribute & Save"):
                if bulk_amount <= 0:
                    st.error("Please enter an amount greater than zero.")
                elif n_months == 0:
                    st.error("Project has no months defined. Check project start/end dates.")
                else:
                    per_month_val = bulk_amount / n_months
                    for m in all_months:
                        db.add_budget_item(project_id, bulk_category, bulk_desc, per_month_val, m)
                    st.success(f"Created {n_months} monthly entries of € {per_month_val:,.2f} for '{bulk_category}'.")
                    st.rerun()

    # ── TAB 3: View & delete ──────────────────────────────────────────────────
    with tab_view:
        items = db.get_budget_items(project_id)
        if not items:
            st.info("No budget lines yet. Use the tabs above to add planning entries.")
        else:
            df = pd.DataFrame(items)

            # Summary totals
            total = df["planned"].sum()
            c1, c2 = st.columns(2)
            c1.metric("Total Budget", f"€ {total:,.2f}")
            c2.metric("Budget Lines", len(df))

            # Monthly pivot table: rows = category, columns = month
            pivot = df.pivot_table(
                index="category", columns="month", values="planned",
                aggfunc="sum", fill_value=0
            )
            if len(pivot.columns) > 0:
                # Sort columns chronologically
                sorted_cols = sorted([c for c in pivot.columns if c], key=lambda x: str(x))
                other_cols = [c for c in pivot.columns if c not in sorted_cols]
                pivot = pivot[sorted_cols + other_cols]
                pivot.loc["TOTAL"] = pivot.sum()
                st.markdown("**Budget by Category × Month (€):**")
                st.dataframe(pivot.style.format("{:,.0f}"), use_container_width=True)
            else:
                st.info("No monthly allocation yet. Existing lines were likely saved without month values.")
                fallback = df.groupby("category", as_index=False)["planned"].sum().rename(
                    columns={"category": "Category", "planned": "Planned (€)"}
                )
                st.markdown("**Budget by Category (€):**")
                st.dataframe(fallback, use_container_width=True, hide_index=True)

            # Charts
            col_chart1, col_chart2 = st.columns(2)
            with col_chart1:
                cat_df = df.groupby("category")["planned"].sum().reset_index()
                fig_pie = px.pie(cat_df, values="planned", names="category", title="Budget by Category")
                st.plotly_chart(fig_pie, use_container_width=True)
            with col_chart2:
                month_df = (
                    df[df["month"].notna()]
                    .groupby("month")["planned"]
                    .sum()
                    .reset_index()
                    .sort_values("month")
                )
                if not month_df.empty:
                    fig_bar = px.bar(month_df, x="month", y="planned", title="Budget by Month",
                                     labels={"month": "Month", "planned": "Planned (€)"})
                    st.plotly_chart(fig_bar, use_container_width=True)
                else:
                    st.info("No month-based data yet for monthly chart.")

            # Delete
            with st.expander("🗑️ Delete budget entries"):
                del_scope = st.radio("Delete by", ["Single line", "Entire category", "All lines for this project"], horizontal=True)
                if del_scope == "Single line":
                    del_options = {
                        f"[{r['id']}] {r['category']} | {r['month'] or 'no month'} | {r['description']} (€{r['planned']:,.0f})": r["id"]
                        for r in items
                    }
                    sel = st.selectbox("Select line", list(del_options.keys()))
                    if st.button("Delete selected line"):
                        db.delete_budget_item(del_options[sel])
                        st.success("Deleted.")
                        st.rerun()
                elif del_scope == "Entire category":
                    cats = sorted(df["category"].unique())
                    sel_cat = st.selectbox("Select category to delete", cats)
                    ids_to_del = df[df["category"] == sel_cat]["id"].tolist()
                    st.warning(f"This will delete {len(ids_to_del)} lines for '{sel_cat}'.")
                    if st.button("Delete entire category"):
                        for i in ids_to_del:
                            db.delete_budget_item(i)
                        st.success("Deleted.")
                        st.rerun()
                else:
                    st.warning(f"This will delete ALL {len(items)} budget lines for this project.")
                    if st.button("Delete ALL budget lines"):
                        for r in items:
                            db.delete_budget_item(r["id"])
                        st.success("All budget lines deleted.")
                        st.rerun()
# ════════════════════════════════════════════════════════════════════════════
# 4. ACTUALS
# ════════════════════════════════════════════════════════════════════════════
elif page == "📥 Actuals":
    st.title("📥 Actuals Entry")
    project_id, project = select_project()
    if not project_id:
        st.stop()

    tab1, tab2 = st.tabs(["✏️ Manual Entry", "📂 Import from CSV/Excel (MCR)"])

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
        st.markdown("""
        Upload a CSV or Excel file exported from MCR (or any system).
        The file must have at least these columns (names flexible):
        - **category** (or Cost Type, Category)
        - **amount** (or Amount, Cost, Value)
        - **date** (or Date, Booking Date)
        - **description** (optional)
        """)
        uploaded = st.file_uploader("Upload file", type=["csv", "xlsx", "xls"])
        if uploaded:
            try:
                if uploaded.name.endswith(".csv"):
                    df_up = pd.read_csv(uploaded)
                else:
                    df_up = pd.read_excel(uploaded)

                st.write("Preview (first 5 rows):", df_up.head())

                # Flexible column mapping (auto-suggested for MCR-like exports)
                col_map = {}
                st.markdown("**Map your file columns to app input fields:**")
                cols = ["(none)"] + list(df_up.columns)
                default_category = guess_column(df_up.columns, ["category", "cost type", "cost_category", "cost element", "expense type"])
                default_amount = guess_column(df_up.columns, ["amount", "cost", "value", "actuals", "actual amount", "booked amount"])
                default_date = guess_column(df_up.columns, ["date", "booking date", "posting date", "document date", "period"])
                default_desc = guess_column(df_up.columns, ["description", "task", "text", "comment", "note", "item"])
                default_project_no = guess_column(df_up.columns, ["project no", "project_no", "project", "project number", "wbs", "wbs element"])
                default_employee = guess_column(df_up.columns, ["employee", "employee name", "name", "resource", "person"]) 
                default_rg = guess_column(df_up.columns, ["resource group", "resource_group", "rg", "team", "cost center"])

                # Mapping profiles
                profiles = db.get_import_profiles("actuals")
                profile_names = [p["profile_name"] for p in profiles]
                selected_profile = st.selectbox(
                    "Import mapping profile",
                    ["(none)"] + profile_names,
                    index=0,
                    help="Load or save mapping settings for repeated imports.",
                )
                selected_settings = {}
                if selected_profile != "(none)":
                    selected_settings = next(
                        (p["settings"] for p in profiles if p["profile_name"] == selected_profile),
                        {},
                    )

                mc1, mc2, mc3, mc4 = st.columns(4)
                pref_category = selected_settings.get("col_map", {}).get("category", default_category)
                pref_amount = selected_settings.get("col_map", {}).get("amount", default_amount)
                pref_date = selected_settings.get("col_map", {}).get("date", default_date)
                pref_desc = selected_settings.get("col_map", {}).get("description", default_desc)
                pref_project_no = selected_settings.get("col_map", {}).get("project_no", default_project_no)
                pref_employee = selected_settings.get("col_map", {}).get("employee", default_employee)
                pref_rg = selected_settings.get("col_map", {}).get("resource_group", default_rg)

                col_map["category"] = mc1.selectbox("Category -> App Category", cols, index=cols.index(pref_category) if pref_category in cols else 0)
                col_map["amount"] = mc2.selectbox("Amount -> App Amount", cols, index=cols.index(pref_amount) if pref_amount in cols else 0)
                col_map["date"] = mc3.selectbox("Date -> App Date", cols, index=cols.index(pref_date) if pref_date in cols else 0)
                col_map["description"] = mc4.selectbox("Description -> App Description", cols, index=cols.index(pref_desc) if pref_desc in cols else 0)

                ec1, ec2, ec3 = st.columns(3)
                col_map["project_no"] = ec1.selectbox("Project no. -> Labour Mapping", cols, index=cols.index(pref_project_no) if pref_project_no in cols else 0)
                col_map["employee"] = ec2.selectbox("Employee -> Labour Mapping", cols, index=cols.index(pref_employee) if pref_employee in cols else 0)
                col_map["resource_group"] = ec3.selectbox("Resource Group -> Labour Mapping", cols, index=cols.index(pref_rg) if pref_rg in cols else 0)

                oc1, oc2 = st.columns(2)
                pref_default_cat = selected_settings.get("default_category_if_missing", "Other")
                default_category_if_missing = oc1.selectbox(
                    "Default category if missing",
                    CATEGORIES,
                    index=CATEGORIES.index(pref_default_cat) if pref_default_cat in CATEGORIES else CATEGORIES.index("Other"),
                )
                pref_amount_mode = selected_settings.get("amount_mode", "Keep as in file")
                amount_mode = oc2.selectbox(
                    "Amount sign handling",
                    [
                        "Keep as in file",
                        "Convert to positive (absolute)",
                        "Invert sign",
                    ],
                    index=["Keep as in file", "Convert to positive (absolute)", "Invert sign"].index(pref_amount_mode)
                    if pref_amount_mode in ["Keep as in file", "Convert to positive (absolute)", "Invert sign"]
                    else 0,
                )

                pc1, pc2, pc3 = st.columns([2, 1, 1])
                profile_name_input = pc1.text_input("Profile name to save", value=selected_profile if selected_profile != "(none)" else "")
                save_profile_clicked = pc2.button("💾 Save Profile")
                delete_profile_clicked = pc3.button("🗑️ Delete Profile")

                current_profile_settings = {
                    "col_map": {
                        "category": col_map["category"],
                        "amount": col_map["amount"],
                        "date": col_map["date"],
                        "description": col_map["description"],
                        "project_no": col_map["project_no"],
                        "employee": col_map["employee"],
                        "resource_group": col_map["resource_group"],
                    },
                    "default_category_if_missing": default_category_if_missing,
                    "amount_mode": amount_mode,
                }

                if save_profile_clicked:
                    if profile_name_input.strip():
                        db.save_import_profile(profile_name_input.strip(), current_profile_settings, "actuals")
                        st.success(f"Profile '{profile_name_input.strip()}' saved.")
                        st.rerun()
                    else:
                        st.error("Enter a profile name before saving.")

                if delete_profile_clicked:
                    if selected_profile == "(none)":
                        st.error("Select a profile to delete.")
                    else:
                        db.delete_import_profile(selected_profile, "actuals")
                        st.success(f"Profile '{selected_profile}' deleted.")
                        st.rerun()

                with st.expander("🧭 Labour Mapping Table (Project no. -> Employee -> Resource Group -> Task)", expanded=False):
                    st.caption("Use this when labour exports do not include direct task/description. Mapping is applied during import preview.")
                    mappings = db.get_labour_task_mappings(active_only=False)
                    if mappings:
                        map_df = pd.DataFrame(mappings)[[
                            "id", "project_no", "employee_name", "resource_group", "task_name", "task_description", "is_active", "updated_at"
                        ]].rename(
                            columns={
                                "id": "ID",
                                "project_no": "Project no.",
                                "employee_name": "Employee Name",
                                "resource_group": "Resource Group",
                                "task_name": "Task Name",
                                "task_description": "Task Description",
                                "is_active": "Active",
                                "updated_at": "Updated",
                            }
                        )
                        st.dataframe(map_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No labour mappings yet.")

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
                                db.upsert_labour_task_mapping(
                                    map_project_no.strip(),
                                    map_employee.strip(),
                                    map_rg.strip(),
                                    map_task.strip(),
                                    map_task_desc.strip(),
                                    map_active,
                                )
                                st.success("Labour mapping saved.")
                                st.rerun()

                    st.markdown("**Import mapping table (first pass):**")
                    mapping_upload = st.file_uploader(
                        "Upload labour mapping file",
                        type=["xlsx", "xls", "csv"],
                        key="labour_mapping_upload",
                        help="Expected fields: Project no., Employee, Resource group, Task name.",
                    )
                    if mapping_upload:
                        try:
                            if mapping_upload.name.lower().endswith(".csv"):
                                map_src_df = pd.read_csv(mapping_upload)
                            else:
                                map_src_df = pd.read_excel(mapping_upload)

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
                                imported_map = 0
                                skipped_map = 0
                                for _, mr in map_src_df.iterrows():
                                    project_no = str(mr[map_project_col]).strip() if map_project_col != "(none)" else ""
                                    employee_name = str(mr[map_emp_col]).strip() if map_emp_col != "(none)" else ""
                                    resource_group = str(mr[map_rg_col]).strip() if map_rg_col != "(none)" else ""
                                    task_name = str(mr[map_task_col]).strip() if map_task_col != "(none)" else ""
                                    task_desc = str(mr[map_desc_col]).strip() if map_desc_col != "(none)" else ""

                                    if project_no.lower() == "nan":
                                        project_no = ""
                                    if employee_name.lower() == "nan":
                                        employee_name = ""
                                    if resource_group.lower() == "nan":
                                        resource_group = ""
                                    if task_name.lower() == "nan":
                                        task_name = ""
                                    if task_desc.lower() == "nan":
                                        task_desc = ""

                                    if not employee_name or not task_name:
                                        skipped_map += 1
                                        continue

                                    db.upsert_labour_task_mapping(
                                        project_no,
                                        employee_name,
                                        resource_group,
                                        task_name,
                                        task_desc,
                                        import_active,
                                    )
                                    imported_map += 1

                                st.success(f"Imported/updated {imported_map} mapping rows.")
                                if skipped_map:
                                    st.warning(f"Skipped {skipped_map} rows (missing Employee Name or Task Name).")
                                st.rerun()
                        except Exception as map_ex:
                            st.error(f"Error reading mapping file: {map_ex}")

                    if mappings:
                        del_map_options = {
                            f"[{m['id']}] {m.get('project_no') or '-'} | {m['employee_name']} | {m['resource_group'] or '-'} -> {m['task_name']}": m["id"]
                            for m in mappings
                        }
                        sel_del = st.selectbox("Delete labour mapping", list(del_map_options.keys()))
                        if st.button("🗑️ Delete selected mapping"):
                            db.delete_labour_task_mapping(del_map_options[sel_del])
                            st.success("Labour mapping deleted.")
                            st.rerun()

                # Build normalized preview table based on selected mappings
                preview_rows = []
                for _, row in df_up.iterrows():
                    raw_cat = row[col_map["category"]] if col_map["category"] != "(none)" else default_category_if_missing
                    cat = normalize_category(raw_cat)
                    if cat == "Other" and col_map["category"] == "(none)":
                        cat = default_category_if_missing

                    employee_val = ""
                    if col_map["employee"] != "(none)":
                        employee_val = str(row[col_map["employee"]]).strip()
                        if employee_val.lower() == "nan":
                            employee_val = ""

                    project_no_val = ""
                    if col_map["project_no"] != "(none)":
                        project_no_val = str(row[col_map["project_no"]]).strip()
                        if project_no_val.lower() == "nan":
                            project_no_val = ""

                    rg_val = ""
                    if col_map["resource_group"] != "(none)":
                        rg_val = str(row[col_map["resource_group"]]).strip()
                        if rg_val.lower() == "nan":
                            rg_val = ""

                    amt = parse_amount(row[col_map["amount"]]) if col_map["amount"] != "(none)" else None
                    if amt is not None:
                        if amount_mode == "Convert to positive (absolute)":
                            amt = abs(amt)
                        elif amount_mode == "Invert sign":
                            amt = -amt

                    dt = parse_date(row[col_map["date"]]) if col_map["date"] != "(none)" else str(datetime.date.today())
                    if col_map["description"] != "(none)":
                        desc = str(row[col_map["description"]]).strip()
                        if desc.lower() == "nan":
                            desc = ""
                    else:
                        desc = ""

                    mapped_task_name = ""
                    mapped_task_desc = ""
                    mapping_status = "Not mapped"
                    if employee_val:
                        mapping_row = db.resolve_labour_task_mapping(project_no_val, employee_val, rg_val)
                        if mapping_row:
                            mapped_task_name = mapping_row.get("task_name", "") or ""
                            mapped_task_desc = mapping_row.get("task_description", "") or ""
                            mapping_status = "Mapped"

                    if mapped_task_name or mapped_task_desc:
                        if mapped_task_name and mapped_task_desc:
                            desc = f"{mapped_task_name} - {mapped_task_desc}"
                        elif mapped_task_name:
                            desc = mapped_task_name
                        else:
                            desc = mapped_task_desc

                    preview_rows.append({
                        "Date": dt,
                        "Category": cat,
                        "Project no.": project_no_val,
                        "Employee": employee_val,
                        "Resource Group": rg_val,
                        "Task Name": mapped_task_name,
                        "Task Description": mapped_task_desc,
                        "Description": desc,
                        "Amount (€)": amt,
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
                        "Category": st.column_config.SelectboxColumn("Category", options=CATEGORIES),
                        "Project no.": st.column_config.TextColumn("Project no."),
                        "Employee": st.column_config.TextColumn("Employee"),
                        "Resource Group": st.column_config.TextColumn("Resource Group"),
                        "Task Name": st.column_config.TextColumn("Task Name"),
                        "Task Description": st.column_config.TextColumn("Task Description"),
                        "Description": st.column_config.TextColumn("Description"),
                        "Amount (€)": st.column_config.NumberColumn("Amount (€)", step=1.0),
                        "Mapping Status": st.column_config.TextColumn("Mapping Status"),
                    },
                    key="actuals_import_preview",
                )

                c_imp1, c_imp2 = st.columns(2)
                import_only_positive = c_imp1.checkbox("Import only positive amounts", value=True)
                skip_blank_desc = c_imp2.checkbox("Allow blank descriptions", value=True)

                if st.button("Import Preview Rows"):
                    imported = 0
                    skipped = 0

                    edited_preview["Amount (€)"] = pd.to_numeric(edited_preview["Amount (€)"], errors="coerce")
                    for _, prow in edited_preview.iterrows():
                        amt = prow["Amount (€)"]
                        if pd.isna(amt):
                            skipped += 1
                            continue
                        if import_only_positive and float(amt) <= 0:
                            skipped += 1
                            continue

                        task_name = "" if pd.isna(prow.get("Task Name", "")) else str(prow.get("Task Name", "")).strip()
                        task_desc = "" if pd.isna(prow.get("Task Description", "")) else str(prow.get("Task Description", "")).strip()
                        base_desc = "" if pd.isna(prow.get("Description", "")) else str(prow.get("Description", "")).strip()
                        if task_name and task_desc:
                            final_desc = f"{task_name} - {task_desc}"
                        elif task_name:
                            final_desc = task_name
                        elif task_desc:
                            final_desc = task_desc
                        else:
                            final_desc = base_desc

                        if not skip_blank_desc and not final_desc:
                            skipped += 1
                            continue

                        cat = normalize_category(prow["Category"]) if not pd.isna(prow["Category"]) else "Other"
                        dt = parse_date(prow["Date"])

                        db.add_actual(project_id, cat, final_desc, float(amt), dt, "MCR Import")
                        imported += 1

                    st.success(f"Imported {imported} rows.")
                    if skipped:
                        st.warning(f"Skipped {skipped} rows due to validation rules.")
                    st.rerun()
            except Exception as e:
                st.error(f"Error reading file: {e}")

    # ── Show actuals table ──
    actuals = db.get_actuals(project_id)
    if actuals:
        st.markdown("---")
        st.subheader("Recorded Actuals")
        df_a = pd.DataFrame(actuals)
        df_display = df_a[["date", "category", "description", "amount", "source"]].rename(columns={
            "date": "Date", "category": "Category", "description": "Description",
            "amount": "Amount (€)", "source": "Source"
        })
        st.dataframe(df_display, use_container_width=True, hide_index=True)
        st.markdown(f"**Total Actuals: € {df_a['amount'].sum():,.2f}**")

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

        st.dataframe(
            df_v.style.applymap(color_variance, subset=["Variance (€)"]),
            use_container_width=True, hide_index=True
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
