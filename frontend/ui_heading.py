"""Consistent page and section headings for Streamlit pages."""
import streamlit as st


def page_title(text):
    st.title(text)


def _space():
    st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)


def section(text):
    _space()
    st.header(text)


def subsection(text):
    _space()
    st.subheader(text)
