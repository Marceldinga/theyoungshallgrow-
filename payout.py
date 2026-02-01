
# 1) Build contributions df for that session (same session_id)
df_contrib, _meta = contributions_for_session(sb_service, int(res["session_id"]))

# 2) Get signatures for this session from your schema
sig_rows = get_signatures(sb_service, "payout", int(res["session_id"]))

# 3) Build PDF
pdf_bytes = build_payout_receipt_pdf(
    group_name="theyoungshallgrow",
    session_id=int(res["session_id"]),
    payout_day=(payout_day.isoformat() if payout_day else None),
    beneficiary_id=int(res["beneficiary_id"]),
    beneficiary_name=beneficiary_name,
    contributions_df=df_contrib,
    members_df=dfm,              # dfm already exists in render_payouts()
    signatures=sig_rows,
)

filename = f"payout_receipt
