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

import sqlite3
from contextlib import closing

from flask import Flask, request, jsonify
import threading

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

APP_TITLE = "AI-ассистент для Moodle"
DEFAULT_API_BASE = os.getenv("AI_API_BASE_URL", "")
DEFAULT_API_KEY = os.getenv("AI_API_KEY", "")
DB_PATH = os.getenv("APP_DB_PATH", "mvp_state.db")
REQUEST_TIMEOUT = 60
UPLOAD_DIR = os.getenv("APP_UPLOAD_DIR", "uploads")
LLM_API_URL = os.getenv("LLM_API_URL", "http://llm-host:8000")
PUBLIC_CALLBACK_BASE = os.getenv("PUBLIC_CALLBACK_BASE", "http://your-host:8008")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8008"))

# ----------------------------- БД ---------------------------------------
def _db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with closing(_db()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY,
                condition TEXT NOT NULL,
                created INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results(
                task_id TEXT PRIMARY KEY,
                json TEXT NOT NULL,
                updated INTEGER NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions(
                task_id    TEXT PRIMARY KEY,
                mode       TEXT NOT NULL CHECK(mode IN ('file','text')),
                text       TEXT,
                file_path  TEXT,
                file_name  TEXT,
                uploaded_at INTEGER NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS teacher_reviews(
                task_id    TEXT PRIMARY KEY,
                json       TEXT NOT NULL,   -- список критериев с passed от преподавателя
                total      INTEGER NOT NULL,
                updated    INTEGER NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_jobs(
                submission_id TEXT PRIMARY KEY,
                task_id       TEXT NOT NULL,
                status        TEXT NOT NULL,           -- queued|processing|done|error
                external_id   TEXT,
                result_json   TEXT,
                created       INTEGER NOT NULL,
                updated       INTEGER NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)

def load_state_from_db():
    with closing(_db()) as conn:
        cur = conn.execute("SELECT id, condition, created FROM tasks ORDER BY created ASC")
        st.session_state.tasks = [
            {"id": r[0], "condition": r[1], "created": r[2]} for r in cur.fetchall()
        ]
        cur = conn.execute("SELECT task_id, json FROM results")
        st.session_state.results = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
        # submissions -> dict
        cur = conn.execute("SELECT task_id, mode, text, file_path, file_name, uploaded_at FROM submissions")
        st.session_state.submissions = {
            r[0]: {"mode": r[1], "text": r[2], "file_path": r[3], "file_name": r[4], "uploaded_at": r[5]}
            for r in cur.fetchall()
        }
        # teacher_reviews -> dict
        cur = conn.execute("SELECT task_id, json, total, updated FROM teacher_reviews")
        st.session_state.teacher_reviews = {
            r[0]: {"criteria": json.loads(r[1]), "total": r[2], "updated": r[3]}
            for r in cur.fetchall()
        }
        # review_jobs -> dict
        cur = conn.execute("SELECT submission_id, task_id, status, external_id, result_json, created, updated FROM review_jobs")
        st.session_state.review_jobs = {
            r[0]: {
                "task_id": r[1], "status": r[2], "external_id": r[3],
                "result_json": json.loads(r[4]) if r[4] else None,
                "created": r[5], "updated": r[6]
            }
            for r in cur.fetchall()
        }

def refresh_jobs_and_results():
    """Лёгкий рефреш только review_jobs и results из БД, чтобы подхватывать вебхук."""
    with closing(_db()) as conn:
        # review_jobs
        cur = conn.execute(
            "SELECT submission_id, task_id, status, external_id, result_json, created, updated FROM review_jobs"
        )
        st.session_state.review_jobs = {
            r[0]: {
                "task_id": r[1],
                "status": r[2],
                "external_id": r[3],
                "result_json": json.loads(r[4]) if r[4] else None,
                "created": r[5],
                "updated": r[6],
            }
            for r in cur.fetchall()
        }

        # results
        cur = conn.execute("SELECT task_id, json FROM results")
        st.session_state.results = {r[0]: json.loads(r[1]) for r in cur.fetchall()}


def _start_webhook_server_once():
    if st.session_state.get("_webhook_started"):
        return
    app = Flask("llm-callback-server")

    @app.post("/callback")
    def callback():
        try:
            data = request.get_json(force=True, silent=True) or {}
            submission_id = data.get("submission_id")
            task_id = data.get("task_id")
            result = data.get("result")  # ожидаем JSON результата в том же формате, что и сейчас в results
            if not submission_id or not task_id or not isinstance(result, dict):
                return jsonify({"ok": False, "error": "bad payload"}), 400

            set_job_result(submission_id, task_id, result)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    def _run():
        app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False, use_reloader=False)

    threading.Thread(target=_run, daemon=True).start()
    st.session_state["_webhook_started"] = True

# запускаем вебхук при старте приложения
_start_webhook_server_once()

def persist_task(task: Dict[str, Any]):
    with closing(_db()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO tasks(id, condition, created) VALUES(?,?,?)",
            (task["id"], task["condition"], int(task["created"]))
        )

def delete_task(task_id: str):
    with closing(_db()) as conn, conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

def persist_result(task_id: str, data: Dict[str, Any]):
    with closing(_db()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO results(task_id, json, updated) VALUES(?,?,?)",
            (task_id, json.dumps(data, ensure_ascii=False), int(time.time()))
        )

def persist_teacher_review(task_id: str, criteria_list: List[Dict[str, Any]]):
    """criteria_list: [{'name':..., 'passed': bool, 'details': ...}, ...]"""
    total = min(sum(1 for c in criteria_list if bool(c.get("passed"))), 10)
    with closing(_db()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO teacher_reviews(task_id, json, total, updated) VALUES(?,?,?,?)",
            (task_id, json.dumps(criteria_list, ensure_ascii=False), int(total), int(time.time()))
        )
    return total

def persist_submission(task_id: str, payload: Dict[str, Any]):
    """payload: {'mode':'text'|'file', 'text':..., 'file_path':..., 'file_name':...}"""
    with closing(_db()) as conn, conn:
        conn.execute(
            """INSERT OR REPLACE INTO submissions(task_id, mode, text, file_path, file_name, uploaded_at)
               VALUES(?,?,?,?,?,?)""",
            (
                task_id,
                payload.get("mode"),
                payload.get("text"),
                payload.get("file_path"),
                payload.get("file_name"),
                int(time.time()),
            )
        )

def persist_review_job(submission_id: str, task_id: str, status: str="queued", external_id: Optional[str]=None, result: Optional[Dict[str, Any]]=None):
    with closing(_db()) as conn, conn:
        conn.execute(
            """INSERT OR REPLACE INTO review_jobs(submission_id, task_id, status, external_id, result_json, created, updated)
               VALUES(?,?,?,?,?,
                      COALESCE((SELECT created FROM review_jobs WHERE submission_id=?), ?),
                      ?)""",
            (
                submission_id, task_id, status, external_id,
                json.dumps(result, ensure_ascii=False) if result else None,
                submission_id, int(time.time()), int(time.time())
            )
        )

def set_job_result(submission_id: str, task_id: str, result: Dict[str, Any]):
    # сохраняем как обычный результат (в твою таблицу results) + отмечаем джобу
    persist_result(task_id, result)
    persist_review_job(submission_id, task_id, status="done", result=result)


# ----------------------------- Состояние ---------------------------------------
if "tasks" not in st.session_state:
    st.session_state.tasks: List[Dict[str, Any]] = []
if "task_counter" not in st.session_state:
    st.session_state.task_counter = 1
if "show_create" not in st.session_state:
    st.session_state.show_create = False
if "results" not in st.session_state:
    st.session_state.results: Dict[str, Dict[str, Any]] = {}
if "submissions" not in st.session_state:
    st.session_state.submissions: Dict[str, Dict[str, Any]] = {}
if "confirm_delete_task" not in st.session_state:
    st.session_state.confirm_delete_task = None
if "teacher_reviews" not in st.session_state:
    st.session_state.teacher_reviews: Dict[str, Dict[str, Any]] = {}
if "review_jobs" not in st.session_state:
    st.session_state.review_jobs: Dict[str, Dict[str, Any]] = {}

# Инициализируем БД и (при первом запуске) загружаем состояние из неё
if "db_initialized" not in st.session_state:
    init_db()
    load_state_from_db()
    st.session_state.setdefault("tasks", [])
    st.session_state.setdefault("results", {})
    st.session_state.setdefault("submissions", {})
    st.session_state.db_initialized = True

# task_counter синхронизируем с БД (следующее число после максимального)
if "task_counter" not in st.session_state or st.session_state.task_counter == 1:
    if st.session_state.tasks:
        max_num = max(int(t["id"].replace("T", "")) for t in st.session_state.tasks)
        st.session_state.task_counter = max_num + 1
    else:
        st.session_state.task_counter = 1

# NEW: коллбэки для открытия/добавления/отмены
def _open_create():
    st.session_state.show_create = True

def _cancel_create():
    st.session_state.show_create = False
    st.session_state.pop("new_task_text", None)

def _add_task():
    text = (st.session_state.get("new_task_text") or "").strip()
    t_id = f"T{st.session_state.task_counter:04d}"
    task = {"id": t_id, "condition": text, "created": int(time.time())}
    st.session_state.tasks.append(task)
    st.session_state.task_counter += 1
    st.session_state.show_create = False
    st.session_state.pop("new_task_text", None)
    persist_task(task)
    st.toast(f"Задание {t_id} добавлено")

def render_delete_confirmation():
    """Единый блок подтверждения удаления."""
    tid = st.session_state.get("confirm_delete_task")
    if not tid:
        return
    with st.container(border=True):
        st.warning(f"Удалить задание {tid}? Это действие необратимо.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Да, удалить", type="primary", key=f"confirm_yes_{tid}"):
                delete_task(tid)
                st.session_state.tasks = [t for t in st.session_state.tasks if t["id"] != tid]
                st.session_state.results.pop(tid, None)
                st.session_state.submissions.pop(tid, None)
                st.session_state.pop(f"input_mode_{tid}", None)
                st.session_state.pop(f"file_{tid}", None)
                st.session_state.pop(f"text_{tid}", None)
                st.session_state.confirm_delete_task = None
                st.toast(f"Задание {tid} удалено")
                st.rerun()
        with c2:
            if st.button("Отмена", key=f"confirm_no_{tid}"):
                st.session_state.confirm_delete_task = None
                st.rerun()

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
        x = int(r.get("x", 0) * w); y = int(r.get("y", 0) * h)
        rw = int(r.get("w", 0) * w); rh = int(r.get("h", 0) * h)
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
        "comment": (f"Черновой отчёт (мок). task={payload.get('task','(нет)')} ts={now}"
                    f"text_length={len((payload.get('text') or ''))}"),
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
refresh_jobs_and_results()

# Автообновление, если есть активные задания
has_active = any(j["status"] in ("queued", "processing") for j in st.session_state.get("review_jobs", {}).values())
if has_active:
    # каждые 3 секунды
    st.experimental_set_query_params(_=int(time.time()))  # чтобы не кэшировалось
    st.markdown(
        "<script>setTimeout(()=>window.location.reload(),3000);</script>",
        unsafe_allow_html=True
    )


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
    st.button("Добавить задание", width="stretch", on_click=_open_create)

if st.session_state.show_create:
    st.info("Введите условие задания и нажмите «Добавить».")
    st.text_area("Условие задания", key="new_task_text", height=160)
    c1, c2 = st.columns([1,1])
    with c1:
        st.button("Добавить", type="primary", on_click=_add_task)
    with c2:
        st.button("Отмена", on_click=_cancel_create)

if not st.session_state.tasks:
    st.caption("Пока нет заданий — создайте первое.")

for task in st.session_state.tasks:
    with st.container(border=True):
        # ── ШАПКА: название слева, удалить справа ──────────────────────────────
        head_l, head_r = st.columns([0.88, 0.04])
        with head_l:
            st.markdown(f"**Задание №{task['id']}** — {task['condition'] or '(без описания)'}")
        with head_r:
            st.markdown("<div style='text-align:right;'>", unsafe_allow_html=True)
            if st.button("Удалить", key=f"delete_{task['id']}"):
                st.session_state.confirm_delete_task = task["id"]
            st.markdown("</div>", unsafe_allow_html=True)

        # показываем окно подтверждения удаления именно под этим заданием
        if st.session_state.get("confirm_delete_task") == task["id"]:
            with st.container(border=True):
                st.warning(f"Удалить задание {task['id']}? Это действие необратимо.")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Да, удалить", type="primary", key=f"confirm_yes_{task['id']}"):
                        delete_task(task["id"])
                        st.session_state.tasks = [t for t in st.session_state.tasks if t["id"] != task["id"]]
                        st.session_state.results.pop(task["id"], None)
                        st.session_state.submissions.pop(task["id"], None)
                        st.session_state.pop(f"input_mode_{task['id']}", None)
                        st.session_state.pop(f"file_{task['id']}", None)
                        st.session_state.pop(f"text_{task['id']}", None)
                        st.session_state.confirm_delete_task = None
                        st.toast(f"Задание {task['id']} удалено")
                        st.rerun()
                with c2:
                    if st.button("Отмена", key=f"confirm_no_{task['id']}"):
                        st.session_state.confirm_delete_task = None
                        st.rerun()

        # уже отправленное решение?
        submission = st.session_state.submissions.get(task["id"])

        # ── СКЛАДЫВАЕМЫЙ БЛОК: ответ + отправка + оценки ──────────────────────
        with st.expander(
            "Ответ ученика и оценки",
            expanded=not bool(submission)  # до отправки — открыто, после — свернуто
        ):
            # выбор способа сдачи (блокируем, если уже отправлено)
            mode_key = f"input_mode_{task['id']}"
            if mode_key not in st.session_state:
                st.session_state[mode_key] = "Файл"

            input_mode = st.radio(
                "Способ загрузки решения",
                ["Файл", "Текст"],
                key=mode_key,
                horizontal=True,
                help="Выберите, как хотите сдать решение для этого задания",
                disabled=bool(submission),
            )

            uploaded = None
            sol_text = ""

            # поля ввода / просмотр отправленного
            if submission:
                st.success("Решение отправлено. Повторная отправка и редактирование недоступны.")
                if submission["mode"] == "text":
                    st.text_area("Отправленный текст", value=submission.get("text") or "",
                                 height=180, disabled=True)
                else:
                    st.caption("Отправленный файл:")
                    file_name = submission.get("file_name") or "file"
                    file_path = submission.get("file_path")
                    if file_path and os.path.exists(file_path):
                        with open(file_path, "rb") as f:
                            st.download_button("Скачать отправленный файл", data=f.read(), file_name=file_name)
                    else:
                        st.info(file_name)
            else:
                if input_mode == "Файл":
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

                # Кнопка отправки показываем только пока нет submission
                if st.button(f"Отправить решение", key=f"send_{task['id']}"):
                    # валидация
                    if input_mode == "Файл" and not uploaded:
                        st.error("Пожалуйста, загрузите файл решения.")
                        st.stop()
                    if input_mode == "Текст" and not (sol_text and sol_text.strip()):
                        st.error("Пожалуйста, введите текст решения.")
                        st.stop()

                    payload = {
                        "submission_id": f"{task['id']}-submission",
                        "task": task.get("condition", ""),
                        "text": (sol_text or "").strip() if input_mode == "Текст" else "",
                    }

                    # вызов API/мок
                    if mode == "API":
                        # 1) фиксируем submission локально (как и в синхронной ветке)
                        if input_mode == "Текст":
                            sub = {"mode": "text", "text": (sol_text or "").strip(), "file_path": None, "file_name": None}
                        else:
                            ts = int(time.time())
                            fname = uploaded.name
                            safe_name = f"{task['id']}_{ts}_{fname}"
                            path = os.path.join(UPLOAD_DIR, safe_name)
                            with open(path, "wb") as out:
                                out.write(uploaded.getbuffer())
                            sub = {"mode": "file", "text": None, "file_path": path, "file_name": fname}

                        st.session_state.submissions[task["id"]] = sub
                        persist_submission(task["id"], sub)

                        # 2) регистрируем job и зовём внешний LLM
                        submission_id = f"{task['id']}-submission"
                        persist_review_job(submission_id, task["id"], status="queued")

                        # локально сразу обновим память — чтобы баннер и авто-обновление включились мгновенно
                        st.session_state.review_jobs[submission_id] = {
                            "task_id": task["id"],
                            "status": "queued",
                            "external_id": None,
                            "result_json": None,
                            "created": int(time.time()),
                            "updated": int(time.time()),
                        }

                        payload = {
                            "submission_id": submission_id,
                            "task_id": task["id"],
                            "task_text": task.get("condition", ""),
                            "mode": "text" if input_mode == "Текст" else "file",
                            "text": (sol_text or "").strip() if input_mode == "Текст" else "",
                            "file_url": None,  # при нужде отдай сюда URL файла
                            "callback_url": PUBLIC_CALLBACK_BASE.rstrip("/") + "/callback",
                        }

                        try:
                            if not LLM_API_URL:
                                st.error("Не задан LLM_API_URL")
                                st.stop()
                            if not PUBLIC_CALLBACK_BASE:
                                st.error("Не задан PUBLIC_CALLBACK_BASE")
                                st.stop()
                            llm_api = LLM_API_URL.rstrip("/") + "/reviews/async"
                            resp = requests.post(llm_api, json=payload, timeout=REQUEST_TIMEOUT)
                            if resp.status_code >= 400:
                                st.error(f"LLM API error: {resp.status_code} {resp.text[:200]}")
                                persist_review_job(submission_id, task["id"], status="error")
                            else:
                                data = resp.json()
                                new_status = data.get("status", "processing")
                                ext_id = data.get("id")
                                persist_review_job(submission_id, task["id"], status=new_status, external_id=ext_id)

                                # Сразу обновим память
                                st.session_state.review_jobs[submission_id].update({
                                    "status": new_status,
                                    "external_id": ext_id,
                                    "updated": int(time.time()),
                                })
                        except Exception as e:
                            st.error(f"Ошибка вызова LLM: {e}")
                            persist_review_job(submission_id, task["id"], status="error")
                            st.session_state.review_jobs[submission_id]["status"] = "error"
                            st.session_state.review_jobs[submission_id]["updated"] = int(time.time())

                        st.success("Решение отправлено на проверку. Как только LLM закончит, результат появится автоматически.")
                        st.rerun()
                    else:
                        # синхронный МОК как и раньше
                        data = mock_response({
                            "submission_id": f"{task['id']}-submission",
                            "task": task.get("condition", ""),
                            "text": (sol_text or "").strip() if input_mode == "Текст" else "",
                        })
                        st.session_state.results[task['id']] = data
                        persist_result(task['id'], data)

                        # фиксируем submission (для мок-ветки)
                        if input_mode == "Текст":
                            sub = {"mode": "text", "text": (sol_text or "").strip(), "file_path": None, "file_name": None}
                        else:
                            ts = int(time.time())
                            fname = uploaded.name
                            safe_name = f"{task['id']}_{ts}_{fname}"
                            path = os.path.join(UPLOAD_DIR, safe_name)
                            with open(path, "wb") as out:
                                out.write(uploaded.getbuffer())
                            sub = {"mode": "file", "text": None, "file_path": path, "file_name": fname}

                        st.session_state.submissions[task["id"]] = sub
                        persist_submission(task["id"], sub)
                        st.success("Решение отправлено.")
                        st.rerun()

            # Статус внешней проверки
            submission_id = f"{task['id']}-submission"
            job = st.session_state.review_jobs.get(submission_id)
            if job and job["status"] in ("queued", "processing"):
                st.info("Оценка запущена на внешнем LLM. Ожидаем результат…")
            elif job and job["status"] == "error":
                st.error("Не удалось получить результат от LLM. Попробуйте отправить ещё раз.")


            # ── Оценка AI ───────────────────────────────────────────────────────
            data = st.session_state.results.get(task['id'])
            if data:
                st.subheader("Оценка AI:")
                if "criteria" in data and data["criteria"]:
                    raw = pd.DataFrame(data["criteria"]).copy()
                    raw["passed"] = raw.get("passed", False).astype(bool)
                    raw["name"]   = raw.get("name", "").astype(str)
                    raw["details"]= raw.get("details", "").astype(str)

                    df_display = pd.DataFrame({
                        "№": range(1, len(raw) + 1),
                        "Описание критерия": raw["name"],
                        "Оценка": [""] * len(raw),    # покрасим клетку
                        "Пояснение": raw["details"],
                    })

                    def _ai_color(_):
                        return ['background-color: #dcfce7' if p else 'background-color: #fee2e2'
                                for p in raw["passed"]]

                    styler = df_display.style.apply(_ai_color, subset=["Оценка"])

                    st.dataframe(
                        styler,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "№": st.column_config.NumberColumn("№", width=60),
                            "Описание критерия": st.column_config.TextColumn("Описание критерия"),
                            "Оценка": st.column_config.TextColumn("Оценка"),
                            "Пояснение": st.column_config.TextColumn("Пояснение"),
                        },
                        key=f"df_ai_{task['id']}"
                    )

                    ai_total = min(int(raw["passed"].sum()), 10)
                    st.metric("Итоговая оценка AI", f"{ai_total} / 10")
                else:
                    st.info("Критерии отсутствуют.")

                # ── Оценка преподавателя ────────────────────────────────────────
                st.subheader("Оценка преподавателя:")
                teacher = st.session_state.teacher_reviews.get(task["id"])

                if data.get("criteria"):
                    base = pd.DataFrame(data["criteria"])
                    base["name"] = base.get("name", "").astype(str)
                    base["details"] = base.get("details", "").astype(str)

                    if teacher:
                        traw = pd.DataFrame(teacher["criteria"])
                        for col in ("name", "passed", "details"):
                            if col not in traw:
                                traw[col] = "" if col != "passed" else False
                        traw["passed"] = traw["passed"].astype(bool)

                        df_t = pd.DataFrame({
                            "№": range(1, len(traw) + 1),
                            "Описание критерия": traw["name"],
                            "Оценка": [""] * len(traw),
                            "Пояснение": traw["details"],
                        })

                        def _t_color(_):
                            return ['background-color: #dcfce7' if p else 'background-color: #fee2e2'
                                    for p in traw["passed"]]

                        st.dataframe(
                            df_t.style.apply(_t_color, subset=["Оценка"]),
                            width="stretch",
                            hide_index=True,
                            column_config={
                                "№": st.column_config.NumberColumn("№", width=60),
                                "Описание критерия": st.column_config.TextColumn("Описание критерия"),
                                "Оценка": st.column_config.TextColumn("Оценка"),
                                "Пояснение": st.column_config.TextColumn("Пояснение"),
                            },
                            key=f"df_teacher_{task['id']}"
                        )
                        st.metric("Итоговая оценка преподавателя", f"{teacher['total']} / 10")
                    else:
                        st.caption("Отметьте статус и (опционально) добавьте пояснение к каждому критерию.")
                        teacher_inputs = []
                        for i, row in base.reset_index(drop=True).iterrows():
                            c1, c2, c3, c4 = st.columns([0.07, 0.38, 0.25, 0.30])
                            with c1:
                                st.markdown(f"**{i+1}**")
                            with c2:
                                st.markdown(row["name"] or "")
                            with c3:
                                status_val = st.radio(
                                    "Статус",
                                    options=["Выполнено", "Не выполнено"],
                                    horizontal=True,
                                    key=f"teach_radio_{task['id']}_{i}",
                                    label_visibility="collapsed",
                                )
                            with c4:
                                note_val = st.text_input(
                                    "Пояснение",
                                    value=row.get("details", "") or "",
                                    key=f"teach_note_{task['id']}_{i}",
                                    placeholder="Комментарий (необязательно)",
                                    label_visibility="collapsed",
                                )
                            teacher_inputs.append({
                                "name": row["name"],
                                "passed": (status_val == "Выполнено"),
                                "details": (note_val or "").strip(),
                            })

                        if st.button("Сохранить оценку преподавателя", type="primary",
                                     key=f"save_teacher_{task['id']}"):
                            total = persist_teacher_review(task["id"], teacher_inputs)
                            st.session_state.teacher_reviews[task["id"]] = {
                                "criteria": teacher_inputs, "total": total, "updated": int(time.time())
                            }
                            st.success("Оценка преподавателя сохранена.")
                            st.rerun()

                    # Итог (главный): если есть оценка преподавателя — берём её
                    final_total = st.session_state.teacher_reviews.get(task["id"], {}).get("total", ai_total)
                    st.subheader("Итоговая оценка")
                    st.metric("Итог", f"{final_total} / 10")

        # визуальный разделитель между заданиями
        st.divider()
