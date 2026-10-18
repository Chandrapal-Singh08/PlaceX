import re
import zipfile

def extract_text(path: str, ext: str) -> str | None:
    try:
        if ext in (".txt", ".md"):
            return open(path, "r", encoding="utf-8", errors="ignore").read()
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)).strip()
        if ext == ".pdf":
            try:
                from pypdf import PdfReader          # optional: pip install pypdf
            except ImportError:
                return None
            return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    except Exception:                                  # noqa: BLE001
        return None
    return None
