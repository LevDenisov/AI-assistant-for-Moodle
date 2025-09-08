from __future__ import annotations
import requests
from typing import Any, Dict, Optional, Tuple
from config import REQUEST_TIMEOUT, LLM_API_URL

def call_orchestrator_async(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    try:
        url = LLM_API_URL.rstrip("/") + "/reviews/async"
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 400:
            return False, f"{resp.status_code} {resp.text[:200]}"
        return True, None
    except Exception as e:
        return False, str(e)
