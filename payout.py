
# payout.py (diagnostic version)
import streamlit as st
# ✅ Robust import for payout module (works if payout.py is in root OR in panels/)
import importlib
import os
import sys
import streamlit as st

render_payouts = None
_import_errors = []

for modname in ("payout", "panels.payout", "src.payout"):
    try:
        mod = importlib.import_module(modname)
        render_payouts = getattr(mod, "render_payouts", None)
        if render_payouts is not None:
            break
        else:
            _import_errors.append(f"Imported {modname} but render_payouts not found.")
    except Exception as e:
        _import_errors.append(f"Failed importing {modname}: {e}")

if render_payouts is None:
    st.error("❌ Could not import render_payouts. Fix payout module path / function name.")
    st.write("Python sys.path:", sys.path)
    st.write("Files in app directory:", os.listdir(os.path.dirname(__file__)))
    st.write("Import attempts:", _import_errors)
    st.stop()

def render_payouts(sb_service, schema: str):
    st.header("Payouts")
    st.success("✅ payout.py imported successfully")
    st.write("Schema:", schema)
