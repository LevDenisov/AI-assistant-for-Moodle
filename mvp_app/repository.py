from __future__ import annotations
from typing import Any, Dict, List
from contextlib import closing
import json, time
from db import connect
from models import Task, Submission, ReviewJob

def upsert_task(task: Task) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO tasks(id, condition, created) VALUES(?,?,?)",
            (task.id, task.condition, task.created),
        )

def delete_task(task_id: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

def list_tasks() -> List[Task]:
    with closing(connect()) as conn:
        cur = conn.execute("SELECT id, condition, created FROM tasks ORDER BY created ASC")
        return [Task(*row) for row in cur.fetchall()]

def upsert_result(task_id: str, data: Dict[str, Any]) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO results(task_id, json, updated) VALUES(?,?,?)",
            (task_id, json.dumps(data, ensure_ascii=False), int(time.time())),
        )

def load_results() -> Dict[str, Dict[str, Any]]:
    with closing(connect()) as conn:
        cur = conn.execute("SELECT task_id, json FROM results")
        return {r[0]: json.loads(r[1]) for r in cur.fetchall()}

def upsert_submission(sub: Submission) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO submissions(task_id, mode, text, file_path, file_name, uploaded_at)
            VALUES(?,?,?,?,?,?)
            """,
            (sub.task_id, sub.mode, sub.text, sub.file_path, sub.file_name, sub.uploaded_at),
        )

def load_submissions() -> Dict[str, Dict[str, Any]]:
    with closing(connect()) as conn:
        cur = conn.execute(
            "SELECT task_id, mode, text, file_path, file_name, uploaded_at FROM submissions"
        )
        return {
            r[0]: {
                "mode": r[1],
                "text": r[2],
                "file_path": r[3],
                "file_name": r[4],
                "uploaded_at": r[5],
            }
            for r in cur.fetchall()
        }

def upsert_teacher_review(task_id: str, criteria_list: List[Dict[str, Any]]) -> int:
    total = min(sum(1 for c in criteria_list if bool(c.get("passed"))), 10)
    with closing(connect()) as conn, conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO teacher_reviews(task_id, json, total, updated)
            VALUES(?,?,?,?)
            """,
            (task_id, json.dumps(criteria_list, ensure_ascii=False), int(total), int(time.time())),
        )
    return total

def load_teacher_reviews() -> Dict[str, Dict[str, Any]]:
    with closing(connect()) as conn:
        cur = conn.execute("SELECT task_id, json, total, updated FROM teacher_reviews")
        return {
            r[0]: {"criteria": json.loads(r[1]), "total": r[2], "updated": r[3]}
            for r in cur.fetchall()
        }

def upsert_review_job(job: ReviewJob) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO review_jobs(submission_id, task_id, status, external_id, result_json, created, updated)
            VALUES(?,?,?,?,?,
                    COALESCE((SELECT created FROM review_jobs WHERE submission_id=?), ?),
                    ?)
            """,
            (
                job.submission_id,
                job.task_id,
                job.status,
                job.external_id,
                json.dumps(job.result_json, ensure_ascii=False) if job.result_json else None,
                job.submission_id,
                job.created,
                job.updated,
            ),
        )

def set_job_result(submission_id: str, task_id: str, result: Dict[str, Any]) -> None:
    upsert_result(task_id, result)
    upsert_review_job(
        ReviewJob(
            submission_id=submission_id,
            task_id=task_id,
            status="done",
            external_id=None,
            result_json=result,
            created=int(time.time()),
            updated=int(time.time()),
        )
    )

def load_review_jobs() -> Dict[str, Dict[str, Any]]:
    with closing(connect()) as conn:
        cur = conn.execute(
            "SELECT submission_id, task_id, status, external_id, result_json, created, updated FROM review_jobs"
        )
        return {
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
