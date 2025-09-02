# Video Upload Backend (FastAPI)

This FastAPI service provides endpoints to upload, list, retrieve, stream, and delete video files. All files are stored under the `/upload` directory at the project root, as required.

## Features
- Upload video files with validation (`.mp4`, `.avi`, `.mov`, `.mkv`)
- List uploaded videos with basic metadata (filename, file size, type, upload time)
- Download or stream specific files
- Get metadata for a specific file
- Delete files
- Swagger UI documentation at `/docs`

## Directory Structure
- `/upload` - storage location for uploaded files (created at runtime if not present)
- `src/api/main.py` - FastAPI app entry point

## Running locally

Install dependencies:
```
pip install -r requirements.txt
```

Start the server (from this directory):
```
uvicorn src.api.main:app --host 0.0.0.0 --port 3001 --reload
```

Open API docs: http://localhost:3001/docs

## API Overview

- GET `/` - Health check
- POST `/videos/upload` - Upload a video file (form field: `file`)
- GET `/videos` - List videos (optional query: `ext`, `limit`, `offset`)
- GET `/videos/metadata/{filename}` - Get metadata for a file
- GET `/videos/download/{filename}` - Download a file
- GET `/videos/stream/{filename}` - Stream a file (basic, no range support)
- DELETE `/videos/{filename}` - Delete a file

## Notes
- No authentication/authorization included.
- Ensure your client uses `multipart/form-data` with a `file` field when uploading.
