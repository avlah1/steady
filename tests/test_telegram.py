"""Telegram alerts and linking, with a fake Telegram client (no network)."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.deps import get_telegram_bot, get_repo
from app.incidents import IncidentFlow
from app.main import app
from app.models import Contact, ContactKind, Incident, Trigger as T
from app.notifier import TelegramNotifier, alert_text
from app.repository import InMemoryRepository
from app.incidents import HELP_COMING_PROMPT
from app.models import IncidentState as S
from app.telegram import (
    ALREADY_LINKED_REPLY,
    LINKED_REPLY,
    NOT_LINKED_REPLY,
    TelegramBot,
    TelegramError,
    ack_button,
)
from tests.conftest import TEST_API_KEY

AUTH = {"X-API-Key": TEST_API_KEY}
CHAT_ID = 8999464616


class FakeTelegram:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[int, str]] = []
        self.buttons: list[dict | None] = []  # buttons under each sent message, same order as sent
        self.edits: list[tuple[int, int, str]] = []
        self.answers: list[tuple[str, str]] = []
        self.updates: list[list[dict]] = []  # one batch per get_updates call
        self.offsets: list[int | None] = []

    async def send_message(self, chat_id: int, text: str, buttons: dict | None = None) -> None:
        if self.fail:
            raise TelegramError("sendMessage: ConnectError")
        self.sent.append((chat_id, text))
        self.buttons.append(buttons)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edits.append((chat_id, message_id, text))

    async def answer_button(self, query_id: str, text: str) -> None:
        self.answers.append((query_id, text))

    async def username(self) -> str:
        if self.fail:
            raise TelegramError("getMe: ConnectError")
        return "steady_test_bot"

    async def get_updates(self, offset: int | None) -> list[dict]:
        self.offsets.append(offset)
        if not self.updates:
            await asyncio.sleep(3600)  # like a long poll with nothing new
        return self.updates.pop(0)


def contacts() -> list[Contact]:
    # Fresh objects every test: linking changes contacts in place.
    return [
        Contact(id="contact-1", name="Andrew"),
        Contact(id="emergency", name="Emergency services", kind=ContactKind.EMERGENCY),
        Contact(id="contact-2", name="Caregiver 2"),
    ]


def start_message(text: str, chat_id: int = CHAT_ID, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "type": "private"}, "text": text}}


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def tg_repo() -> InMemoryRepository:
    return InMemoryRepository(contacts=contacts())


@pytest.fixture
def bot(tg, tg_repo, tg_flow) -> TelegramBot:
    return TelegramBot(tg, tg_repo, tg_flow)


# --- Linking ---


@pytest.mark.anyio
async def test_link_uses_a_random_code_not_the_contact_id(bot, tg_repo):
    contact = tg_repo.get_contact("contact-1")

    link = await bot.link_for(contact)

    code = contact.telegram_link_code
    assert link == f"https://t.me/steady_test_bot?start={code}"
    assert "contact-1" not in link and len(code) >= 20


@pytest.mark.anyio
async def test_tapping_the_link_saves_the_chat_id(bot, tg, tg_repo):
    link = await bot.link_for(tg_repo.get_contact("contact-1"))
    code = link.split("start=")[1]

    await bot.handle_update(start_message(f"/start {code}"))

    contact = tg_repo.get_contact("contact-1")
    assert contact.telegram_chat_id == CHAT_ID
    assert contact.telegram_link_code is None
    assert tg.sent == [(CHAT_ID, LINKED_REPLY.format(name="Andrew"))]


@pytest.mark.anyio
async def test_link_works_only_once(bot, tg, tg_repo):
    link = await bot.link_for(tg_repo.get_contact("contact-1"))
    code = link.split("start=")[1]
    await bot.handle_update(start_message(f"/start {code}"))

    await bot.handle_update(start_message(f"/start {code}", chat_id=666))  # someone else reuses it

    assert tg_repo.get_contact("contact-1").telegram_chat_id == CHAT_ID
    assert tg.sent[-1] == (666, NOT_LINKED_REPLY)


@pytest.mark.anyio
async def test_a_new_link_replaces_the_old_one(bot, tg, tg_repo):
    contact = tg_repo.get_contact("contact-1")
    old_code = (await bot.link_for(contact)).split("start=")[1]
    await bot.link_for(contact)

    await bot.handle_update(start_message(f"/start {old_code}"))

    assert contact.telegram_chat_id is None


@pytest.mark.parametrize("text", ["/start wrong-code", "/start", "Hi"])
@pytest.mark.anyio
async def test_unknown_messages_get_instructions(bot, tg, tg_repo, text):
    await bot.link_for(tg_repo.get_contact("contact-1"))

    await bot.handle_update(start_message(text))

    assert tg_repo.get_contact("contact-1").telegram_chat_id is None
    assert tg.sent == [(CHAT_ID, NOT_LINKED_REPLY)]


@pytest.mark.anyio
async def test_linked_caregiver_messaging_again_is_told_they_are_connected(bot, tg, tg_repo):
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID

    await bot.handle_update(start_message("Hi"))

    assert tg.sent == [(CHAT_ID, ALREADY_LINKED_REPLY.format(name="Andrew"))]


@pytest.mark.anyio
async def test_polling_confirms_each_update_and_keeps_going_after_a_bad_one(bot, tg, tg_repo):
    code = (await bot.link_for(tg_repo.get_contact("contact-1"))).split("start=")[1]
    tg.updates = [[{"update_id": 7}, start_message(f"/start {code}", update_id=8)]]  # first has no message

    task = asyncio.create_task(bot.run())
    while len(tg.offsets) < 2:
        await asyncio.sleep(0.001)
    task.cancel()

    assert tg.offsets == [None, 9]  # next poll asks for after the last update seen
    assert tg_repo.get_contact("contact-1").telegram_chat_id == CHAT_ID


# --- Alerts ---


async def escalate(flow: IncidentFlow, trigger: T = T.NEEDS_HELP) -> Incident:
    incident = Incident(camera_id="living_room")
    flow.repo.save_incident(incident)
    await flow.handle(incident, T.NEW_FALL)
    await flow.handle(incident, trigger)
    return incident


@pytest.fixture
async def tg_flow(tg, tg_repo, box):
    notifier = TelegramNotifier(tg)
    f = IncidentFlow(tg_repo, box, notifier, reply_timeout_s=60, ack_timeout_s=60)
    yield f
    f.cancel_all_timers()


@pytest.mark.anyio
async def test_escalation_messages_the_linked_caregiver(tg_flow, tg, tg_repo):
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID

    incident = await escalate(tg_flow)
    await tg_flow.notifier.flush()

    assert tg.sent == [(CHAT_ID, alert_text(incident))]
    assert tg.buttons == [ack_button(incident.id)]
    assert "living_room" in tg.sent[0][1]
    assert "They said they need help." in tg.sent[0][1]


@pytest.mark.anyio
async def test_alert_says_when_they_did_not_answer(tg_flow, tg, tg_repo):
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID
    incident = Incident(camera_id="living_room")
    tg_flow.repo.save_incident(incident)
    await tg_flow.handle(incident, T.NEW_FALL)
    for attempt in (1, 2, 3):
        await tg_flow.handle(incident, T.REPLY_TIMEOUT, {"attempt": attempt})
    await tg_flow.notifier.flush()

    assert "They didn't say they were okay when asked." in tg.sent[0][1]


@pytest.mark.anyio
async def test_unlinked_caregiver_is_only_logged(tg_flow, tg, caplog):
    with caplog.at_level(logging.WARNING):
        await escalate(tg_flow)
        await tg_flow.notifier.flush()

    assert tg.sent == []
    assert "has no Telegram linked" in caplog.text
    assert tg_flow.notifier.sent  # the log line still went out


@pytest.mark.anyio
async def test_emergency_services_never_gets_a_telegram(tg_flow, tg, tg_repo):
    emergency = tg_repo.get_contact("emergency")
    emergency.telegram_chat_id = 123  # even if one is set by mistake
    tg_repo.get_contact("contact-2").telegram_chat_id = CHAT_ID

    incident = await escalate(tg_flow)
    await tg_flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": incident.contact_position})
    await tg_flow.notifier.flush()

    assert [chat for chat, _ in tg.sent] == [CHAT_ID]  # contact-2 only, after emergency was logged


@pytest.mark.anyio
async def test_failed_send_is_logged_and_escalation_continues(tg_repo, box, caplog):
    flow = IncidentFlow(tg_repo, box, TelegramNotifier(FakeTelegram(fail=True)), reply_timeout_s=60, ack_timeout_s=60)
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID

    with caplog.at_level(logging.ERROR):
        incident = await escalate(flow)
        await flow.notifier.flush()

    assert "Telegram alert to contact-1 failed" in caplog.text
    assert incident.id in flow._timers  # ack timer running: the next contact will still be alerted
    flow.cancel_all_timers()


# --- "I'm on it" button ---


def tap(incident_id: str, user_id: int = CHAT_ID, query_id: str = "q1") -> dict:
    """What Telegram sends when someone taps the button under an alert."""
    return {
        "update_id": 50,
        "callback_query": {
            "id": query_id,
            "from": {"id": user_id},
            "data": ack_button(incident_id)["inline_keyboard"][0][0]["callback_data"],
            "message": {"message_id": 99, "chat": {"id": user_id}, "text": "⚠️ Possible fall"},
        },
    }


@pytest.mark.anyio
async def test_tapping_the_button_acknowledges(bot, tg_flow, tg, tg_repo, box):
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID
    incident = await escalate(tg_flow)

    await bot.handle_update(tap(incident.id))

    assert tg_repo.get_incident(incident.id).state == S.ACKNOWLEDGED
    assert box.said[-1] == (incident.id, HELP_COMING_PROMPT)
    assert tg.answers == [("q1", "Thanks. They've been told help is coming.")]
    assert tg.edits == [(CHAT_ID, 99, "⚠️ Possible fall\n\n✓ Andrew is on it.")]  # the button is gone


@pytest.mark.anyio
async def test_second_tap_says_someone_is_on_it(bot, tg_flow, tg, tg_repo):
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID
    tg_repo.get_contact("contact-2").telegram_chat_id = 222
    incident = await escalate(tg_flow)
    await bot.handle_update(tap(incident.id))

    await bot.handle_update(tap(incident.id, user_id=222, query_id="q2"))

    assert tg.answers[-1] == ("q2", "Someone is already on it.")


@pytest.mark.anyio
async def test_tap_after_person_said_they_are_fine_changes_nothing(bot, tg_flow, tg, tg_repo):
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID
    incident = Incident(camera_id="living_room")
    tg_flow.repo.save_incident(incident)
    await tg_flow.handle(incident, T.NEW_FALL)
    await tg_flow.handle(incident, T.OK)

    await bot.handle_update(tap(incident.id))

    assert tg_repo.get_incident(incident.id).state == S.RESOLVED_OK
    assert tg.answers == [("q1", "This incident is already over.")]


@pytest.mark.anyio
async def test_tap_from_someone_not_connected_is_refused(bot, tg_flow, tg, tg_repo):
    incident = await escalate(tg_flow)

    await bot.handle_update(tap(incident.id, user_id=666))  # e.g. the alert was forwarded to them

    assert tg_repo.get_incident(incident.id).state == S.ESCALATED
    assert tg.answers == [("q1", "You're not connected as a caregiver.")]


@pytest.mark.anyio
async def test_tap_for_unknown_incident(bot, tg, tg_repo):
    tg_repo.get_contact("contact-1").telegram_chat_id = CHAT_ID

    await bot.handle_update(tap("no-such-incident"))

    assert tg.answers == [("q1", "That incident wasn't found.")]


# --- Endpoint ---


@pytest.fixture
def api(tg_repo):
    def make(bot):
        app.dependency_overrides[get_settings] = lambda: Settings(api_key=TEST_API_KEY)
        app.dependency_overrides[get_repo] = lambda: tg_repo
        app.dependency_overrides[get_telegram_bot] = lambda: bot
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def link_bot(tg, tg_repo) -> TelegramBot:
    # No state machine: these sync endpoint tests only make links, and IncidentFlow needs an event loop.
    return TelegramBot(tg, tg_repo, flow=None)


def post_link(client, contact_id: str = "contact-1", headers=AUTH):
    return client.post(f"/api/v1/contacts/{contact_id}/telegram-link", headers=headers)


def test_endpoint_returns_a_link(api, link_bot, tg_repo):
    resp = post_link(api(link_bot))

    assert resp.status_code == 200
    code = tg_repo.get_contact("contact-1").telegram_link_code
    assert resp.json() == {"contact_id": "contact-1", "link": f"https://t.me/steady_test_bot?start={code}"}


@pytest.mark.parametrize(
    ("contact_id", "status"),
    [("nobody", 404), ("emergency", 400)],
)
def test_endpoint_rejects_bad_contacts(api, link_bot, contact_id, status):
    assert post_link(api(link_bot), contact_id).status_code == status


def test_endpoint_without_bot_token(api):
    assert post_link(api(None)).status_code == 503


def test_endpoint_when_telegram_is_unreachable(api, tg_repo):
    resp = post_link(api(TelegramBot(FakeTelegram(fail=True), tg_repo, flow=None)))

    assert resp.status_code == 503
    assert tg_repo.get_contact("contact-1").telegram_link_code is None


def test_endpoint_needs_api_key(api, link_bot):
    assert post_link(api(link_bot), headers={}).status_code == 401
