import io
from pathlib import Path


def extract_resume_text(content: bytes, filename: str = "resume.pdf") -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".txt":
        return content.decode("utf-8", errors="replace")
    if ext != ".pdf":
        return ""

    try:
        from pypdf import PdfReader
    except ImportError:
        return "[PDF text extraction requires: pip install pypdf]"

    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return "[Could not extract PDF text]"

