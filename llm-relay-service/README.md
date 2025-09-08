# LLM Relay Service (FastAPI)

Сервис-посредник между вашим веб‑сервисом и LLM:
- Принимает задания от веб‑сервиса (`POST /v1/reviews`).
- Форвардит их в LLM (HTTP POST) и ожидает вебхук от LLM (`POST /v1/llm/callback/{job_id}`).
- Возвращает результат обратно в ваш веб‑сервис по HTTP с ретраями и HMAC‑подписью.

## Быстрый старт

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `DATABASE_URL` | Путь к SQLite | `sqlite:///./relay.db` |
| `LLM_API_URL` | Базовый URL LLM сервера | `http://llm-host:8000` |
| `LLM_API_KEY` | (опц.) API ключ LLM | пусто |
| `PUBLIC_BASE_URL` | Публичный базовый URL этого сервиса для вебхуков LLM | `http://localhost:8080` |
| `CALLBACK_HMAC_SECRET` | Секрет для подписи HMAC при отправке результата в ваш сервис | `dev-secret` |
| `CALLBACK_MAX_RETRIES` | Кол-во попыток повторной отправки | `6` |
| `CALLBACK_BACKOFF_SECONDS` | Начальная задержка между ретраями | `2` |

## Контракты

### 1) Приём задания от веб‑сервиса

`POST /v1/reviews`

Пример запроса:
```json
{
  "submission_id": "subm-001",
  "file_refs": [{"url": "https://files/1.pdf"}],
  "student_id": "stu-42",
  "metadata": {"course":"ML-101"},
  "webhook_url": "https://your-webservice.example.com/api/reviews/callback"
}
```

Ответ:
```json
{"job_id":"job_...","status":"queued"}
```

### 2) Вебхук от LLM

`POST /v1/llm/callback/{job_id}`

Пример payload (LLM → relay):
```json
{
  "ok": true,
  "result": {"score": 0.86, "feedback": "Хорошая структура..."}
}
```

### 3) Релей результата обратно в ваш веб‑сервис

`POST {webhook_url}`

Заголовки:
- `X-Signature: sha256=<hex>` — HMAC от тела запроса с секретом `CALLBACK_HMAC_SECRET`.

Пример payload (relay → ваш сервис):
```json
{
  "job_id": "job_...",
  "submission_id": "subm-001",
  "ok": true,
  "result": {...}
}
```

## Локальная симуляция LLM
Для отладки доступен эндпоинт:
- `POST /simulate-llm/{job_id}` — имитирует вызов вебхука LLM.

## Заметки по продакшену
- Поставьте reverse-proxy (nginx/traefik) + HTTPS.
- Настройте `PUBLIC_BASE_URL` на публичный адрес.
- Секреты храните в менеджере секретов.
- Версионируйте схемы ответов (JSON Schema/PG migrations).
