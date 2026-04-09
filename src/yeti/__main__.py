"""Run YETI API server directly: python -m yeti"""

import uvicorn

from yeti.config import settings

uvicorn.run(
    "yeti.app:app",
    host=settings.host,
    port=settings.port,
    reload=settings.debug,
)
