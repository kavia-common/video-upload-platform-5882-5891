from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, status, Query, Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# Base directories
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # /home/.../video-upload-platform-.../
CONTAINER_ROOT = Path(__file__).resolve().parents[2]  # .../video_upload_backend/
# Per requirement: "Store all uploaded files under the /upload directory at the project root."
UPLOAD_DIR = PROJECT_ROOT / "upload"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Allowed video extensions
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}

app = FastAPI(
    title="Video Upload Backend",
    description="FastAPI service to upload, list, download, and manage videos stored under the /upload directory.",
    version="1.0.0",
    openapi_tags=[
        {"name": "health", "description": "Health check"},
        {"name": "videos", "description": "Video upload and management"},
    ],
)

# Enable permissive CORS for simplicity (no auth in this task)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Models
class VideoMetadata(BaseModel):
    filename: str = Field(..., description="File name including extension")
    file_type: str = Field(..., description="MIME type if available, otherwise inferred from extension")
    file_size: int = Field(..., description="File size in bytes")
    uploaded_at: datetime = Field(..., description="Upload timestamp in UTC")


class UploadResponse(BaseModel):
    filename: str = Field(..., description="Stored file name")
    url: str = Field(..., description="Direct download URL for the stored file")
    metadata: VideoMetadata = Field(..., description="Metadata for the uploaded file")


class ListVideosResponse(BaseModel):
    items: List[VideoMetadata] = Field(..., description="List of stored video files metadata")
    total: int = Field(..., description="Total number of files found")


