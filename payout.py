
# payout.py (diagnostic version)
import streamlit as st

def render_payouts(sb_service, schema: str):
    st.header("Payouts")
    st.success("✅ payout.py imported successfully")
    st.write("Schema:", schema)
