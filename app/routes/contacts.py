from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.deps import get_linker, get_repo, require_api_key
from app.models import ContactKind
from app.repository import Repository
from app.telegram import TelegramError, TelegramLinker

router = APIRouter(prefix="/api/v1/contacts", dependencies=[Depends(require_api_key)])


class TelegramLink(BaseModel):
    contact_id: str
    link: str


@router.post("/{contact_id}/telegram-link")
async def create_telegram_link(
    contact_id: str,
    repo: Repository = Depends(get_repo),
    linker: TelegramLinker | None = Depends(get_linker),
) -> TelegramLink:
    """A one-time link for the caregiver to open, connecting their Telegram for alerts.

    For now scripts/telegram_link.sh calls this; later the dashboard will, when a caregiver signs up.
    """
    contact = repo.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    if contact.kind != ContactKind.PERSON:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only people get Telegram alerts")
    if linker is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="TELEGRAM_BOT_TOKEN not set")
    try:
        link = await linker.link_for(contact)
    except TelegramError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Telegram unreachable: {e}")
    return TelegramLink(contact_id=contact.id, link=link)
