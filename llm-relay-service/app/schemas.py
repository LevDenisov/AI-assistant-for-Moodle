from pydantic import BaseModel, HttpUrl, Field
from typing import Any, Dict, List, Optional

class FileRef(BaseModel):
    url: HttpUrl
    pages: Optional[List[int]] = None

class ReviewCreate(BaseModel):
    submission_id: str = Field(..., min_length=1)
    file_refs: List[FileRef]
    student_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    webhook_url: HttpUrl

class ReviewEnqueued(BaseModel):
    job_id: str
    status: str

class LlmCallbackIn(BaseModel):
    ok: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
