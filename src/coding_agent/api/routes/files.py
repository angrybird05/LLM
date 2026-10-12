import os
import uuid

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from coding_agent.api.middleware.auth import get_current_user
from coding_agent.config import settings
from coding_agent.models.schemas import FileUploadResponse

router = APIRouter()

UPLOAD_DIR = settings.UPLOAD_DIR
os.makedirs(UPLOAD_DIR, exist_ok=True)


def is_allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in settings.allowed_extensions_list


@router.post("/files/upload", response_model=FileUploadResponse)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not is_allowed_file(file.filename):
        raise HTTPException(status_code=400, detail=f"File type not allowed. Allowed: {settings.allowed_extensions_list}")

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Max size: {settings.MAX_UPLOAD_SIZE} bytes")

    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1]
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")

    async with aiofiles.open(save_path, "wb") as f:
        await f.write(content)

    return FileUploadResponse(
        filename=file.filename,
        path=save_path,
        size=len(content),
        content_type=file.content_type or "application/octet-stream",
    )


@router.get("/files/{file_id}")
async def download_file(
    file_id: str,
    user: dict = Depends(get_current_user),
):
    for ext in settings.allowed_extensions_list:
        path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
        if os.path.exists(path):
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="File not found")
