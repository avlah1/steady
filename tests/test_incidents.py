"""State machine tests: call IncidentFlow.handle() directly, no HTTP."""

import asyncio

import pytest

from app.incidents import (
    ASK_AGAIN_PROMPT,
    ASK_PROMPT,
    ESCALATING_PROMPT,
    HELP_COMING_PROMPT,
    RESOLVED_PROMPT,
    IncidentFlow,
)
from app.models import Contact, ContactKind, Incident, IncidentState as S, Trigger as T
from app.repository import InMemoryRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
async def flow(repo, box, notifier):
    # Long timeouts: timers never fire on their own; tests send timeout triggers by hand.
    f = IncidentFlow(repo, box, notifier, reply_timeout_s=60, ack_timeout_s=60)
    yield f
    f.cancel_all_timers()


@pytest.fixture
async def fast_flow(repo, box, notifier):
    # Real timers with tiny durations, for end-to-end timing tests.
    f = IncidentFlow(repo, box, notifier, reply_timeout_s=0.02, ack_timeout_s=0.02)
    yield f
    f.cancel_all_timers()


async def new_incident(flow: IncidentFlow) -> Incident:
    incident = Incident(camera_id="living_room")
    flow.repo.save_incident(incident)
    await flow.handle(incident, T.NEW_FALL)
    return incident


async def wait_until(condition, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


def said(box) -> list[str]:
    return [text for _, text in box.said]


# --- Asking ---


async def test_new_fall_asks_and_starts_reply_timer(flow, box):
    incident = await new_incident(flow)

    assert incident.state == S.ASKING
    assert incident.attempt == 1
    assert said(box) == [ASK_PROMPT]
    assert incident.id in flow._timers


async def test_ok_resolves(flow, box):
    incident = await new_incident(flow)
    await flow.handle(incident, T.OK)

    assert incident.state == S.RESOLVED_OK
    assert said(box)[-1] == RESOLVED_PROMPT
    assert incident.id not in flow._timers


async def test_unclear_asks_again_then_escalates_after_third_attempt(flow, box, notifier):
    incident = await new_incident(flow)

    await flow.handle(incident, T.UNCLEAR)
    assert (incident.state, incident.attempt) == (S.ASKING, 2)
    await flow.handle(incident, T.UNCLEAR)
    assert (incident.state, incident.attempt) == (S.ASKING, 3)
    assert said(box) == [ASK_PROMPT, ASK_AGAIN_PROMPT, ASK_AGAIN_PROMPT]

    await flow.handle(incident, T.UNCLEAR)
    assert incident.state == S.ESCALATED
    assert notifier.sent == [("contact-1", incident.id)]


async def test_reply_timeout_counts_as_failed_attempt(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.REPLY_TIMEOUT, {"attempt": 1})

    assert (incident.state, incident.attempt) == (S.ASKING, 2)


async def test_needs_help_escalates_immediately(flow, box, notifier):
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)

    assert incident.state == S.ESCALATED
    assert notifier.sent == [("contact-1", incident.id)]
    assert said(box)[-1] == ESCALATING_PROMPT
    assert incident.id in flow._timers  # ack timer


async def test_needs_help_on_later_attempt_escalates(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.UNCLEAR)
    await flow.handle(incident, T.NEEDS_HELP)

    assert incident.state == S.ESCALATED


# --- Escalation and ack ---


async def test_ack_timeout_alerts_next_contact_and_wraps_around(flow, notifier):
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK_TIMEOUT, {"alerts_sent": 1})
    await flow.handle(incident, T.ACK_TIMEOUT, {"alerts_sent": 2})

    assert incident.state == S.ESCALATED
    assert [c for c, _ in notifier.sent] == ["contact-1", "contact-2", "contact-1"]


async def test_emergency_contact_alerted_in_its_place_in_the_order(box, notifier, caplog):
    contacts = [
        Contact(id="contact-1", name="Caregiver 1"),
        Contact(id="emergency", name="Emergency services", kind=ContactKind.EMERGENCY),
        Contact(id="contact-2", name="Caregiver 2"),
    ]
    flow = IncidentFlow(InMemoryRepository(contacts=contacts), box, notifier, reply_timeout_s=60, ack_timeout_s=60)
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK_TIMEOUT, {"alerts_sent": 1})
    await flow.handle(incident, T.ACK_TIMEOUT, {"alerts_sent": 2})
    flow.cancel_all_timers()

    assert [c for c, _ in notifier.sent] == ["contact-1", "emergency", "contact-2"]
    assert "EMERGENCY SERVICES (simulated)" in caplog.text


