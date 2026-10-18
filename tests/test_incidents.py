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
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 1})
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 2})

    assert incident.state == S.ESCALATED
    assert [c for c, _ in notifier.sent] == ["contact-1", "contact-2", "contact-1"]


def emergency_flow(box, notifier, contacts: list[Contact]) -> IncidentFlow:
    return IncidentFlow(InMemoryRepository(contacts=contacts), box, notifier, reply_timeout_s=60, ack_timeout_s=60)


EMERGENCY = Contact(id="emergency", name="Emergency services", kind=ContactKind.EMERGENCY)
C1 = Contact(id="contact-1", name="Caregiver 1")
C2 = Contact(id="contact-2", name="Caregiver 2")


async def test_emergency_alert_does_not_wait_for_ack(box, notifier, caplog):
    flow = emergency_flow(box, notifier, [C1, EMERGENCY, C2])
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)  # contact-1
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 1})  # emergency, then contact-2 straight away
    flow.cancel_all_timers()

    assert [c for c, _ in notifier.sent] == ["contact-1", "emergency", "contact-2"]
    assert incident.state == S.ESCALATED  # still needs a person to ack
    assert "EMERGENCY SERVICES (simulated)" in caplog.text


async def test_emergency_first_in_order_alerts_next_person_too(box, notifier):
    flow = emergency_flow(box, notifier, [EMERGENCY, C1])
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    flow.cancel_all_timers()

    assert [c for c, _ in notifier.sent] == ["emergency", "contact-1"]


async def test_emergency_alerted_only_once_on_wrap_around(box, notifier):
    flow = emergency_flow(box, notifier, [C1, EMERGENCY, C2])
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 1})
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 3})  # back to contact-1
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 4})  # skips emergency
    flow.cancel_all_timers()

    assert [c for c, _ in notifier.sent] == ["contact-1", "emergency", "contact-2", "contact-1", "contact-2"]


async def test_only_emergency_contact_alerts_once_and_keeps_waiting(box, notifier):
    flow = emergency_flow(box, notifier, [EMERGENCY])
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 1})

    assert [c for c, _ in notifier.sent] == ["emergency"]
    assert incident.state == S.ESCALATED
    assert incident.id in flow._timers  # still waiting for someone to ack
    flow.cancel_all_timers()


async def test_failed_emergency_alert_still_alerts_next_person(box, notifier, monkeypatch):
    real_alert = notifier.alert

    async def flaky_alert(contact, incident):
        if contact.kind == ContactKind.EMERGENCY:
            raise RuntimeError("simulated outage")
        await real_alert(contact, incident)

    monkeypatch.setattr(notifier, "alert", flaky_alert)
    flow = emergency_flow(box, notifier, [EMERGENCY, C1])
    incident = await new_incident(flow)
    await flow.handle(incident, T.NEEDS_HELP)
    flow.cancel_all_timers()

    assert [c for c, _ in notifier.sent] == ["contact-1"]


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
    await flow.handle(incident, trigger, {"attempt": 1, "contact_position": 0})

    assert incident.state == S.RESOLVED_OK


@pytest.mark.parametrize("trigger", list(T))
async def test_acknowledged_ignores_everything(flow, trigger):
    incident = await new_incident(flow)
    await flow.handle(incident, T.ACK)
    await flow.handle(incident, trigger, {"attempt": 1, "contact_position": 1})

    assert incident.state == S.ACKNOWLEDGED


async def test_unknown_trigger_raises(flow):
    incident = await new_incident(flow)
    with pytest.raises(ValueError):
        await flow.handle(incident, "needs-help")


