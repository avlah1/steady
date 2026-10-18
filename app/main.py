import logging

from fastapi import FastAPI

from app.routes import box, detections, incidents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Steady")
app.include_router(detections.router)
app.include_router(box.router)
app.include_router(incidents.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