async def test_ack_while_escalated(flow, box):
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK)

    assert incident.state == S.ACKNOWLEDGED
    assert said(box)[-1] == HELP_COMING_PROMPT
    assert incident.id not in flow._timers


async def test_early_ack_while_asking(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.ACK)

    assert incident.state == S.ACKNOWLEDGED


async def test_late_ok_does_not_cancel_escalation(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.OK)

    assert incident.state == S.ESCALATED


async def test_escalation_with_no_contacts_still_escalates(box, notifier):
    flow = IncidentFlow(InMemoryRepository(contacts=[]), box, notifier, reply_timeout_s=60, ack_timeout_s=60)
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)

    assert incident.state == S.ESCALATED
    assert notifier.sent == []
    assert incident.id in flow._timers  # keeps retrying in case contacts are added
    flow.cancel_all_timers()


# --- Terminal states and bad input ---


@pytest.mark.parametrize("trigger", list(T))
async def test_resolved_ignores_everything(flow, trigger):
    incident = await new_incident(flow)
    await flow.handle(incident, T.OK)
    await flow.handle(incident, trigger, {"attempt": 1, "alerts_sent": 0})

    assert incident.state == S.RESOLVED_OK


@pytest.mark.parametrize("trigger", list(T))
async def test_acknowledged_ignores_everything(flow, trigger):
    incident = await new_incident(flow)
    await flow.handle(incident, T.ACK)
    await flow.handle(incident, trigger, {"attempt": 1, "alerts_sent": 1})

    assert incident.state == S.ACKNOWLEDGED


async def test_unknown_trigger_raises(flow):
    incident = await new_incident(flow)
    with pytest.raises(ValueError):
        await flow.handle(incident, "needs-help")


async def test_timeline_records_every_transition(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.UNCLEAR)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK_TIMEOUT, {"alerts_sent": 1})
    await flow.handle(incident, T.ACK)

    assert [(e.from_state, e.to_state, e.trigger) for e in incident.timeline] == [
        (S.OPEN, S.ASKING, T.NEW_FALL),
        (S.ASKING, S.ASKING, T.UNCLEAR),
        (S.ASKING, S.ESCALATED, T.NEEDS_HELP),
        (S.ESCALATED, S.ESCALATED, T.ACK_TIMEOUT),
        (S.ESCALATED, S.ACKNOWLEDGED, T.ACK),
    ]


# --- Stale timers and races ---


async def test_stale_reply_timeout_ignored(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.UNCLEAR)  # now on attempt 2
    await flow.handle(incident, T.REPLY_TIMEOUT, {"attempt": 1})  # timer from attempt 1

    assert (incident.state, incident.attempt) == (S.ASKING, 2)


async def test_stale_reply_timeout_after_resolve_ignored(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.OK)
    await flow.handle(incident, T.REPLY_TIMEOUT, {"attempt": 1})

    assert incident.state == S.RESOLVED_OK


async def test_stale_ack_timeout_ignored(flow, notifier):
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK_TIMEOUT, {"alerts_sent": 1})  # alerts contact-2
    await flow.handle(incident, T.ACK_TIMEOUT, {"alerts_sent": 1})  # old timer, stale

    assert len(notifier.sent) == 2


async def test_reply_and_timeout_at_same_time_never_escalate(flow, notifier):
    # Both arrive together; the lock runs them one after the other.
    # Whichever goes first, the person said "ok", so the incident must end resolved.
    incident = await new_incident(flow)
    await asyncio.gather(
        flow.handle(incident, T.REPLY_TIMEOUT, {"attempt": 1}),
        flow.handle(incident, T.OK),
    )

    assert incident.state == S.RESOLVED_OK
    assert notifier.sent == []


# --- Real timers ---


async def test_no_reply_escalates_and_cycles_contacts_on_its_own(fast_flow, box, notifier):
    incident = await new_incident(fast_flow)

    await wait_until(lambda: len(notifier.sent) >= 3)

    assert said(box)[:4] == [ASK_PROMPT, ASK_AGAIN_PROMPT, ASK_AGAIN_PROMPT, ESCALATING_PROMPT]
    assert [c for c, _ in notifier.sent[:3]] == ["contact-1", "contact-2", "contact-1"]
    assert incident.state == S.ESCALATED


async def test_reply_cancels_pending_timer(fast_flow, notifier):
    incident = await new_incident(fast_flow)
    await fast_flow.handle(incident, T.OK)
    await asyncio.sleep(0.1)  # well past the reply timeout

    assert incident.state == S.RESOLVED_OK
    assert notifier.sent == []
