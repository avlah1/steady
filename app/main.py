import logging

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Steady")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
