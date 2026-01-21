# loans_ui.py
import streamlit as st
import pandas as pd
from loans_core import (
    fetch_member_loans,
    fetch_loan_payments,
    record_payment,
    confirm_payment,
)

def render_loans(sb, schema, actor_user_id="admin"):
    st.header("Loans (Organizational Standard)")

    # ----- KPI -----
    loans = sb.schema(schema).table("loans_legacy").select("*").execute().data or []
    df = pd.DataFrame(loans)
    active = df[df["status"] == "active"] if not df.empty else []

    c1, c2 = st.columns(2)
    c1.metric("Active loans", len(active))
    c2.metric("Total due", f"{active['total_due'].sum():,.0f}" if not df.empty else "0")

    st.divider()

    # ----- MOBILE SAFE MENU -----
    MENU = ["Requests", "Ledger", "Record Payment", "Confirm Payments"]
    section = st.selectbox("Loans menu", MENU)

    if section == "Ledger":
        st.dataframe(df, use_container_width=True)

    elif section == "Record Payment":
        loan_id = st.number_input("Loan ID", min_value=1)
        amt = st.number_input("Amount", min_value=0.0)
        if st.button("Record Payment"):
            record_payment(sb, loan_id, amt, st.date_input("Paid on"), actor_user_id)
            st.success("Payment recorded")

    elif section == "Confirm Payments":
        payments = sb.table("loan_payments").select("*").eq("status", "pending").execute().data or []
        st.dataframe(pd.DataFrame(payments))
