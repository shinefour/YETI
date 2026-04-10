"""Image API — serve stored images."""

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from yeti.vision.storage import get_image_path

router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("/{image_id}")
async def get_image(image_id: str):
    path = get_image_path(image_id)
    if not path:
        return JSONResponse(
            {"error": "Not found"}, status_code=404
        )
    return FileResponse(path)
