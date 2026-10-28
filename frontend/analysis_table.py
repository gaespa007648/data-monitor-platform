"""Small bidirectional table for dataset analysis actions."""
from pathlib import Path

import streamlit.components.v1 as components


_table = components.declare_component(
    "dataset_analysis_table", path=str(Path(__file__).parent / "analysis_table_component"))


def render_table(dataset_id, columns, rows, sort_by, sort_dir):
    return _table(dataset_id=dataset_id, columns=columns, rows=rows,
                  sort_by=sort_by, sort_dir=sort_dir,
                  key=f"dataset_analysis_{dataset_id}", default=None)
