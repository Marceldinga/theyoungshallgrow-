
# payout.py (minimal)
import streamlit as st

def render_payouts(sb_service, schema: str):
    st.header("Payouts")
    st.success("✅ payout.py connected successfully!")
    st.write("Schema:", schema)
