"""Shared Streamlit input widget for deal fields (custom and researched deals)."""

from __future__ import annotations

import streamlit as st

from deal_input import Field


def field_widget(field: Field, default, key: str, label: str | None = None,
                 fmt: str | None = None):
    """Text, whole-number, or decimal input for a deal field. `default` may be None
    for number fields, leaving the input empty until the user enters a value."""
    label = label or field.label
    if field.kind == "text":
        return st.text_input(label, value=default or "", key=key)
    if field.kind == "int":
        return st.number_input(label, value=None if default is None else int(default),
                               min_value=1, step=1, key=key)
    fmt = fmt or {"pct": "%.2f", "multiple": "%.2f"}.get(field.kind, "%.4f")
    return st.number_input(label, value=None if default is None else float(default), step=1.0,
                           format=fmt, key=key)
