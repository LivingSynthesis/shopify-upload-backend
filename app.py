from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pathlib import Path
from uuid import uuid4
import shutil
import os

app = FastAPI()

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")


def safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        else:
            keep.append("-")
    cleaned = "".join(keep).strip("-")
    return cleaned or "upload.bin"


@app.get("/")
def root():
    return {"ok": True}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    upload_id = uuid4().hex[:12]
    original_name = safe_filename(file.filename)
    stored_name = f"{upload_id}-{original_name}"
    destination = UPLOAD_DIR / stored_name

    with destination.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    url = f"{BASE_URL}/files/{stored_name}" if BASE_URL else f"/files/{stored_name}"

    return JSONResponse(
        {
            "upload_id": upload_id,
            "filename": stored_name,
            "original_filename": file.filename,
            "url": url,
        }
    )


@app.get("/files/{filename}")
def get_file(filename: str):
    path = UPLOAD_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)