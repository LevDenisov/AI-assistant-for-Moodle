from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class Task:
    id: str
    condition: str
    created: int

@dataclass
class Submission:
    task_id: str
    mode: str            # 'file' | 'text'
    text: Optional[str]
    file_path: Optional[str]
    file_name: Optional[str]
    uploaded_at: int

@dataclass
class ReviewJob:
    submission_id: str
    task_id: str
    status: str          # queued|processing|done|error
    external_id: Optional[str]
    result_json: Optional[Dict[str, Any]]
    created: int
    updated: int
