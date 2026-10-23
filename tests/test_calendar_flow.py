"""End-to-end message flows: Telegram text -> LLM (scripted) -> Google Calendar (fake)."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from Handlers import Calendar_API

TZ = ZoneInfo("Africa/Tunis")


def next_weekday(weekday, after=None):
    day = after or datetime.now(TZ).date()
    day += timedelta(days=1)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day


WED = next_weekday(2)
FRI = next_weekday(4, after=WED)


def at(day: date, hour: int):
    return datetime.combine(day, time(hour), TZ)


def block(action, summary="N/A", start="N/A", end="N/A", location="N/A", description="N/A"):
    lines = [f"Action: {action}"]
    if action != "list":
        lines.append(f"Summary: {summary}")
    if action in ("create", "update"):
        lines += [f"Location: {location}", f"Description: {description}"]
    if action == "list":
        lines.append(f"Location: {location}")
    lines += [f"Start Time: {start}", f"End Time: {end}"]
    if action in ("create", "update"):
        lines.append("Reminders: N/A")
    return "```\n" + "\n".join(lines) + "\n```"


def last(replies):
    return replies[-1].lower()


# --- timezone -----------------------------------------------------------------

def test_prompt_gives_the_model_the_calendar_timezone_and_weekday(calendar, say):
    _, llm = say("What's on my calendar?", block("list"))
    today = datetime.now(TZ)
    assert "Africa/Tunis" in llm.system_prompt
    assert today.strftime("%A") in llm.system_prompt


def test_create_keeps_the_wall_clock_time_the_user_asked_for(calendar, say):
    # The model mimics the old prompt's examples and appends "Z" to a local time.
    replies, _ = say("Book a dentist appointment on Wednesday at 10",
                     block("create", "Dentist appointment", f"{WED}T10:00:00Z", f"{WED}T11:00:00Z"))
    (event,) = calendar.store.values()
    assert calendar.local_start(event) == at(WED, 10)
    assert calendar.local_end(event) == at(WED, 11)
    assert "created" in last(replies)


# --- update -------------------------------------------------------------------

def test_moving_an_event_to_another_day_finds_it_by_name(calendar, say):
    event = calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    replies, _ = say("Move my dentist appointment to Friday at 3",
                     block("update", "Dentist appointment", f"{FRI}T15:00:00", f"{FRI}T16:00:00"))
    moved = calendar.store[event["id"]]
    assert calendar.local_start(moved) == at(FRI, 15)
    assert calendar.local_end(moved) == at(FRI, 16)
    assert "updated" in last(replies)


def test_name_match_ignores_case(calendar, say):
    event = calendar.add("Dentist Appointment", at(WED, 10), at(WED, 11))
    say("Move the dentist appointment to Friday at 3",
        block("update", "dentist appointment", f"{FRI}T15:00:00", f"{FRI}T16:00:00"))
    assert calendar.local_start(calendar.store[event["id"]]) == at(FRI, 15)


def test_moving_with_only_a_new_start_keeps_the_duration(calendar, say):
    event = calendar.add("Team sync", at(WED, 10), at(WED, 11) + timedelta(minutes=30))
    say("Push team sync to Friday at 3", block("update", "Team sync", f"{FRI}T15:00:00", "N/A"))
    moved = calendar.store[event["id"]]
    assert calendar.local_start(moved) == at(FRI, 15)
    assert calendar.local_end(moved) == at(FRI, 16) + timedelta(minutes=30)


def test_updating_an_event_that_does_not_exist_says_so(calendar, say):
    replies, _ = say("Move my yoga class to Friday",
                     block("update", "Yoga class", f"{FRI}T15:00:00", f"{FRI}T16:00:00"))
    assert "no event" in last(replies)
    assert "updated" not in last(replies)


# --- honest replies -----------------------------------------------------------

def test_failed_create_is_not_reported_as_created(calendar, say):
    calendar.failing.add("insert")
    replies, _ = say("Lunch with Sarah on Wednesday at noon",
                     block("create", "Lunch with Sarah", f"{WED}T12:00:00", f"{WED}T13:00:00"))
    assert not calendar.store
    assert "created" not in last(replies)
    assert "couldn't" in last(replies)


def test_failed_update_is_not_reported_as_updated(calendar, say):
    calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    calendar.failing.add("patch")
    replies, _ = say("Move my dentist appointment to Friday at 3",
                     block("update", "Dentist appointment", f"{FRI}T15:00:00", f"{FRI}T16:00:00"))
    assert "updated" not in last(replies)
    assert "couldn't" in last(replies)


# --- delete -------------------------------------------------------------------

def test_delete_without_a_time_finds_the_event_by_name(calendar, say):
    calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    replies, _ = say("Cancel the dentist appointment", block("delete", "Dentist appointment"))
    assert not calendar.store
    assert "deleted" in last(replies)


def test_delete_event_reports_success(calendar):
    event = calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    assert Calendar_API.delete_event(calendar, event["id"]) is True


def test_failed_delete_is_not_reported_as_deleted(calendar, say):
    calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    calendar.failing.add("delete")
    replies, _ = say("Cancel the dentist appointment", block("delete", "Dentist appointment"))
    assert "deleted" not in last(replies)
    assert "couldn't" in last(replies)


# --- list ---------------------------------------------------------------------

def test_list_with_no_time_range_and_no_events_says_so(calendar, say):
    replies, _ = say("What's on my calendar?", block("list"))
    assert replies, "the bot must answer even when there is nothing to list"
    assert "no events" in last(replies)


def test_list_shows_local_times(calendar, say):
    calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    replies, _ = say("What do I have on Wednesday?",
                     block("list", start=f"{WED}T00:00:00", end=f"{WED}T23:59:59"))
    text = "\n".join(replies)
    assert "Dentist appointment" in text
    assert "10:00" in text


# --- fix round 1: ambiguity, delete bookkeeping, raw block ---------------------

def test_two_exact_matches_are_not_acted_on_and_candidates_are_listed(calendar, say):
    calendar.add("Dentist", at(WED, 10), at(WED, 11))
    calendar.add("Dentist", at(FRI, 10), at(FRI, 11))
    replies, _ = say("Cancel the dentist", block("delete", "Dentist"))
    assert len(calendar.store) == 2
    assert "dentist" in last(replies) and WED.strftime("%d") in last(replies) and FRI.strftime("%d") in last(replies)
    assert "deleted" not in last(replies)


def test_several_partial_matches_are_not_acted_on_for_update(calendar, say):
    calendar.add("Dentist checkup", at(WED, 10), at(WED, 11))
    calendar.add("Dentist cleaning", at(FRI, 10), at(FRI, 11))
    before = dict(calendar.store)
    replies, _ = say("Move my dentist", block("update", "dentist", start=at(FRI, 15).replace(tzinfo=None).isoformat()))
    assert calendar.store == before
    assert "dentist checkup" in last(replies) and "dentist cleaning" in last(replies)


def test_exact_match_is_preferred_over_partial(calendar, say):
    calendar.add("Meeting", at(WED, 10), at(WED, 11))
    calendar.add("Meeting with Sarah", at(FRI, 10), at(FRI, 11))
    say("Cancel the meeting", block("delete", "Meeting"))
    assert [e["summary"] for e in calendar.store.values()] == ["Meeting with Sarah"]


def test_single_partial_match_is_still_found(calendar, say):
    calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    say("Cancel the dentist", block("delete", "dentist"))
    assert not calendar.store


def test_a_shorter_event_title_is_not_matched_by_a_longer_request(calendar, say):
    calendar.add("Meeting", at(WED, 10), at(WED, 11))
    replies, _ = say("Cancel meeting with Sarah and team", block("delete", "Meeting with Sarah and team"))
    assert len(calendar.store) == 1
    assert "no event found" in last(replies)


def test_delete_replies_even_if_local_bookkeeping_fails(calendar, say, monkeypatch):
    calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))

    def boom(event_id):
        raise RuntimeError("db locked")
    monkeypatch.setattr(Calendar_API, "delete_event_from_db", boom)
    replies, _ = say("Cancel the dentist appointment", block("delete", "Dentist appointment"))
    assert not calendar.store
    assert "deleted" in last(replies)


def test_raw_action_block_is_not_echoed_to_the_user(calendar, say):
    calendar.add("Dentist appointment", at(WED, 10), at(WED, 11))
    replies, _ = say("Cancel the dentist appointment", block("delete", "Dentist appointment"))
    assert len(replies) == 1
    assert not any("Action:" in r for r in replies)


def test_plain_chat_reply_is_still_sent_as_is(calendar, say):
    replies, _ = say("hello", "Hi! I can manage your calendar.")
    assert replies[0] == "Hi! I can manage your calendar."


def test_find_event_does_not_match_a_title_contained_in_the_request(calendar):
    # Google's q search is fuzzy, so the service can return "Meeting" for a longer request.
    calendar.add("Meeting", at(WED, 10), at(WED, 11))
    calendar.events().list = lambda **kw: type("R", (), {"execute": lambda self: {"items": list(calendar.store.values())}})()
    assert Calendar_API.find_event(calendar, "Meeting with Sarah and team", "Africa/Tunis") is None


# --- AI call failure ----------------------------------------------------------

def _say_with(llm, text):
    import asyncio
    from types import SimpleNamespace
    import bot
    from conftest import FakeMessage, _noop
    replies = []
    update = SimpleNamespace(message=FakeMessage(text, replies),
                             effective_user=SimpleNamespace(id=4242, first_name="Demo"),
                             effective_chat=SimpleNamespace(id=4242))
    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=_noop))
    asyncio.run(bot.handle_message(update, context, client=llm))
    return replies


def test_failed_ai_call_sends_one_message_and_no_action_parsing(calendar):
    from conftest import FailingLLM
    replies = _say_with(FailingLLM(), "Book a dentist appointment on Wednesday at 10")
    assert len(replies) == 1
    assert "trouble" in replies[0].lower()
    assert "couldn't work out" not in replies[0].lower()
    assert not calendar.store
