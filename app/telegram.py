"""Telegram: the API client, and linking a caregiver's Telegram chat to their contact.

A bot can only message someone who has messaged it first, and that's the only time Telegram
tells us their chat ID. So each caregiver gets a one-time link (t.me/<bot>?start=<code>).
Tapping Start sends "/start <code>" to the bot; TelegramLinker matches the code to the contact
and saves the chat ID on it. Alerts are sent by TelegramNotifier (app/notifier.py).

Messages are received by long polling getUpdates: the backend asks "anything new?" and Telegram
holds the request open for up to 30 s until something arrives. It only makes outgoing requests,
so it works on the venue Wi-Fi. (A webhook would need a public URL for Telegram to call.)
"""

import asyncio
import logging
import secrets
from typing import Any

import httpx

from app.models import Contact, ContactKind
from app.repository import Repository

log = logging.getLogger("telegram")

POLL_TIMEOUT_S = 30
RETRY_AFTER_S = 5

LINKED_REPLY = "You're connected, {name}. Steady will message you here if a fall needs your help."
ALREADY_LINKED_REPLY = "You're already connected as {name}. Steady will message you here if a fall needs your help."
NOT_LINKED_REPLY = "This bot sends fall alerts to caregivers. To connect, open the link you were given."


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._http = http or httpx.AsyncClient()
        self._username: str | None = None

    async def call(self, method: str, timeout_s: float = 10.0, **params: Any) -> Any:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            resp = await self._http.post(url, json=params, timeout=timeout_s)
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            # Only the error type: httpx messages can include the URL, which contains the token.
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('error_code')} {body.get('description')}")
        return body["result"]

    async def send_message(self, chat_id: int, text: str) -> None:
        await self.call("sendMessage", chat_id=chat_id, text=text)

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT_S, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        # The HTTP timeout must outlast the long poll, or every quiet poll would look like a failure.
        return await self.call("getUpdates", timeout_s=POLL_TIMEOUT_S + 10, **params)

    async def username(self) -> str:
        """The bot's @username, needed to build t.me links. Asked once, then cached."""
        if self._username is None:
            self._username = (await self.call("getMe"))["username"]
        return self._username

    async def close(self) -> None:
        await self._http.aclose()


class TelegramLinker:
    def __init__(self, client: TelegramClient, repo: Repository) -> None:
        self.client = client
        self.repo = repo

    async def link_for(self, contact: Contact) -> str:
        """A new one-time link for this contact. Replaces any earlier unused link.

        The code is random, not the contact ID: a guessable link would let anyone connect their
        own Telegram to a contact and receive that family's fall alerts.
        """
        username = await self.client.username()  # first, so a Telegram failure doesn't leave a half-made code
        # 22 URL-safe characters, within what Telegram allows in a start link (64, A-Z a-z 0-9 _ -).
        contact.telegram_link_code = secrets.token_urlsafe(16)
        self.repo.save_contact(contact)
        return f"https://t.me/{username}?start={contact.telegram_link_code}"

    async def run(self) -> None:
        """Poll for messages until cancelled. Started with the backend (app/main.py)."""
        offset: int | None = None
        while True:
            try:
                updates = await self.client.get_updates(offset)
            except Exception as e:
                # e.g. Wi-Fi down, or "409 Conflict": another backend is polling with the same bot token.
                log.warning("Telegram polling failed (%s); retrying in %d s", e, RETRY_AFTER_S)
                await asyncio.sleep(RETRY_AFTER_S)
                continue
            for update in updates:
                # Asking for offset = last id + 1 tells Telegram we've seen it, so it isn't sent again.
                offset = update["update_id"] + 1
                try:
                    await self.handle_update(update)
                except Exception:
                    log.exception("Couldn't handle Telegram update %s", update.get("update_id"))

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        if chat_id is None:
            return
        command, _, code = (message.get("text") or "").partition(" ")
        contact = self._contact_with_code(code.strip()) if command == "/start" else None

        if contact is None:
            linked = next((c for c in self.repo.list_contacts() if c.telegram_chat_id == chat_id), None)
            if linked:
                await self.client.send_message(chat_id, ALREADY_LINKED_REPLY.format(name=linked.name))
            else:
                await self.client.send_message(chat_id, NOT_LINKED_REPLY)
            return

        contact.telegram_chat_id = chat_id
        contact.telegram_link_code = None  # one use only
        self.repo.save_contact(contact)
        log.info("Telegram chat linked to contact %s (%s)", contact.id, contact.name)
        await self.client.send_message(chat_id, LINKED_REPLY.format(name=contact.name))

    def _contact_with_code(self, code: str) -> Contact | None:
        if not code:
            return None
        for contact in self.repo.list_contacts():
            if (contact.kind == ContactKind.PERSON and contact.telegram_link_code
                    and secrets.compare_digest(contact.telegram_link_code, code)):
                return contact
        return None
