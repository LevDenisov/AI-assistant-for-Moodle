from __future__ import annotations
import streamlit as st
from config import APP_TITLE, AI_API_BASE, AI_API_KEY, PDF_DPI_DEFAULT, PDF_DPI_MIN, PDF_DPI_MAX
from ui.state import init_session_state, soft_refresh_jobs_and_results, auto_refresh_if_active
from ui.sections import add_task_ui, delete_confirmation_widget, submission_form, ai_and_teacher_blocks
from services.webhook_server import start_once as start_webhook

st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

start_webhook()
init_session_state()
soft_refresh_jobs_and_results()
auto_refresh_if_active()

with st.sidebar:
    st.header("Настройки")
    api_base = st.text_input("API base URL", value=AI_API_BASE, placeholder="http://host:port")
    api_key = st.text_input("API key", value=AI_API_KEY, type="password")
    dpi = st.slider("DPI рендера (PDF)", PDF_DPI_MIN, PDF_DPI_MAX, PDF_DPI_DEFAULT)

st.subheader("Задания")
c_add, _ = st.columns([1, 6])
with c_add:
    st.button("Добавить задание", use_container_width=True,
              on_click=lambda: st.session_state.update({"show_create": True}))

if st.session_state.show_create:
    add_task_ui()

if not st.session_state.tasks:
    st.caption("Пока нет заданий — создайте первое.")

for task in st.session_state.tasks:
    with st.container(border=True):
        head_l, head_r = st.columns([0.88, 0.12])
        with head_l:
            st.markdown(f"**Задание №{task['id']}** — {task['condition'] or '(без описания)'}")
        with head_r:
            if st.button("Удалить", key=f"delete_{task['id']}"):
                st.session_state.confirm_delete_task = task["id"]

        if st.session_state.get("confirm_delete_task") == task["id"]:
            delete_confirmation_widget(task["id"])

        with st.expander("Ответ ученика и оценки", expanded=not bool(st.session_state.submissions.get(task["id"]))):
            submission_form(task, dpi=dpi)
            ai_and_teacher_blocks(task)

    st.divider()
