from __future__ import annotations
import os, time
from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st
from models import Task, Submission, ReviewJob
from repository import (
    upsert_task, delete_task, upsert_submission, upsert_teacher_review,
    upsert_review_job, load_results
)
from services.llm_client import call_orchestrator_async
from services.pdf_renderer import render_pdf_pages
from config import PUBLIC_CALLBACK_BASE, UPLOAD_DIR

def toast(msg: str) -> None:
    st.toast(msg)

def add_task_ui():
    st.info("Введите условие задания и нажмите «Добавить».")
    st.text_area("Условие задания", key="new_task_text", height=160)
    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("Добавить", type="primary"):
            text = (st.session_state.get("new_task_text") or "").strip()
            t_id = f"T{st.session_state.task_counter:04d}"
            task = Task(id=t_id, condition=text, created=int(time.time()))
            st.session_state.tasks.append(task.__dict__)
            st.session_state.task_counter += 1
            st.session_state.show_create = False
            st.session_state.pop("new_task_text", None)
            upsert_task(task)
            toast(f"Задание {t_id} добавлено")
    with c2:
        st.button("Отмена", on_click=lambda: st.session_state.update({"show_create": False, "new_task_text": None}))

def delete_confirmation_widget(tid: str):
    with st.container(border=True):
        st.warning(f"Удалить задание {tid}? Это действие необратимо.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Да, удалить", type="primary"):
                delete_task(tid)
                st.session_state.tasks = [t for t in st.session_state.tasks if t["id"] != tid]
                st.session_state.results.pop(tid, None)
                st.session_state.submissions.pop(tid, None)
                st.session_state.confirm_delete_task = None
                toast(f"Задание {tid} удалено")
                st.rerun()
        with c2:
            if st.button("Отмена"):
                st.session_state.confirm_delete_task = None
                st.rerun()

def criteria_df_block(title: str, rows: pd.DataFrame, key: str):
    rows = rows.copy()
    rows["passed"] = rows.get("passed", False).astype(bool)
    rows["name"] = rows.get("name", "").astype(str)
    rows["details"] = rows.get("details", "").astype(str)

    df_display = pd.DataFrame({
        "№": range(1, len(rows) + 1),
        "Описание критерия": rows["name"],
        "Оценка": [""] * len(rows),
        "Пояснение": rows["details"],
    })

    def _color(_):
        return ['background-color: #dcfce7' if p else 'background-color: #fee2e2' for p in rows["passed"]]

    st.subheader(title)
    st.dataframe(
        df_display.style.apply(_color, subset=["Оценка"]),
        width="stretch",
        hide_index=True,
        column_config={
            "№": st.column_config.NumberColumn("№", width=60),
            "Описание критерия": st.column_config.TextColumn("Описание критерия"),
            "Оценка": st.column_config.TextColumn("Оценка"),
            "Пояснение": st.column_config.TextColumn("Пояснение"),
        },
        key=key
    )
    return int(rows["passed"].sum())

def _build_file_on_disk(uploaded, task_id: str) -> Optional[str]:
    try:
        ts = int(time.time())
        fname = uploaded.name
        safe_name = f"{task_id}_{ts}_{fname}"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        path = os.path.join(UPLOAD_DIR, safe_name)
        with open(path, "wb") as out:
            out.write(uploaded.getbuffer())
        return safe_name
    except Exception:
        return None

