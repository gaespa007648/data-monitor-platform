"""Persist the access token in the browser across Streamlit server restarts."""
from pathlib import Path

import streamlit.components.v1 as components


_storage = components.declare_component(
    "auth_storage", path=str(Path(__file__).parent / "auth_storage_component"))


def browser_token(action: str, token: str | None = None):
    return _storage(action=action, token=token, key="browser_auth_storage", default=None)
