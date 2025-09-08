from __future__ import annotations
from typing import Any, Dict, List
from PIL import Image, ImageDraw
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

def render_pdf_pages(file_bytes: bytes, dpi: int) -> List[Image.Image]:
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
