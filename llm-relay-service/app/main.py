import uuid, json
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from .settings import settings
from .db import SessionLocal, init_db
from .schemas import ReviewCreate, ReviewEnqueued, LlmCallbackIn
from .relay import save_job, post_to_llm, mark_job_status, relay_back_to_webservice

app = FastAPI(title="LLM Relay Service", version="1.0.0")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def _startup():
    init_db()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.post("/v1/reviews", response_model=ReviewEnqueued)
async def enqueue_review(body: ReviewCreate, db: Session = Depends(get_db)):
    job_id = "job_" + uuid.uuid4().hex[:24]
    payload = {
        "submission_id": body.submission_id,
        "file_refs": [f.model_dump() for f in body.file_refs],
        "student_id": body.student_id,
        "metadata": body.metadata or {},
    }
    # сохраняем задание
    save_job(db, job_id, body.submission_id, str(body.webhook_url), payload)
    # отправляем в LLM
    try:
        await post_to_llm(job_id, payload)
    except Exception as e:
        # помечаем как failed и сразу пробуем отослать об этом в веб‑сервис
        row = mark_job_status(db, job_id, "failed", {"error": f"LLM request failed: {e}"})
        try:
            await relay_back_to_webservice(row)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail="Upstream LLM error")
    return ReviewEnqueued(job_id=job_id, status="queued")

@app.post("/v1/llm/callback/{job_id}")
async def llm_callback(job_id: str, body: LlmCallbackIn, db: Session = Depends(get_db)):
    # обновляем статус
    status = "done" if body.ok else "failed"
    result = body.result if body.ok else {"error": body.error or "unknown"}
    row = mark_job_status(db, job_id, status, result)

    # релеим обратно в ваш веб‑сервис (с ретраями)
    try:
        await relay_back_to_webservice(row)
    except Exception as e:
        # логируйте через Sentry/логгеры, здесь просто возвращаем 202
        return JSONResponse({"received": True, "relayed": False, "error": str(e)}, status_code=202)

    return {"received": True, "relayed": True}

# Удобный эндпоинт для локальной отладки: имитируем, как будто LLM прислал результат
@app.post("/simulate-llm/{job_id}")
async def simulate_llm(job_id: str, db: Session = Depends(get_db)):
    body = LlmCallbackIn(ok=True, result={"score": 0.91, "feedback": "Супер! Добавьте раздел про метрики."})
    return await llm_callback(job_id, body, db)

# Простой просмотр состояния задания
@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)
