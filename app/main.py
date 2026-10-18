import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.deps import get_transcriber
from app.routes import box, box_ws, detections, incidents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load Whisper at startup, so the first reply after a fall doesn't wait for it.
    get_transcriber()
    yield


app = FastAPI(title="Steady", lifespan=lifespan)

# Browsers block a page on one origin (the dashboard, e.g. localhost:5173) from calling another
# (this API) unless the API says it's allowed. Reading settings here also means a missing
# API_KEY stops the server at startup rather than on the first request.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)
app.include_router(detections.router)
app.include_router(box.router)
app.include_router(box_ws.router)
app.include_router(incidents.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
