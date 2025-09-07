from __future__ import annotations

import io
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from PIL import Image, ImageDraw

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

APP_TITLE = "AI‑ассистент для Moodle"
DEFAULT_API_BASE = os.getenv("AI_API_BASE_URL", "")
DEFAULT_API_KEY = os.getenv("AI_API_KEY", "")
REQUEST_TIMEOUT = 60

# ----------------------------- Состояние ---------------------------------------
if "tasks" not in st.session_state:
    st.session_state.tasks: List[Dict[str, Any]] = []
if "task_counter" not in st.session_state:
    st.session_state.task_counter = 1
if "show_create" not in st.session_state:
    st.session_state.show_create = False
if "results" not in st.session_state:
    st.session_state.results: Dict[str, Dict[str, Any]] = {}

# NEW: коллбэки для открытия/добавления/отмены
def _open_create():
    st.session_state.show_create = True

def _cancel_create():
    st.session_state.show_create = False
    st.session_state.pop("new_task_text", None)

def _add_task():
    text = (st.session_state.get("new_task_text") or "").strip()
    t_id = f"T{st.session_state.task_counter:04d}"
    st.session_state.tasks.append({
        "id": t_id,
        "condition": text,
        "created": int(time.time()),
    })
    st.session_state.task_counter += 1
    # закрываем окно и чистим поле
    st.session_state.show_create = False
    st.session_state.pop("new_task_text", None)
    st.toast(f"Задание {t_id} добавлено")   # моментальное всплывающее сообщение
# ----------------------------- Утилиты -----------------------------------------

def _render_pdf_pages(file_bytes: bytes, dpi: int = 150) -> List[Image.Image]:
    if fitz is None:
        return []
    pages: List[Image.Image] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            pages.append(img)
    return pages


def _overlay_regions(img: Image.Image, regions: List[Dict[str, Any]]) -> Image.Image:
    if not regions:
        return img
    overlay = img.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    w, h = img.size
    for r in regions:
        x = int(r.get("x", 0) * w)
        y = int(r.get("y", 0) * h)
        rw = int(r.get("w", 0) * w)
        rh = int(r.get("h", 0) * h)
        draw.rectangle([x, y, x + rw, y + rh], outline=(255, 0, 0, 255), width=3)
        draw.rectangle([x, y, x + rw, y + rh], fill=(255, 0, 0, 60))
    return overlay


