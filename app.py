import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
from calendar import monthrange
from supabase import create_client, ClientOptions
import io

# Connect to Supabase
sb = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"],
    options=ClientOptions(schema="public", auto_refresh_token=False, persist_session=False)
)

PLATFORMS = ["DoorDash", "Uber", "Other"]
TAX_RATE = 0.30 # 15.3% SE Tax + ~12% Fed + 2.5% City ≈ 30%

st.set_page_config(page_title="Gig Tracker", page_icon="🚗", layout="wide")
st.title("🚗 Gig Income & Mileage Tracker")


# ---------------- Helpers ----------------
def get_mileage_rate(d: date) -> float:
    if d >= date(2026, 7, 1): return 0.76
    elif d >= date(2026, 1, 1): return 0.725
    elif d >= date(2025, 1, 1): return 0.70
    return 0.67

def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())

def load_entries() -> pd.DataFrame:
    res = sb.table("entries").select("*").order("entry_date").order("id").execute()
    df = pd.DataFrame(res.data)
    if df.empty:
        return df
    
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    if "miles" not in df.columns or df["miles"].isna().any():
        df["miles"] = df["odo_end"].astype(float) - df["odo_start"].astype(float)
    if "entry_type" not in df.columns:
        df["entry_type"] = "Business"
    df["entry_type"] = df["entry_type"].fillna("Business")
    if "hours_worked" not in df.columns:
        df["hours_worked"] = 0.0
    df["hours_worked"] = df["hours_worked"].fillna(0.0)

    df["week_start"] = df["entry_date"].apply(monday_of_week)
    df["rate"] = df["entry_date"].apply(get_mileage_rate)
    
    df["mileage_ded"] = df.apply(lambda row: row["miles"] * row["rate"] if row["entry_type"] == "Business" else 0.0, axis=1)
    df["taxable_profit"] = df.apply(lambda row: row["income"] - row["mileage_ded"] if row["entry_type"] == "Business" else 0.0, axis=1)
    df["hourly_wage"] = df.apply(lambda row: row["income"] / row["hours_worked"] if row["entry_type"] == "Business" and row["hours_worked"] > 0 else 0.0, axis=1)
    return df

def load_writeoffs() -> pd.DataFrame:
    res = sb.table("writeoffs").select("*").order("id").execute()
    return pd.DataFrame(res.data)

def recurring_total(writeoffs: pd.DataFrame, weeks: float, months: float) -> float:
    if writeoffs.empty: return 0.0
    active = writeoffs[writeoffs["active"] == True]
    return sum(float(w["amount"]) * (months if w["frequency"] == "monthly" else weeks) for _, w in active.iterrows())

def per_platform(df: pd.DataFrame) -> dict:
    df_biz = df[df["entry_type"] == "Business"]
    return {p: {"income": df_biz[df_biz["platform"] == p]["income"].sum(), "miles": df_biz[df_biz["platform"] == p]["miles"].sum()} for p in PLATFORMS}

entries = load_entries()
writeoffs = load_writeoffs()
last_odo = float(entries.iloc[-1]["odo_end"]) if not entries.empty else 0.0


# ---------------- Tabs ----------------
tab_entry, tab_view, tab_writeoffs = st.tabs(
    ["➕ Add Entry", "📊 View / Weekly Breakdown", "⚙️ Recurring Write-offs"]
)


