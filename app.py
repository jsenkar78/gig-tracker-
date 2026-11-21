import streamlit as st
import pandas as pd
from datetime import date, datetime
from calendar import monthrange
from supabase import create_client, ClientOptions
import io

# Connect to Supabase (forces standard public schema for new API keys)
sb = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"],
    options=ClientOptions(schema="public", auto_refresh_token=False, persist_session=False)
)

MILEAGE_RATE = 0.72
PLATFORMS = ["DoorDash", "Uber", "Other"]

st.set_page_config(page_title="Gig Tracker", page_icon="🚗", layout="wide")
st.title("🚗 Gig Income & Mileage Tracker")


# ---------------- Helpers ----------------
def week_of_day(d: date) -> int:
    return ((d.day - 1) // 7) + 1


def load_entries():
    res = sb.table("entries").select("*").order("entry_date").execute()
    df = pd.DataFrame(res.data)
    if df.empty:
        return df
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["week"] = df["entry_date"].apply(week_of_day)
    df["mileage_ded"] = df["miles"] * MILEAGE_RATE
    df["net"] = df["income"] - df["mileage_ded"]
    return df


def load_writeoffs():
    res = sb.table("writeoffs").select("*").order("id").execute()
    return pd.DataFrame(res.data)


def recurring_total(writeoffs: pd.DataFrame, weeks: float, months: float) -> float:
    if writeoffs.empty:
        return 0.0
    active = writeoffs[writeoffs["active"] == True]
    total = 0.0
    for _, w in active.iterrows():
        total += float(w["amount"]) * (months if w["frequency"] == "monthly" else weeks)
    return total


def per_platform(df: pd.DataFrame):
    out = {}
    for p in PLATFORMS:
        sub = df[df["platform"] == p]
        out[p] = {"income": sub["income"].sum(), "miles": sub["miles"].sum()}
    return out


# ---------------- Tabs ----------------
tab_entry, tab_view, tab_writeoffs = st.tabs(
    ["➕ Add Entry", "📊 View / Weekly Breakdown", "⚙️ Recurring Write-offs"]
)


# ================= TAB 1: ADD ENTRY =================
with tab_entry:
    st.subheader("Add a daily entry")
    st.caption("Enter odometer start and end — miles are calculated for you. "
               "Doing both Uber and DoorDash the same day? Make one entry per platform.")

    with st.form("add_entry", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            entry_date = st.date_input("Date", value=date.today(), max_value=date.today())
            platform = st.selectbox("Platform", PLATFORMS)
            income = st.number_input("Income ($)", min_value=0.0, step=0.01)
        with col2:
            odo_start = st.number_input("Odometer start", min_value=0.0, step=0.1, value=0.0)
            odo_end = st.number_input("Odometer end", min_value=0.0, step=0.1, value=0.0)
        notes = st.text_input("Notes (optional)")
        submitted = st.form_submit_button("💾 Save")

        if submitted:
            if entry_date > date.today():
                st.error(
                    f"❌ Date {entry_date} is in the future. "
                    f"Today is {date.today()}. Pick today or an earlier date."
                )
            elif odo_end < odo_start:
                st.error(f"❌ End odometer ({odo_end:,.1f}) is less than start ({odo_start:,.1f}). "
                         f"Check your numbers.")
            elif odo_end == odo_start:
                st.error("❌ Odometer start and end are identical — miles would be 0. "
                         "Check your numbers.")
            else:
                try:
                    sb.table("entries").insert({
                        "entry_date": entry_date.isoformat(),
                        "platform": platform,
                        "income": float(income),
                        "odo_start": float(odo_start),
                        "odo_end": float(odo_end),
                        "notes": notes,
                    }).execute()
                    miles = odo_end - odo_start
                    st.success(
                        f"✅ Saved {entry_date} | {platform} | ${income:.2f} income | "
                        f"{miles:.1f} mi ({odo_start:,.1f} → {odo_end:,.1f}) | "
                        f"deduction ${miles * MILEAGE_RATE:.2f}"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")


# ================= TAB 2: VIEW / BREAKDOWN =================
with tab_view:
    entries = load_entries()
    writeoffs = load_writeoffs()

    if entries.empty:
        st.info("No entries yet. Add one in the ➕ tab.")
    else:
        months_available = sorted(
            {(e.year, e.month) for e in entries["entry_date"]},
            reverse=True,
        )
        month_labels = [datetime(y, m, 1).strftime("%B %Y") for y, m in months_available]
        picked_label = st.selectbox("Select month", month_labels, index=0)
        picked_year, picked_month = months_available[month_labels.index(picked_label)]

        days_in_month = monthrange(picked_year, picked_month)[1]
        month_df = entries[
            (entries["entry_date"].apply(lambda d: d.year) == picked_year)
            & (entries["entry_date"].apply(lambda d: d.month) == picked_month)
        ].copy()

        month_income = month_df["income"].sum()
        month_miles = month_df["miles"].sum()
        month_mileage = month_miles * MILEAGE_RATE
        month_rec = recurring_total(writeoffs, weeks=days_in_month / 7, months=1)
        month_agi = month_income - month_mileage - month_rec

        st.subheader(f"📅 {picked_label} — Monthly Total")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Gross Income", f"${month_income:,.2f}")
        m2.metric("Miles", f"{month_miles:,.1f}")
        m3.metric("Mileage Deduction", f"${month_mileage:,.2f}")
        m4.metric("Other Write-offs", f"${month_rec:,.2f}")
        m5.metric("Adjusted Gross Income", f"${month_agi:,.2f}")

        st.subheader("🚙 Income by platform (this month)")
        pp = per_platform(month_df)
        pc1, pc2, pc3 = st.columns(3)
        for col, p in zip([pc1, pc2, pc3], PLATFORMS):
            col.metric(f"{p}", f"${pp[p]['income']:,.2f}", f"{pp[p]['miles']:,.1f} mi")

        st.divider()

        st.subheader("📆 Week-by-week breakdown (click a week to expand)")
        weeks_present = sorted(month_df["week"].unique())
        run_income = run_net = 0.0

        for wk in weeks_present:
            wk_df = month_df[month_df["week"] == wk]
            wk_income = wk_df["income"].sum()
            wk_miles = wk_df["miles"].sum()
            wk_mileage = wk_miles * MILEAGE_RATE
            wk_rec = recurring_total(writeoffs, weeks=1, months=1/4.333)
            wk_net = wk_income - wk_mileage - wk_rec

            run_income += wk_income
            run_net += wk_net

            start_day = (wk - 1) * 7 + 1
            end_day = min(wk * 7, days_in_month)

            with st.expander(
                f"**Week {wk}** (days {start_day}–{end_day}) "
                f"— Income ${wk_income:.2f} | Net ${wk_net:.2f}"
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Income", f"${wk_income:,.2f}")
                c2.metric("Miles", f"{wk_miles:,.1f}")
                c3.metric("Mileage Deduction", f"${wk_mileage:,.2f}")
                c4.metric("Net", f"${wk_net:,.2f}")

                st.caption(
                    f"📈 **Running total through Week {wk}:** "
                    f"Income ${run_income:,.2f} | Net ${run_net:,.2f}"
                )

                wp = per_platform(wk_df)
                st.write("**By platform this week:**")
                pcols = st.columns(3)
                for col, p in zip(pcols, PLATFORMS):
                    col.metric(p, f"${wp[p]['income']:,.2f}", f"{wp[p]['miles']:,.1f} mi")

                st.dataframe(
                    wk_df[["entry_date", "platform", "income",
                           "odo_start", "odo_end", "miles",
                           "mileage_ded", "net", "notes"]]
                    .rename(columns={
                        "entry_date": "Date", "platform": "Platform",
                        "income": "Income", "odo_start": "Odo Start",
                        "odo_end": "Odo End", "miles": "Miles",
                        "mileage_ded": "Mileage Ded", "net": "Net", "notes": "Notes",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

        st.divider()

        st.subheader("🗂️ All entries this month")
        st.dataframe(
            month_df[["entry_date", "week", "platform", "income",
                      "odo_start", "odo_end", "miles", "mileage_ded", "net", "notes"]]
            .rename(columns={
                "entry_date": "Date", "week": "Week", "platform": "Platform",
                "income": "Income", "odo_start": "Odo Start", "odo_end": "Odo End",
                "miles": "Miles", "mileage_ded": "Mileage Ded", "net": "Net",
                "notes": "Notes",
            })
            .sort_values("Date", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.subheader("🖨️ Export / Print")
        ec1, ec2 = st.columns(2)

        with ec1:
            csv_buf = io.StringIO()
            month_df[["entry_date", "week", "platform", "income",
                      "odo_start", "odo_end", "miles", "mileage_ded", "net", "notes"]] \
                .rename(columns={
                    "entry_date": "Date", "week": "Week", "platform": "Platform",
                    "income": "Income", "odo_start": "Odo Start", "odo_end": "Odo End",
                    "miles": "Miles", "mileage_ded": "Mileage Ded", "net": "Net",
                    "notes": "Notes",
                }) \
                .sort_values("Date") \
                .to_csv(csv_buf, index=False)
            st.download_button(
                label="⬇️ Download this month as CSV",
                data=csv_buf.getvalue(),
                file_name=f"gig_{picked_year}_{picked_month:02d}.csv",
                mime="text/csv",
            )

        with ec2:
            if st.button("🖨️ Show printable report"):
                st.session_state["show_print"] = True

        if st.session_state.get("show_print"):
            st.markdown("---")
            st.markdown(f"### Monthly Income Report — {picked_label}")
            st.markdown(
                f"**Total income:** ${month_income:,.2f}  \n"
                f"**Total miles:** {month_miles:,.1f}  \n"
                f"**Mileage deduction:** ${month_mileage:,.2f}  \n"
                f"**Other write-offs:** ${month_rec:,.2f}  \n"
                f"**Adjusted gross income:** ${month_agi:,.2f}"
            )
            st.markdown("#### Week-by-week")
            wk_summary = month_df.groupby("week").agg(
                income=("income", "sum"),
                miles=("miles", "sum"),
            ).reset_index()
            wk_summary["mileage_ded"] = wk_summary["miles"] * MILEAGE_RATE
            wk_summary["net"] = wk_summary["income"] - wk_summary["mileage_ded"]
            st.table(wk_summary.rename(columns={
                "week": "Week", "income": "Income", "miles": "Miles",
                "mileage_ded": "Mileage Ded", "net": "Net",
            }).round(2))
            st.markdown("#### Platform totals")
            plat_summary = pd.DataFrame([
                {"Platform": p, "Income": pp[p]["income"], "Miles": pp[p]["miles"]}
                for p in PLATFORMS
            ])
            st.table(plat_summary.round(2))
            st.caption("Press Ctrl+P (or Cmd+P on Mac) to print this report.")


# ================= TAB 3: MANAGE WRITE-OFFS =================
with tab_writeoffs:
    st.subheader("Your recurring write-offs")
    st.caption("Edit amounts when your phone bill changes. Turn off (deactivate) when you cancel something. "
               "Inactive write-offs are excluded from all future calculations but their history stays.")

    writeoffs = load_writeoffs()

    if not writeoffs.empty:
        st.dataframe(writeoffs[["id", "name", "amount", "frequency", "active"]],
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Edit a write-off")
        edit_id = st.selectbox(
            "Choose a write-off to edit",
            options=writeoffs["id"].tolist(),
            format_func=lambda i: f"#{i} — {writeoffs.loc[writeoffs['id']==i,'name'].iloc[0]}",
        )
        row = writeoffs.loc[writeoffs["id"] == edit_id].iloc[0]

        with st.form("edit_writeoff"):
            e1, e2, e3, e4 = st.columns(4)
            new_name = e1.text_input("Name", value=row["name"])
            new_amt = e2.number_input("Amount ($)", value=float(row["amount"]), step=0.01)
            new_freq = e3.selectbox(
                "Frequency", ["monthly", "weekly"],
                index=0 if row["frequency"] == "monthly" else 1,
            )
            new_active = e4.checkbox("Active", value=bool(row["active"]))
            save_edit = st.form_submit_button("💾 Save changes")

            if save_edit:
                sb.table("writeoffs").update({
                    "name": new_name,
                    "amount": float(new_amt),
                    "frequency": new_freq,
                    "active": bool(new_active),
                }).eq("id", int(edit_id)).execute()
                st.success(f"✅ Updated write-off #{edit_id}")
                st.rerun()

        st.divider()
        st.subheader("Delete a write-off")
        del_id = st.selectbox(
            "Choose a write-off to delete permanently",
            options=writeoffs["id"].tolist(),
            format_func=lambda i: f"#{i} — {writeoffs.loc[writeoffs['id']==i,'name'].iloc[0]}",
            key="del_wo",
        )
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
        wname = wcol1.text_input("Name", placeholder="e.g. Gridwise")
        wamount = wcol2.number_input("Amount ($)", min_value=0.0, step=0.01)
        wfreq = wcol3.selectbox("Frequency", ["monthly", "weekly"])
        wsub = st.form_submit_button("💾 Save write-off")
        if wsub and wname:
            sb.table("writeoffs").insert({
                "name": wname, "amount": float(wamount),
                "frequency": wfreq, "active": True,
            }).execute()
            st.success(f"✅ Added {wname} — ${wamount:.2f}/{wfreq}")
            st.rerun()


# ================= SIDEBAR: DELETE ENTRY =================
with st.sidebar:
    st.header("🗑️ Delete an entry")
    st.caption("Find the ID in the month table or Supabase dashboard.")
    del_entry_id = st.number_input("Entry ID", min_value=1, step=1, key="del_e")
    if st.button("Delete entry"):
        try:
            sb.table("entries").delete().eq("id", int(del_entry_id)).execute()
            st.success(f"Deleted entry #{del_entry_id}")
        except Exception as e:
            st.error(str(e))
