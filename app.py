"""Streamlit entry point: retinal vessel calibre (AVR, CRAE, CRVE) and ACS severity (SYNTAX).

Run locally:  streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Retina → SYNTAX", page_icon="👁️", layout="centered")

pages = st.navigation([
    st.Page("views/predictor.py", title="Predictor", icon="🔬", default=True),
    st.Page("views/methodology.py", title="How we built it", icon="📖"),
])
pages.run()