# ================= TAB 1: ADD ENTRY =================
with tab_entry:
    st.subheader("Add a daily entry")
    with st.form("add_entry", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            entry_date = st.date_input("Date", value=date.today(), max_value=date.today())
            entry_type = st.selectbox("Entry Type", ["Business", "Personal"])
            if entry_type == "Business":
                platform = st.selectbox("Platform", PLATFORMS)
                income = st.number_input("Income ($)", min_value=0.0, step=0.01)
                hours_worked = st.number_input("Hours Worked (Active)", min_value=0.0, step=0.1)
        with col2:
            odo_start = st.number_input("Odometer start", min_value=0.0, step=0.1, value=last_odo)
            odo_end = st.number_input("Odometer end", min_value=0.0, step=0.1, value=last_odo)
        notes = st.text_input("Notes (optional)")
        submitted = st.form_submit_button("💾 Save")

        if submitted:
            if odo_end <= odo_start:
                st.error("❌ End odometer must be greater than start.")
            else:
                try:
                    final_income = float(income) if entry_type == "Business" else 0.0
                    final_platform = platform if entry_type == "Business" else "N/A"
                    final_hours = float(hours_worked) if entry_type == "Business" else 0.0
                    
                    sb.table("entries").insert({
                        "entry_date": entry_date.isoformat(), "entry_type": entry_type,
                        "platform": final_platform, "income": final_income,
                        "odo_start": float(odo_start), "odo_end": float(odo_end), 
                        "hours_worked": final_hours, "notes": notes,
                    }).execute()
                    
                    miles = odo_end - odo_start
                    rate = get_mileage_rate(entry_date) if entry_type == "Business" else 0.0
                    
                    msg = f"✅ Saved {entry_type} on {entry_date} | {miles:.1f} miles."
                    if entry_type == "Business":
                        hourly = final_income / final_hours if final_hours > 0 else 0
                        msg += f" | Income: ${final_income:.2f} | Hours: {final_hours:.1f} | Hourly: ${hourly:.2f}/hr"
                        
                    st.success(msg)
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")


# ================= TAB 2: VIEW / BREAKDOWN =================
with tab_view:
    if entries.empty:
        st.info("No entries yet. Add one in the ➕ tab.")
    else:
        months_available = sorted({(e.year, e.month) for e in entries["entry_date"]}, reverse=True)
        month_labels = [datetime(y, m, 1).strftime("%B %Y") for y, m in months_available]
        picked_label = st.selectbox("Select month", month_labels, index=0)
        picked_year, picked_month = months_available[month_labels.index(picked_label)]

        days_in_month = monthrange(picked_year, picked_month)[1]
        month_start_date = date(picked_year, picked_month, 1)
        month_end_date = date(picked_year, picked_month, days_in_month)

        month_df = entries[(entries["entry_date"].apply(lambda d: d.year) == picked_year) & (entries["entry_date"].apply(lambda d: d.month) == picked_month)].copy()
        month_df_biz = month_df[month_df["entry_type"] == "Business"]
        month_df_pers = month_df[month_df["entry_type"] == "Personal"]

        month_income = month_df_biz["income"].sum()
        month_biz_miles = month_df_biz["miles"].sum()
        month_pers_miles = month_df_pers["miles"].sum()
        month_mileage = month_df_biz["mileage_ded"].sum()
        month_rec = recurring_total(writeoffs, weeks=days_in_month / 7, months=1.0)
        month_net_cash = month_income - month_rec
        month_taxable = month_income - month_mileage - month_rec
        month_tax_savings = month_taxable * TAX_RATE if month_taxable > 0 else 0
        month_hours = month_df_biz["hours_worked"].sum()
        month_hourly = month_income / month_hours if month_hours > 0 else 0

        st.subheader(f"📅 {picked_label} — Monthly Summary")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Gross Income", f"${month_income:,.2f}")
        m2.metric("Net Biz Cash", f"${month_net_cash:,.2f}")
        m3.metric("Hours Worked", f"{month_hours:,.1f} hrs", f"${month_hourly:,.2f}/hr avg")
        m4.metric("Miles", f"{month_biz_miles:,.1f}", f"{month_pers_miles:,.1f} Personal")
        m5.metric("Schedule C Profit", f"${month_taxable:,.2f}")
        m6.metric("Set Aside for Taxes", f"${month_tax_savings:,.2f}")

        st.subheader("🚙 Income by platform (this month)")
        pp = per_platform(month_df)
        pc1, pc2, pc3 = st.columns(3)
        for col, p in zip([pc1, pc2, pc3], PLATFORMS):
            col.metric(f"{p}", f"${pp[p]['income']:,.2f}", f"{pp[p]['miles']:,.1f} mi")

        st.divider()

        st.subheader("📆 Week-by-week breakdown (Monday–Sunday)")
        weeks_present = sorted(month_df["week_start"].unique())
        run_income = run_cash = run_taxable = run_hours = 0.0

        for idx, wk_start in enumerate(weeks_present, start=1):
            wk_end = wk_start + timedelta(days=6)
            in_month_start = max(wk_start, month_start_date)
            in_month_end = min(wk_end, month_end_date)
            days_in_wk_bucket = (in_month_end - in_month_start).days + 1

            wk_df = month_df[month_df["week_start"] == wk_start]
            wk_df_biz = wk_df[wk_df["entry_type"] == "Business"]
            wk_df_pers = wk_df[wk_df["entry_type"] == "Personal"]
            
            wk_income = wk_df_biz["income"].sum()
            wk_biz_miles = wk_df_biz["miles"].sum()
            wk_pers_miles = wk_df_pers["miles"].sum()
            wk_mileage = wk_df_biz["mileage_ded"].sum()
            wk_hours = wk_df_biz["hours_worked"].sum()
            wk_hourly = wk_income / wk_hours if wk_hours > 0 else 0
            
            wk_rec = recurring_total(writeoffs, weeks=days_in_wk_bucket / 7, months=days_in_wk_bucket / days_in_month)
            wk_net_cash = wk_income - wk_rec
            wk_taxable = wk_income - wk_mileage - wk_rec
            wk_tax_savings = wk_taxable * TAX_RATE if wk_taxable > 0 else 0

            run_income += wk_income
            run_cash += wk_net_cash
            run_taxable += wk_taxable
            run_hours += wk_hours

            wk_label = f"{wk_start.strftime('%b %d')} – {wk_end.strftime('%b %d')}"
            with st.expander(
                f"**Week {idx}** ({wk_label}) — Gross ${wk_income:.2f} | ${wk_hourly:.2f}/hr | Schedule C ${wk_taxable:.2f}"
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Gross Income", f"${wk_income:,.2f}")
                c2.metric("Hours Worked", f"{wk_hours:,.1f} hrs", f"${wk_hourly:.2f}/hr avg")
                c3.metric("Mileage Shield", f"${wk_mileage:,.2f}")
                c4.metric("Schedule C Profit", f"${wk_taxable:,.2f}")

                st.caption(f"📈 **Running total through Week {idx}:** Gross ${run_income:,.2f} | Hours {run_hours:,.1f} | Schedule C ${run_taxable:,.2f}")

                # BUILD TABLE WITH TOTALS ROW FOR WEEKLY VIEW
                wk_table = wk_df[["entry_date", "entry_type", "platform", "income", "hours_worked", "miles", "rate", "mileage_ded", "taxable_profit", "notes"]].rename(columns={
                    "entry_date": "Date", "entry_type": "Type", "platform": "Platform", "income": "Income", 
                    "hours_worked": "Hours", "miles": "Miles", "rate": "Rate", 
                    "mileage_ded": "Mileage Shield", "taxable_profit": "Schedule C Profit", "notes": "Notes"
                })
                
                total_row_wk = pd.DataFrame([{
                    "Date": "TOTAL", "Type": "", "Platform": "", 
                    "Income": wk_table['Income'].sum(), "Hours": wk_table['Hours'].sum(), 
                    "Miles": wk_table['Miles'].sum(), "Rate": "", 
                    "Mileage Shield": wk_table['Mileage Shield'].sum(), 
                    "Schedule C Profit": wk_table['Schedule C Profit'].sum(), "Notes": ""
                }])
                wk_table_display = pd.concat([wk_table, total_row_wk], ignore_index=True)
                
                st.dataframe(wk_table_display.style.format({
                    "Income": "${:,.2f}", "Miles": "{:,.1f}", "Hours": "{:,.1f}",
                    "Mileage Shield": "${:,.2f}", "Schedule C Profit": "${:,.2f}"
                }), use_container_width=True, hide_index=True)

        st.divider()

        st.subheader("🗂️ All entries this month")
        display_df = month_df[["id", "entry_date", "entry_type", "platform", "income", "hours_worked", "miles", "rate", "mileage_ded", "taxable_profit", "notes"]].rename(columns={
            "id": "ID", "entry_date": "Date", "entry_type": "Type", "platform": "Platform", "income": "Income", 
            "hours_worked": "Hours", "miles": "Miles", "rate": "Rate", 
            "mileage_ded": "Mileage Shield", "taxable_profit": "Schedule C Profit", "notes": "Notes"
        }).sort_values(["Date", "ID"], ascending=[False, False])

        # BUILD TABLE WITH TOTALS ROW FOR MONTHLY VIEW
        total_row_main = pd.DataFrame([{
            "ID": "", "Date": "TOTAL", "Type": "", "Platform": "", 
            "Income": display_df['Income'].sum(), "Hours": display_df['Hours'].sum(), 
            "Miles": display_df['Miles'].sum(), "Rate": "", 
            "Mileage Shield": display_df['Mileage Shield'].sum(), 
            "Schedule C Profit": display_df['Schedule C Profit'].sum(), "Notes": ""
        }])
        
        display_df_with_total = pd.concat([display_df, total_row_main], ignore_index=True)
        st.dataframe(display_df_with_total.style.format({
            "Income": "${:,.2f}", "Miles": "{:,.1f}", "Hours": "{:,.1f}",
            "Mileage Shield": "${:,.2f}", "Schedule C Profit": "${:,.2f}"
        }), use_container_width=True, hide_index=True)

        with st.expander("✏️ Edit or delete an entry from this month"):
            entry_ids = display_df["ID"].tolist()
            selected_entry_id = st.selectbox(
                "Select entry to edit or delete", options=entry_ids,
                format_func=lambda i: f"#{i} — {month_df.loc[month_df['id']==i, 'entry_date'].iloc[0]} | {month_df.loc[month_df['id']==i, 'entry_type'].iloc[0]} | ${month_df.loc[month_df['id']==i, 'income'].iloc[0]:.2f}",
            )
            erow = month_df.loc[month_df["id"] == selected_entry_id].iloc[0]

            with st.form("edit_entry_form"):
                ec1, ec2, ec3 = st.columns(3)
                ed_date = ec1.date_input("Date", value=erow["entry_date"], max_value=date.today())
                ed_type = ec2.selectbox("Type", ["Business", "Personal"], index=0 if erow["entry_type"]=="Business" else 1)
                
                if ed_type == "Business":
                    ed_plat = ec3.selectbox("Platform", PLATFORMS, index=PLATFORMS.index(erow["platform"]) if erow["platform"] in PLATFORMS else 0)
                else:
                    ed_plat = "N/A"
                    ec3.write("*(Personal Drive)*")
                
                ec4, ec5, ec6 = st.columns(3)
                ed_inc = ec4.number_input("Income ($)", value=float(erow["income"]), min_value=0.0, step=0.01, disabled=(ed_type=="Personal"))
                ed_hrs = ec5.number_input("Hours Worked", value=float(erow["hours_worked"]), min_value=0.0, step=0.1, disabled=(ed_type=="Personal"))
                ed_notes = st.text_input("Notes", value=erow["notes"] or "")

                ec7, ec8 = st.columns(2)
                ed_start = ec7.number_input("Odometer start", value=float(erow["odo_start"]), min_value=0.0, step=0.1)
                ed_end = ec8.number_input("Odometer end", value=float(erow["odo_end"]), min_value=0.0, step=0.1)

                b1, b2 = st.columns(2)
                save_entry_btn = b1.form_submit_button("💾 Save changes")
                del_entry_btn = b2.form_submit_button("🗑️ Delete this entry")

                if save_entry_btn:
                    if ed_end <= ed_start:
                        st.error("❌ End odometer must be greater than start odometer.")
                    else:
                        final_inc = float(ed_inc) if ed_type == "Business" else 0.0
                        final_hrs = float(ed_hrs) if ed_type == "Business" else 0.0
                        sb.table("entries").update({
                            "entry_date": ed_date.isoformat(), "entry_type": ed_type, "platform": ed_plat,
                            "income": final_inc, "hours_worked": final_hrs, "odo_start": float(ed_start), "odo_end": float(ed_end), "notes": ed_notes,
                        }).eq("id", int(selected_entry_id)).execute()
                        st.success(f"✅ Updated entry #{selected_entry_id}")
                        st.rerun()

                if del_entry_btn:
                    sb.table("entries").delete().eq("id", int(selected_entry_id)).execute()
                    st.success(f"🗑️ Deleted entry #{selected_entry_id}")
                    st.rerun()

        st.divider()
        st.subheader("🖨️ Export / Print")
        ec1, ec2 = st.columns(2)

        with ec1:
            csv_buf = io.StringIO()
            month_df[["entry_date", "entry_type", "week_start", "platform", "income", "hours_worked", "miles", "rate", "mileage_ded", "taxable_profit", "notes"]] \
                .rename(columns={"entry_date": "Date", "entry_type": "Type", "week_start": "Week Starting", "platform": "Platform", "income": "Income", "hours_worked": "Hours", "miles": "Miles", "rate": "IRS Rate", "mileage_ded": "Mileage Shield", "taxable_profit": "Schedule C Profit", "notes": "Notes"}) \
                .sort_values("Date").to_csv(csv_buf, index=False)
            st.download_button(label="⬇️ Download this month as CSV", data=csv_buf.getvalue(), file_name=f"gig_{picked_year}_{picked_month:02d}.csv", mime="text/csv")

        with ec2:
            if st.button("🖨️ Show printable report"):
                st.session_state["show_print"] = True

        if st.session_state.get("show_print"):
            st.markdown("---")
            st.markdown(f"### Monthly Income & Mileage Report — {picked_label}")
            st.markdown(
                f"**Gross income:** ${month_income:,.2f}  \n"
                f"**Net Biz Cash:** ${month_net_cash:,.2f}  \n"
                f"**Business miles:** {month_biz_miles:,.1f}  \n"
                f"**Personal miles:** {month_pers_miles:,.1f}  \n"
                f"**Mileage shield:** ${month_mileage:,.2f}  \n"
                f"**Schedule C profit:** ${month_taxable:,.2f}  \n"
                f"**Suggested tax set-aside:** ${month_tax_savings:,.2f}  \n"
                f"**Total Hours Worked:** {month_hours:,.1f} (${month_hourly:,.2f}/hr avg)"
            )
            st.caption("Press Ctrl+P (or Cmd+P on Mac) to print this report.")


# ================= TAB 3: MANAGE WRITE-OFFS =================
with tab_writeoffs:
    st.subheader("Your recurring write-offs")
    if not writeoffs.empty:
        st.dataframe(writeoffs[["id", "name", "amount", "frequency", "active"]], use_container_width=True, hide_index=True)
        st.divider()
        st.subheader("Edit a write-off")
        edit_id = st.selectbox("Choose a write-off to edit", options=writeoffs["id"].tolist(), format_func=lambda i: f"#{i} — {writeoffs.loc[writeoffs['id']==i,'name'].iloc[0]}")
        row = writeoffs.loc[writeoffs["id"] == edit_id].iloc[0]

        with st.form("edit_writeoff"):
            e1, e2, e3, e4 = st.columns(4)
            new_name = e1.text_input("Name", value=row["name"])
            new_amt = e2.number_input("Amount ($)", value=float(row["amount"]), step=0.01)
            new_freq = e3.selectbox("Frequency", ["monthly", "weekly"], index=0 if row["frequency"] == "monthly" else 1)
            new_active = e4.checkbox("Active", value=bool(row["active"]))
            save_edit = st.form_submit_button("💾 Save changes")

            if save_edit:
                sb.table("writeoffs").update({"name": new_name, "amount": float(new_amt), "frequency": new_freq, "active": bool(new_active)}).eq("id", int(edit_id)).execute()
                st.success(f"✅ Updated write-off #{edit_id}")
                st.rerun()

        st.divider()
        st.subheader("Delete a write-off")
        del_id = st.selectbox("Choose a write-off to delete permanently", options=writeoffs["id"].tolist(), format_func=lambda i: f"#{i} — {writeoffs.loc[writeoffs['id']==i,'name'].iloc[0]}", key="del_wo")
        if st.button("🗑️ Delete permanently"):
            sb.table("writeoffs").delete().eq("id", int(del_id)).execute()
            st.success(f"Deleted write-off #{del_id}")
            st.rerun()
    else:
        st.info("No write-offs yet. Add one below.")

    st.divider()
    st.subheader("Add new write-off")
    with st.form("add_writeoff", clear_on_submit=True):
        wcol1, wcol2, wcol3 = st.columns(3)
        wname = wcol1.text_input("Name", placeholder="e.g. Business Phone Line")
        wamount = wcol2.number_input("Amount ($)", min_value=0.0, step=0.01)
        wfreq = wcol3.selectbox("Frequency", ["monthly", "weekly"])
        wsub = st.form_submit_button("💾 Save write-off")
        if wsub and wname:
            sb.table("writeoffs").insert({"name": wname, "amount": float(wamount), "frequency": wfreq, "active": True}).execute()
            st.success(f"✅ Added {wname} — ${wamount:.2f}/{wfreq}")
            st.rerun()