def submission_form(task: Dict[str, Any], dpi: int):
    mode_key = f"input_mode_{task['id']}"
    st.session_state.setdefault(mode_key, "Файл")
    input_mode = st.radio("Способ загрузки решения", ["Файл", "Текст"], key=mode_key, horizontal=True)

    uploaded = None
    sol_text = ""

    if st.session_state.submissions.get(task["id"]):
        st.success("Решение отправлено. Ожидается результат от LLM.")
        sub = st.session_state.submissions[task["id"]]
        if sub["mode"] == "text":
            st.text_area("Отправленный текст", value=sub.get("text") or "", height=180, disabled=True)
        else:
            st.caption("Отправленный файл:")
            file_name = sub.get("file_name") or "file"
            file_path = sub.get("file_path")
            if file_path and os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    st.download_button("Скачать", data=f.read(), file_name=file_name)
            else:
                st.info(file_name)
        return

    if input_mode == "Файл":
        uploaded = st.file_uploader(f"Загрузите решение (PDF/изображение) для {task['id']}",
                                    type=["pdf", "png", "jpg", "jpeg"],
                                    key=f"file_{task['id']}")
        if uploaded and uploaded.type == "application/pdf":
            imgs = render_pdf_pages(uploaded.getvalue(), dpi=dpi)
            if imgs:
                st.image(imgs[0], caption="Превью 1-й страницы", use_container_width=True)
    else:
        sol_text = st.text_area(f"Введите текст решения для {task['id']}", key=f"text_{task['id']}", height=180)

    if st.button("Отправить решение", key=f"send_{task['id']}"):
        if input_mode == "Файл" and not uploaded:
            st.error("Пожалуйста, загрузите файл.")
            st.stop()
        if input_mode == "Текст" and not (sol_text and sol_text.strip()):
            st.error("Пожалуйста, введите текст.")
            st.stop()

        # Prepare payload (do NOT persist submission yet — to allow retry on failure)
        file_url = None
        tmp_saved_name = None
        if input_mode == "Файл":
            tmp_saved_name = _build_file_on_disk(uploaded, task["id"])
            if not tmp_saved_name:
                st.error("Не удалось сохранить файл на сервере.")
                st.stop()
            file_url = f"{PUBLIC_CALLBACK_BASE.rstrip('/')}/uploads/{tmp_saved_name}"

        payload = {
            "submission_id": f"{task['id']}-submission",
            "task_id": task["id"],
            "task_text": task.get("condition", ""),
            "mode": ("text" if input_mode == "Текст" else "file"),
            "text": (sol_text or "") if input_mode == "Текст" else "",
            "file_url": file_url,
            "callback_url": f"{PUBLIC_CALLBACK_BASE.rstrip('/')}/callback",
        }

        ok, err = call_orchestrator_async(payload)
        if not ok:
            # On failure: keep the form enabled for retry and clean up temp file
            if tmp_saved_name:
                try:
                    os.remove(os.path.join(UPLOAD_DIR, tmp_saved_name))
                except Exception:
                    pass
            st.error(f"Не удалось связаться с LLM: {err}")
            st.info("Попробуйте отправить ещё раз.")
            st.stop()

        # Success: now persist submission and create job so UI locks the form
        if input_mode == "Текст":
            sub = Submission(task_id=task["id"], mode="text", text=(sol_text or "").strip(),
                             file_path=None, file_name=None, uploaded_at=int(time.time()))
        else:
            abs_path = os.path.join(UPLOAD_DIR, tmp_saved_name)
            sub = Submission(task_id=task["id"], mode="file", text=None, file_path=abs_path,
                             file_name=uploaded.name, uploaded_at=int(time.time()))
        upsert_submission(sub)
        st.session_state.submissions[task["id"]] = sub.__dict__

        submission_id = payload["submission_id"]
        job = ReviewJob(submission_id, task["id"], "queued", None, None, int(time.time()), int(time.time()))
        upsert_review_job(job)
        st.session_state.review_jobs[submission_id] = job.__dict__

        st.success("Решение отправлено на проверку. Ожидаем результат по webhook.")
        st.rerun()

def ai_and_teacher_blocks(task: Dict[str, Any]):
    submission_id = f"{task['id']}-submission"
    job = st.session_state.review_jobs.get(submission_id)
    if job and job["status"] in ("queued", "processing"):
        st.info("Оценка запущена на внешнем LLM. Ожидаем результат по webhook…")
    elif job and job["status"] == "error":
        st.error("Не удалось получить результат от LLM. Попробуйте ещё раз.")

    # Refresh results inline
    st.session_state.results = load_results()
    data = st.session_state.results.get(task['id'])
    if not data:
        return

    if "criteria" in data and data["criteria"]:
        passed_count = criteria_df_block("Оценка AI:", pd.DataFrame(data["criteria"]),
                                         key=f"df_ai_{task['id']}")
        ai_total = min(int(passed_count), 10)
        st.metric("Итоговая оценка AI", f"{ai_total} / 10")
    else:
        st.info("Критерии отсутствуют.")

    st.subheader("Оценка преподавателя:")
    teacher = st.session_state.teacher_reviews.get(task["id"])
    if data.get("criteria"):
        base = pd.DataFrame(data["criteria"])
        base["name"] = base.get("name", "").astype(str)
        base["details"] = base.get("details", "").astype(str)

        if teacher:
            traw = pd.DataFrame(teacher["criteria"])
            traw["passed"] = traw.get("passed", False).astype(bool)
            criteria_df_block("Критерии преподавателя:", traw, key=f"df_teacher_{task['id']}")
            st.metric("Итоговая оценка преподавателя", f"{teacher['total']} / 10")
        else:
            st.caption("Отметьте статус и (опционально) добавьте пояснение к каждому критерию.")
            teacher_inputs: List[Dict[str, Any]] = []
            for i, row in base.reset_index(drop=True).iterrows():
                c1, c2, c3, c4 = st.columns([0.07, 0.38, 0.25, 0.30])
                with c1: st.markdown(f"**{i+1}**")
                with c2: st.markdown(row["name"] or "")
                with c3:
                    status_val = st.radio("Статус", ["Выполнено", "Не выполнено"],
                                          horizontal=True, key=f"teach_radio_{task['id']}_{i}",
                                          label_visibility="collapsed")
                with c4:
                    note_val = st.text_input("Пояснение", value=row.get("details", "") or "",
                                             key=f"teach_note_{task['id']}_{i}",
                                             placeholder="Комментарий (необязательно)",
                                             label_visibility="collapsed")
                teacher_inputs.append({"name": row["name"],
                                       "passed": (status_val == "Выполнено"),
                                       "details": (note_val or "").strip()})
            if st.button("Сохранить оценку преподавателя", type="primary", key=f"save_teacher_{task['id']}"):
                total = upsert_teacher_review(task["id"], teacher_inputs)
                st.session_state.teacher_reviews[task["id"]] = {
                    "criteria": teacher_inputs, "total": total, "updated": int(time.time())
                }
                st.success("Оценка преподавателя сохранена.")
                st.rerun()
