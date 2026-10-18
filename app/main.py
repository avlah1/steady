import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.deps import get_flow, get_telegram_bot, get_repo, get_telegram, get_transcriber
from app.repository import MongoRepository
from app.routes import box, box_ws, contacts, detections, incidents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs every request URL at INFO, and Telegram's URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load Whisper at startup, so the first reply after a fall doesn't wait for it.
    get_transcriber()
    repo = get_repo()
    if isinstance(repo, MongoRepository):
        await repo.start()  # load saved incidents/contacts, start the background writer
    # Timers don't survive a restart: give every incident that's still active a new one.
    await get_flow().resume_after_restart(repo.list_incidents())
    # Listen for caregivers tapping their Telegram link or an alert's "I'm on it" button (app/telegram.py).
    bot = get_telegram_bot()
    polling = asyncio.create_task(bot.run()) if bot else None
    yield
    if polling:
        polling.cancel()
    if telegram := get_telegram():
        await telegram.close()
    if isinstance(repo, MongoRepository):
        await repo.stop()  # finish queued writes before exiting


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
app.include_router(contacts.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
