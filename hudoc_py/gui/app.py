"""Streamlit dashboard for echr-py.

Run via ``echr-py gui`` (which shells out to ``streamlit run`` on this file),
or directly with ``streamlit run hudoc_py/gui/app.py``.

Tabs:
* **Search** – live HUDOC search / smart-fetch.
* **Local** – search + browse a corpus directory built with ``corpus build``.
* **Network** – validated citation graph from authoritative resolution artifacts.

Pipeline functions are imported and called in-process (no subprocess).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def main() -> None:  # pragma: no cover - requires a running Streamlit server
    import streamlit as st

    st.set_page_config(page_title="echr-py", layout="wide")
    st.title("echr-py")

    data_dir_input = st.sidebar.text_input("Corpus directory", value="")
    data_dir: str | None = data_dir_input.strip() or None

    tab_search, tab_local, tab_network = st.tabs(["Search", "Local corpus", "Network"])

    # --- Live search ---------------------------------------------------------
    with tab_search:
        st.subheader("Live HUDOC search")
        col1, col2, col3 = st.columns(3)
        article = col1.text_input("Article", "")
        respondent = col2.text_input("Respondent (ISO)", "")
        text = col3.text_input("Full-text", "")
        limit = st.slider("Limit", 5, 200, 25)
        if st.button("Search", key="live_search"):
            from hudoc_py import search

            filters: dict[str, Any] = {
                k: v
                for k, v in {"article": article, "respondent": respondent, "text": text}.items()
                if v
            }
            with st.spinner("Querying HUDOC…"):
                cases = search(limit=limit, **filters)
            st.caption(f"{len(cases)} of {cases.result_count} matches")
            st.dataframe(cases.to_dataframe())

    # --- Local corpus --------------------------------------------------------
    with tab_local:
        st.subheader("Local corpus")
        if not data_dir:
            st.info("Set a corpus directory in the sidebar.")
        else:
            from hudoc_py.local import available_tables, run_search

            tables = available_tables(data_dir)
            st.write("Tables:", ", ".join(n for n, _f, _p in tables) or "(none)")
            mode = st.selectbox("Mode", ["text", "party", "list"])
            query = st.text_input("Query", "")
            if st.button("Search local", key="local_search") and query:
                out = run_search(data_dir, mode, query, fmt="json")
                import json

                st.dataframe(json.loads(out))

    # --- Network -------------------------------------------------------------
    with tab_network:
        st.subheader("Citation network")
        resolution_dir = st.text_input("Citation resolution directory", "")
        if st.button("Build network", key="network") and resolution_dir:
            from hudoc_py.citations import (
                CitationGraph,
                IncompleteCitationResolutionError,
            )

            p = Path(resolution_dir)
            try:
                graph = CitationGraph.from_artifacts(p, require_complete=True)
                html_path = p / "network.html"
                graph.to_html(str(html_path))
                st.components.v1.html(html_path.read_text(encoding="utf-8"), height=600)
            except IncompleteCitationResolutionError as exc:
                st.error(f"Network metrics are blocked until citation review is complete: {exc}")


if __name__ == "__main__":  # pragma: no cover
    main()
