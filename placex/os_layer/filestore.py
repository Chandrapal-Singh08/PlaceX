"""File-system management: validated uploads, content-hash dedupe, per-user quota,
atomic writes, and cleanup of stale generated files."""
import hashlib
import os
import pathlib
import re
import tempfile

class UploadRejected(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code

_MAGIC = {".pdf": b"%PDF", ".docx": b"PK"}          # cheap content sniffing
ALLOWED = {".pdf", ".docx", ".txt", ".md"}

class FileStore:
    def __init__(self, root, max_bytes, quota_bytes):
        self.root = pathlib.Path(root).resolve()
        self.max_bytes, self.quota_bytes = int(max_bytes), int(quota_bytes)
        self.root.mkdir(parents=True, exist_ok=True)

    def check_upload(self, filename: str, data: bytes) -> str:
        """Validate type, size, and magic bytes; return the file extension."""
        ext = pathlib.Path(filename or "").suffix.lower()
        if ext not in ALLOWED:
            raise UploadRejected(f"unsupported file type {ext!r}; allowed: {sorted(ALLOWED)}", 415)
        if not data:
            raise UploadRejected("empty file")
        if len(data) > self.max_bytes:
            raise UploadRejected(f"file larger than {self.max_bytes // 1_048_576} MB limit", 413)
        magic = _MAGIC.get(ext)
        if magic and not data.startswith(magic):
            raise UploadRejected(f"content does not look like a {ext} file", 415)
        return ext

    def save_upload(self, user_id: int, filename: str, data: bytes, used_bytes: int = 0) -> dict:
        ext = self.check_upload(filename, data)
        sha = hashlib.sha256(data).hexdigest()
        # the stored name is derived from the hash only -> user-supplied names can't traverse paths
        path = self.root / "users" / str(int(user_id)) / sha[:2] / f"{sha}{ext}"
        deduped = path.exists()
        if not deduped:
            if used_bytes + len(data) > self.quota_bytes:
                raise UploadRejected("storage quota exceeded", 413)
            self._atomic_write(path, data)
        return {"path": str(path), "sha256": sha, "size": len(data), "deduped": deduped, "ext": ext}

    def write_generated(self, user_id: int, name: str, text: str) -> dict:
        data = text.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        path = self.root / "users" / str(int(user_id)) / "generated" / f"{sha}.md"
        self._atomic_write(path, data)
        return {"path": str(path), "sha256": sha, "size": len(data)}

    def write_generated_pdf(self, user_id: int, markdown_hash: str, data: bytes) -> dict:
        if not re.fullmatch(r"[0-9a-f]{64}", markdown_hash):
            raise ValueError("generated resume hash is invalid")
        if not data.startswith(b"%PDF-"):
            raise ValueError("generated resume PDF is invalid")
        path = self.root / "users" / str(int(user_id)) / "generated" / f"{markdown_hash}.pdf"
        self._atomic_write(path, data)
        return {"path": str(path), "size": len(data)}

    def delete_generated(self, user_id: int, markdown_hash: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", markdown_hash):
            return
        generated_root = self.root / "users" / str(int(user_id)) / "generated"
        if generated_root.resolve() != generated_root:
            return
        for suffix in (".md", ".pdf"):
            self.delete(str(generated_root / f"{markdown_hash}{suffix}"))

    def delete(self, path: str) -> bool:
        p = pathlib.Path(path).resolve()
        if self.root not in p.parents:               # never delete outside the store
            return False
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _atomic_write(path: pathlib.Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)                    # atomic on POSIX: readers never see a half file
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