def call_orchestrator(api_base: str, api_key: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    url = api_base.rstrip("/") + "/v1/reviews"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 400:
            return {}, f"HTTP {resp.status_code}: {resp.text[:300]}"
        return resp.json(), None
    except Exception as e:
        return {}, str(e)


def mock_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    return {
        "comment": (
            f"Черновой отчёт (мок). task={payload.get('task','(нет)')} ts={now}"
            f"text_length={len((payload.get('text') or ''))}"
        ),
        "score": 80,
        "criteria": [
            {"id": "c1", "name": "Структура", "passed": True, "details": "Есть введение/выводы", "weight": 0.4, "score": 0.38},
            {"id": "c2", "name": "Оформление", "passed": False, "details": "Не по ГОСТ", "weight": 0.3, "score": 0.15},
        ],
        "checks": [
            {"name": "Шрифт ≥ 12pt", "ok": True, "message": "ОК"},
            {"name": "Ссылки оформлены", "ok": False, "message": "Нет единообразия"},
        ],
        "structure": ["Титульный лист", "Введение", "Основная часть", "Выводы"],
        "regions": [],
    }
# ----------------------------- UI ---------------------------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

with st.sidebar:
    st.header("Настройки")
    mode = st.radio("Режим", ["Мок", "API"], index=0)
    api_base = st.text_input("API base URL", value=DEFAULT_API_BASE, placeholder="http://host:port")
    api_key = st.text_input("API key", value=DEFAULT_API_KEY, type="password")
    dpi = st.slider("DPI рендера (PDF)", 72, 220, 150)

# ----------------------------- Раздел: Задания --------------------------------
st.subheader("Задания")
col_add, col_sp = st.columns([1, 6])
with col_add:
    st.button("Добавить задание", use_container_width=True, on_click=_open_create)

if st.session_state.show_create:
    st.info("Введите условие задания и нажмите «Добавить».")
    # new_task_text = st.text_area("Условие задания", key="new_task_text", height=160)
    st.text_area("Условие задания", key="new_task_text", height=160)
    c1, c2 = st.columns([1,1])
    with c1:
        st.button("Добавить", type="primary", on_click=_add_task)
    with c2:
        st.button("Отмена", on_click=_cancel_create)

if not st.session_state.tasks:
    st.caption("Пока нет заданий — создайте первое.")

# # Рендер заданий
# for task in st.session_state.tasks:
#     with st.container(border=True):
#         st.markdown(f"**Задание №{task['id']}** — {task['condition'] or '(без описания)'}")
#         # Поля для решения
#         up_col, tx_col = st.columns([1,1])
#         with up_col:
#             uploaded = st.file_uploader(
#                 f"Загрузите решение (PDF/изображение) для {task['id']}",
#                 type=["pdf", "png", "jpg", "jpeg"],
#                 key=f"file_{task['id']}"
#             )
#         with tx_col:
#             sol_text = st.text_area(
#                 f"Или введите текст решения для {task['id']}",
#                 key=f"text_{task['id']}", height=140
#             )

#         # Кнопка отправки решения
#         if st.button(f"Отправить решение для {task['id']}", key=f"send_{task['id']}"):
#             if not uploaded and not (sol_text and sol_text.strip()):
#                 st.error("Введите текст или загрузите файл")
#             else:
#                 payload = {
#                     "submission_id": f"{task['id']}-submission",
#                     "task": task.get("condition", ""),
#                     "text": (sol_text or "").strip(),
#                 }
#                 if mode == "API":
#                     if not api_base:
#                         st.error("Укажите API base URL или переключитесь в режим Мок")
#                     else:
#                         data, err = call_orchestrator(api_base, api_key, payload)
#                         if err:
#                             st.error(f"Ошибка API: {err}")
#                         else:
#                             st.session_state.results[task['id']] = data
#                 else:
#                     data = mock_response(payload)
#                     st.session_state.results[task['id']] = data

#         # Вывод результата по заданию (если есть)
#         data = st.session_state.results.get(task['id'])
#         if data:
#             st.markdown("**Результат анализа:**")
#             st.text_area("Комментарий", value=data.get("comment", ""), height=160, disabled=True, key=f"out_{task['id']}")
#             if "criteria" in data:
#                 df = pd.DataFrame(data["criteria"])
#                 st.dataframe(df, use_container_width=True, key=f"df_{task['id']}")
#             if "score" in data:
#                 st.metric("Итоговый балл", f"{data['score']}")

# Рендер заданий
for task in st.session_state.tasks:
    with st.container(border=True):
        st.markdown(f"**Задание №{task['id']}** — {task['condition'] or '(без описания)'}")

        # --- выбор способа сдачи (переключаемый UI) ---
        mode_key = f"input_mode_{task['id']}"
        if mode_key not in st.session_state:
            st.session_state[mode_key] = "Файл"  # значение по умолчанию

        input_mode = st.radio(
            "Способ загрузки решения",
            ["Файл", "Текст"],
            key=mode_key,
            horizontal=True,
            help="Выберите, как хотите сдать решение для этого задания",
        )

        uploaded = None
        sol_text = ""

        if input_mode == "Файл":
            # один виджет на всю ширину
            uploaded = st.file_uploader(
                f"Загрузите решение (PDF/изображение) для {task['id']}",
                type=["pdf", "png", "jpg", "jpeg"],
                key=f"file_{task['id']}"
            )
        else:
            sol_text = st.text_area(
                f"Введите текст решения для {task['id']}",
                key=f"text_{task['id']}", height=180
            )

        # Кнопка отправки решения
        if st.button(f"Отправить решение для {task['id']}", key=f"send_{task['id']}"):
            # валидация согласно выбранному режиму
            if input_mode == "Файл" and not uploaded:
                st.error("Пожалуйста, загрузите файл решения.")
            elif input_mode == "Текст" and not (sol_text and sol_text.strip()):
                st.error("Пожалуйста, введите текст решения.")
            else:
                payload = {
                    "submission_id": f"{task['id']}-submission",
                    "task": task.get("condition", ""),
                    "text": (sol_text or "").strip() if input_mode == "Текст" else "",
                    # Для режима 'Файл' здесь обычно передают ссылку на файл в S3/MinIO.
                    # В этом MVP бинарь не отправляем; интеграцию добавим на backend.
                }

                if mode == "API":
                    if not api_base:
                        st.error("Укажите API base URL или переключитесь в режим Мок")
                    else:
                        data, err = call_orchestrator(api_base, api_key, payload)
                        if err:
                            st.error(f"Ошибка API: {err}")
                        else:
                            st.session_state.results[task['id']] = data
                else:
                    data = mock_response(payload)
                    st.session_state.results[task['id']] = data

        # Вывод результата по заданию (если есть)
        data = st.session_state.results.get(task['id'])
        if data:
            st.markdown("**Результат анализа:**")
            st.text_area("Комментарий", value=data.get("comment", ""), height=160, disabled=True, key=f"out_{task['id']}")
            if "criteria" in data:
                df = pd.DataFrame(data["criteria"])
                st.dataframe(df, use_container_width=True, key=f"df_{task['id']}")
            if "score" in data:
                st.metric("Итоговый балл", f"{data['score']}")