async def test_timeline_records_every_transition(flow):
    incident = await new_incident(flow)
    await flow.handle(incident, T.UNCLEAR)
    await flow.handle(incident, T.NEEDS_HELP)
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 1})
    await flow.handle(incident, T.ACK)

    assert [(e.from_state, e.to_state, e.trigger) for e in incident.timeline] == [
        (S.OPEN, S.ASKING, T.NEW_FALL),
        (S.ASKING, S.ASKING, T.UNCLEAR),
        (S.ASKING, S.ESCALATED, T.NEEDS_HELP),
        (S.ESCALATED, S.ESCALATED, T.ACK_TIMEOUT),
        (S.ESCALATED, S.ACKNOWLEDGED, T.ACK),
    ]


# --- Stale timers and races ---


async def test_cancel_stops_a_timer_already_waiting_for_the_lock(repo, box, notifier, monkeypatch):
    # Cancelling alone must be enough, so turn the stale check off to prove it.
    monkeypatch.setattr(IncidentFlow, "_is_stale_timer", staticmethod(lambda *args: False))
    flow = IncidentFlow(repo, box, notifier, reply_timeout_s=0.02, ack_timeout_s=60)
    incident = await new_incident(flow)
    timer = flow._timers[incident.id]

    async with flow._locks[incident.id]:  # something else holds the lock...
        await asyncio.sleep(0.1)  # ...well past the 0.02 s timeout, so the timer has fired and is waiting
        assert not timer.done()
        flow._cancel_timer(incident.id)  # what every transition does

    await wait_until(timer.done)
    assert timer.cancelled()
    assert incident.attempt == 1  # the timer never asked again


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
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 1})  # alerts contact-2
    await flow.handle(incident, T.ACK_TIMEOUT, {"contact_position": 1})  # old timer, stale

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


# --- Restart: timers are re-armed ---


def restored(repo, **fields) -> Incident:
    """An incident as it comes back from storage after a restart: saved state, no timer."""
    incident = Incident(camera_id="living_room", **fields)
    repo.save_incident(incident)
    return incident


async def test_restart_rearms_reply_timer_without_repeating_the_question(fast_flow, repo, box):
    incident = restored(repo, state=S.ASKING, attempt=2)

    await fast_flow.resume_after_restart([incident])

    assert incident.state == S.ASKING and box.said == []  # nothing said, nothing changed yet
    await wait_until(lambda: incident.attempt == 3)  # no answer: the timer goes off and asks again
    assert said(box) == [ASK_AGAIN_PROMPT]


async def test_restart_on_last_attempt_still_escalates(fast_flow, repo, notifier):
    incident = restored(repo, state=S.ASKING, attempt=3)

    await fast_flow.resume_after_restart([incident])

    await wait_until(lambda: incident.state == S.ESCALATED)
    assert notifier.sent[0] == ("contact-1", incident.id)


async def test_restart_rearms_ack_timer_without_realerting(fast_flow, repo, notifier):
    incident = restored(repo, state=S.ESCALATED, attempt=3, contact_position=1)

    await fast_flow.resume_after_restart([incident])

    assert notifier.sent == []  # contact-1 was alerted before the restart
    await wait_until(lambda: len(notifier.sent) == 1)  # no ack: the next contact is alerted
    assert notifier.sent[0] == ("contact-2", incident.id)


async def test_restart_before_first_question_asks_it(flow, repo, box):
    incident = restored(repo)  # still OPEN

    await flow.resume_after_restart([incident])

    assert incident.state == S.ASKING and incident.attempt == 1
    assert said(box) == [ASK_PROMPT]


async def test_restart_leaves_finished_incidents_alone(flow, repo, box):
    done = [restored(repo, state=S.RESOLVED_OK), restored(repo, state=S.ACKNOWLEDGED)]

    await flow.resume_after_restart(done)

    assert box.said == [] and flow._timers == {}


async def test_reply_after_restart_works_normally(flow, repo):
    incident = restored(repo, state=S.ASKING, attempt=1)
    await flow.resume_after_restart([incident])

    await flow.handle(incident, T.OK)

    assert incident.state == S.RESOLVED_OK
    assert incident.id not in flow._timers  # the re-armed timer was cancelled like any other


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
