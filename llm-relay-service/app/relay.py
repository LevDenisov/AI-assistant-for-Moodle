import json, time
from typing import Any, Dict
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
from sqlalchemy import text
from sqlalchemy.orm import Session

from .settings import settings
from .security import compute_hmac_sha256_hex

class RelayError(Exception):
    pass

async def post_to_llm(job_id: str, payload: Dict[str, Any]) -> None:
    # Формируем callback URL для LLM → relay
    callback_url = f"{settings.public_base_url}/v1/llm/callback/{job_id}"
    data = {
        "job_id": job_id,
        "callback_url": callback_url,
        "payload": payload,
    }
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        r = await client.post(f"{settings.llm_api_url}/v1/reviews", json=data, headers=headers)
        r.raise_for_status()

@retry(
    reraise=True,
    stop=stop_after_attempt(lambda: settings.callback_max_retries),
    wait=wait_exponential_jitter(initial=settings.callback_backoff_seconds, max=60),
    retry=retry_if_exception_type(httpx.HTTPError),
)
async def relay_back_to_webservice(job_row: Dict[str, Any]) -> None:
    # Готовим подпись и отправляем результат в веб‑сервис
    body = {
        "job_id": job_row["id"],
        "submission_id": job_row["submission_id"],
        "ok": job_row["status"] == "done",
        "result": json.loads(job_row["result"]) if job_row["result"] else None,
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    sig = compute_hmac_sha256_hex(settings.callback_hmac_secret, raw)
    headers = {
        "Content-Type": "application/json",
        "X-Signature": f"sha256={sig}",
    }
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        resp = await client.post(job_row["webhook_url"], content=raw, headers=headers)
        resp.raise_for_status()

def save_job(db: Session, job_id: str, submission_id: str, webhook_url: str, payload: Dict[str, Any]) -> None:
    db.execute(
        text("""INSERT INTO jobs (id, submission_id, webhook_url, status, payload, result)
                 VALUES (:id, :submission_id, :webhook_url, 'queued', :payload, NULL)"""),
        {"id": job_id, "submission_id": submission_id, "webhook_url": str(webhook_url), "payload": json.dumps(payload, ensure_ascii=False)},
    )
    db.commit()

def mark_job_status(db: Session, job_id: str, status: str, result: Dict[str, Any] | None) -> Dict[str, Any]:
    db.execute(
        text("""UPDATE jobs SET status=:status, result=:result, updated_at=CURRENT_TIMESTAMP WHERE id=:id"""),
        {"id": job_id, "status": status, "result": json.dumps(result, ensure_ascii=False) if result is not None else None},
    )
    db.commit()
    row = db.execute(text("SELECT id, submission_id, webhook_url, status, result FROM jobs WHERE id=:id"), {"id": job_id}).mappings().first()
    return dict(row) if row else {}
