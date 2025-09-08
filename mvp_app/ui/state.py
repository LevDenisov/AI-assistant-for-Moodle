from __future__ import annotations
import streamlit as st, time
from db import migrate
from repository import list_tasks, load_results, load_submissions, load_teacher_reviews, load_review_jobs

def init_session_state() -> None:
    if "db_initialized" not in st.session_state:
        migrate()
        st.session_state.tasks = [t.__dict__ for t in list_tasks()]
        st.session_state.results = load_results()
        st.session_state.submissions = load_submissions()
        st.session_state.teacher_reviews = load_teacher_reviews()
        st.session_state.review_jobs = load_review_jobs()
        st.session_state.db_initialized = True

    if "task_counter" not in st.session_state:
        if st.session_state.tasks:
            max_num = max(int(t["id"].replace("T", "")) for t in st.session_state.tasks if t["id"].startswith("T"))
            st.session_state.task_counter = max_num + 1
        else:
            st.session_state.task_counter = 1

    st.session_state.setdefault("show_create", False)
    st.session_state.setdefault("confirm_delete_task", None)

def soft_refresh_jobs_and_results() -> None:
    st.session_state.review_jobs = load_review_jobs()
    st.session_state.results = load_results()

def auto_refresh_if_active() -> None:
    has_active = any(j["status"] in ("queued", "processing")
                     for j in st.session_state.get("review_jobs", {}).values())
    if has_active:
        st.experimental_set_query_params(_=int(time.time()))
        st.markdown("<script>setTimeout(()=>window.location.reload(),3000);</script>", unsafe_allow_html=True)