def _is_allowed_file(filename: str) -> bool:
    """Return True if file extension is allowed."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _safe_destination_name(original_name: str) -> str:
    """
    Generate a safe destination filename.
    - Strips directory parts
    - Avoids absolute paths
    - Prefixes timestamp to reduce collisions
    """
    name = Path(original_name).name  # remove any path segments
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}__{name}"


def _build_file_metadata(file_path: Path, original_content_type: Optional[str] = None) -> VideoMetadata:
    """Build VideoMetadata for a given file path."""
    stat = file_path.stat()
    file_type = original_content_type or file_path.suffix.lower().lstrip(".")
    # Standardize file type string: prefer content type; if not, use extension
    if original_content_type:
        display_type = original_content_type
    else:
        display_type = f"video/{file_type}" if file_type else "application/octet-stream"
    # Use file's mtime as uploaded_at if present; otherwise now
    uploaded_at = datetime.utcfromtimestamp(stat.st_mtime)
    return VideoMetadata(
        filename=file_path.name,
        file_type=display_type,
        file_size=stat.st_size,
        uploaded_at=uploaded_at,
    )


@app.get("/", tags=["health"], summary="Health Check")
# PUBLIC_INTERFACE
def health_check():
    """Health check endpoint to verify service availability."""
    return {"message": "Healthy"}


@app.post(
    "/videos/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["videos"],
    summary="Upload a video file",
    description="Uploads a single video file to the /upload directory after validating its type.",
)
# PUBLIC_INTERFACE
async def upload_video(file: UploadFile = File(..., description="Video file to upload (.mp4, .avi, .mov, .mkv)")) -> UploadResponse:
    """
    Upload a video file.

    Parameters:
    - file: UploadFile - the uploaded video file.

    Returns:
    - UploadResponse containing the stored filename, a direct URL, and metadata.

    Raises:
    - 400 if file type is not allowed.
    - 500 if saving the file fails.
    """
    # Validate filename and extension
    if not file or not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided.")
    if not _is_allowed_file(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    dest_name = _safe_destination_name(file.filename)
    dest_path = UPLOAD_DIR / dest_name

    try:
        # Save file to disk in chunks to avoid memory spikes
        with dest_path.open("wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                buffer.write(chunk)
    except Exception as exc:
        # Cleanup partial file if existed
        if dest_path.exists():
            try:
                dest_path.unlink()
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to save file: {exc}") from exc
    finally:
        await file.close()

    meta = _build_file_metadata(dest_path, original_content_type=file.content_type)
    url = f"/videos/download/{dest_path.name}"
    return UploadResponse(filename=dest_path.name, url=url, metadata=meta)


@app.get(
    "/videos",
    response_model=ListVideosResponse,
    tags=["videos"],
    summary="List uploaded videos",
    description="Returns metadata for all video files stored under the /upload directory.",
)
# PUBLIC_INTERFACE
def list_videos(
    ext: Optional[str] = Query(default=None, description="Optional extension filter (e.g., mp4, avi). Do not include leading dot."),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description="Optional maximum number of items to return."),
    offset: int = Query(default=0, ge=0, description="Number of items to skip before starting to collect the result set."),
) -> ListVideosResponse:
    """
    List uploaded videos with optional filtering and pagination.

    Parameters:
    - ext: Optional file extension without dot to filter results.
    - limit: Optional maximum number of items to return (1..1000).
    - offset: Number of items to skip.

    Returns:
    - ListVideosResponse with items and total count.
    """
    if not UPLOAD_DIR.exists():
        return ListVideosResponse(items=[], total=0)

    items: List[VideoMetadata] = []
    for entry in sorted(UPLOAD_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in ALLOWED_EXTENSIONS:
            # skip any non-video files in the directory
            continue
        if ext:
            if entry.suffix.lower().lstrip(".") != ext.lower():
                continue
        items.append(_build_file_metadata(entry))

    total = len(items)
    if offset:
        items = items[offset:]
    if limit is not None:
        items = items[:limit]

    return ListVideosResponse(items=items, total=total)


@app.get(
    "/videos/metadata/{filename}",
    response_model=VideoMetadata,
    tags=["videos"],
    summary="Get metadata for a specific video",
    description="Returns metadata for the specified stored video file.",
)
# PUBLIC_INTERFACE
def get_video_metadata(
    filename: str = FPath(..., description="Exact stored filename")
) -> VideoMetadata:
    """
    Get metadata for a specific video file by stored filename.

    Raises:
    - 404 if file does not exist.
    - 400 if the filename is invalid or extension not allowed.
    """
    # security: prevent path traversal
    safe_name = Path(filename).name
    if not _is_allowed_file(safe_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or unsupported file type.")

    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    return _build_file_metadata(file_path)


@app.get(
    "/videos/download/{filename}",
    response_class=FileResponse,
    tags=["videos"],
    summary="Download a video file",
    description="Downloads the specified video file from /upload directory.",
)
# PUBLIC_INTERFACE
def download_video(
    filename: str = FPath(..., description="Exact stored filename")
):
    """
    Download a specific video file by stored filename.

    Raises:
    - 404 if file does not exist.
    - 400 if filename is invalid.
    """
    safe_name = Path(filename).name
    if not _is_allowed_file(safe_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or unsupported file type.")
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    # Best-effort content type based on extension
    ext = file_path.suffix.lower().lstrip(".")
    media_type = f"video/{ext}" if ext else "application/octet-stream"
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
    )


@app.get(
    "/videos/stream/{filename}",
    tags=["videos"],
    summary="Stream a video file",
    description="Streams the specified video file. Note: This is a basic stream and does not implement range requests.",
)
# PUBLIC_INTERFACE
def stream_video(
    filename: str = FPath(..., description="Exact stored filename")
):
    """
    Stream a video file. This basic implementation streams the file sequentially.
    For production-grade streaming with seek support, implement HTTP Range requests.

    Raises:
    - 404 if file does not exist.
    - 400 if filename is invalid.
    """
    safe_name = Path(filename).name
    if not _is_allowed_file(safe_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or unsupported file type.")
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    def iterfile():
        with file_path.open("rb") as f:
            while True:
                data = f.read(1024 * 1024)  # 1MB
                if not data:
                    break
                yield data

    ext = file_path.suffix.lower().lstrip(".")
    media_type = f"video/{ext}" if ext else "application/octet-stream"
    return StreamingResponse(iterfile(), media_type=media_type)


@app.delete(
    "/videos/{filename}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["videos"],
    summary="Delete a video file",
    description="Deletes the specified video file from the /upload directory.",
)
# PUBLIC_INTERFACE
def delete_video(
    filename: str = FPath(..., description="Exact stored filename to delete")
):
    """
    Delete a video file.

    Raises:
    - 404 if file does not exist.
    - 400 if filename is invalid.
    """
    safe_name = Path(filename).name
    if not _is_allowed_file(safe_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or unsupported file type.")
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    try:
        file_path.unlink()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete file: {exc}") from exc

    # 204 No Content
    return None
