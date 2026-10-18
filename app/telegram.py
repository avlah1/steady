"""Telegram: the API client, linking a caregiver's Telegram chat to their contact, and acks.

A bot can only message someone who has messaged it first, and that's the only time Telegram
tells us their chat ID. So each caregiver gets a one-time link (t.me/<bot>?start=<code>).
Tapping Start sends "/start <code>" to the bot; TelegramBot matches the code to the contact
and saves the chat ID on it. Alerts are sent by TelegramNotifier (app/notifier.py).

Messages are received by long polling getUpdates: the backend asks "anything new?" and Telegram
holds the request open for up to 30 s until something arrives. It only makes outgoing requests,
so it works on the venue Wi-Fi. (A webhook would need a public URL for Telegram to call.)

Each alert has an "I'm on it" button. Tapping it reaches us through the same polling, and
acknowledges the incident exactly like the dashboard's ack: handle(incident, "ack").
"""

import asyncio
import logging
import secrets
from typing import TYPE_CHECKING, Any

import httpx

from app.models import Contact, ContactKind, IncidentState, Trigger
from app.repository import Repository

if TYPE_CHECKING:  # only for type hints: a real import would be circular (incidents -> notifier -> telegram)
    from app.incidents import IncidentFlow

log = logging.getLogger("telegram")

POLL_TIMEOUT_S = 30
RETRY_AFTER_S = 5

LINKED_REPLY = "You're connected, {name}. Steady will message you here if a fall needs your help."
ALREADY_LINKED_REPLY = "You're already connected as {name}. Steady will message you here if a fall needs your help."
NOT_LINKED_REPLY = "This bot sends fall alerts to caregivers. To connect, open the link you were given."

ACK_BUTTON = "I'm on it"
ACK_PREFIX = "ack:"  # button data is "ack:<incident id>"; Telegram allows 64 bytes, a uuid needs 40


def ack_button(incident_id: str) -> dict[str, Any]:
    """The "I'm on it" button, attached under an alert message."""
    return {"inline_keyboard": [[{"text": ACK_BUTTON, "callback_data": ACK_PREFIX + incident_id}]]}


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

    async def send_message(self, chat_id: int, text: str, buttons: dict[str, Any] | None = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = buttons
        await self.call("sendMessage", **params)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace a message's text. Its buttons are removed, since none are passed."""
        await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)

    async def answer_button(self, query_id: str, text: str) -> None:
        """Every button tap must be answered, or the button shows a spinner. Text appears as a brief popup."""
        await self.call("answerCallbackQuery", callback_query_id=query_id, text=text)

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT_S, "allowed_updates": ["message", "callback_query"]}
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


class TelegramBot:
    """Everything that arrives from Telegram: link messages and "I'm on it" taps."""

    def __init__(self, client: TelegramClient, repo: Repository, flow: "IncidentFlow") -> None:
        self.client = client
        self.repo = repo
        self.flow = flow

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
        if "callback_query" in update:
            await self._handle_button(update["callback_query"])
            return
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

    async def _handle_button(self, query: dict[str, Any]) -> None:
        data = query.get("data") or ""
        if not data.startswith(ACK_PREFIX):
            await self.client.answer_button(query["id"], "")
            return
        # Who tapped, not which chat: in a private chat they're the same, but checking the person is what matters.
        user_id = query["from"]["id"]
        contact = next((c for c in self.repo.list_contacts() if c.telegram_chat_id == user_id), None)
        incident = self.repo.get_incident(data.removeprefix(ACK_PREFIX))
        if contact is None:
            await self.client.answer_button(query["id"], "You're not connected as a caregiver.")
            return
        if incident is None:
            await self.client.answer_button(query["id"], "That incident wasn't found.")
            return

        before = incident.state
        await self.flow.handle(incident, Trigger.ACK)
        after = self.repo.get_incident(incident.id).state
        log.info("Telegram ack from %s for incident %s: %s -> %s", contact.id, incident.id, before, after)

        if after == IncidentState.ACKNOWLEDGED and before != IncidentState.ACKNOWLEDGED:
            popup, footer = "Thanks. They've been told help is coming.", f"✓ {contact.name} is on it."
        elif after == IncidentState.ACKNOWLEDGED:
            popup, footer = "Someone is already on it.", "✓ Someone is already on it."
        else:  # e.g. RESOLVED_OK: the person said they were fine before anyone escalated
            popup, footer = "This incident is already over.", "Already over: no action needed."
        await self.client.answer_button(query["id"], popup)
        message = query.get("message")
        if message:  # Telegram leaves it out for very old messages
            await self.client.edit_message_text(
                message["chat"]["id"], message["message_id"], f"{message.get('text', '')}\n\n{footer}"
            )

    def _contact_with_code(self, code: str) -> Contact | None:
        if not code:
            return None
        for contact in self.repo.list_contacts():
            if (contact.kind == ContactKind.PERSON and contact.telegram_link_code
                    and secrets.compare_digest(contact.telegram_link_code, code)):
                return contact
        return None
