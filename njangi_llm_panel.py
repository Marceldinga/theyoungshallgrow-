# njangi_llm_panel.py
# ================================================
# NJANGI AI ASSISTANT PANEL (Lightweight Version)
# No external LLM required
# Safe for Streamlit Cloud
# ================================================

from __future__ import annotations
import streamlit as st
import pandas as pd


def render_llm_panel(client):
    st.title("🤖 NJANGI AI Assistant")

    st.info("This is a lightweight AI helper panel for insights and summaries.")

    question = st.text_area("Ask something about your Njangi system")

    if st.button("Analyze"):
        if not question.strip():
            st.warning("Please enter a question.")
            return

        # Example simple intelligence
        if "risk" in question.lower():
            st.success("AI Insight: Members with overdue loans and unpaid interest are high risk.")

        elif "contribution" in question.lower():
            st.success("AI Insight: Consistent contribution frequency improves payout reliability.")

        elif "loan" in question.lower():
            st.success("AI Insight: Monitor principal_current and unpaid_interest closely.")

        else:
            st.write("AI Assistant Response:")
            st.write("This version runs locally without OpenAI. You can upgrade later.")
