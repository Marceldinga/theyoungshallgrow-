
elif page == "Payouts":
    if not sb_service:
        st.warning("Service key not configured. Add SUPABASE_SERVICE_KEY in secrets.")
    else:
        render_payouts(sb_service, SUPABASE_SCHEMA)
