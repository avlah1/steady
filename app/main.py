import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import box, detections, incidents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Steady")

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
app.include_router(incidents.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
