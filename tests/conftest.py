import itertools
import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

# DB.py opens DATABASE_URL at import time, so point it at a throwaway file first.
os.environ["DATABASE_URL"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402

TZ = "Africa/Tunis"


class Request:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


def _aware(when, fallback_tz=None):
    """Resolve a Calendar {'dateTime', 'timeZone'} dict the way Google does."""
    value = when["dateTime"].replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        tz = when.get("timeZone") or fallback_tz
        if tz is None:
            raise ValueError("400: dateTime without offset requires timeZone")
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed


def _bound(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"400: timeMin/timeMax must be RFC3339 with an offset, got {value!r}")
    return parsed


class FakeEvents:
    def __init__(self, calendar):
        self.cal = calendar

    def insert(self, calendarId, body):
        def run():
            if "insert" in self.cal.failing:
                raise RuntimeError("simulated Calendar API failure")
            _aware(body["start"]), _aware(body["end"])
            event = dict(body, id=f"ev{next(self.cal.ids)}")
            self.cal.store[event["id"]] = event
            return event
        return Request(run)

    def list(self, calendarId, maxResults=250, singleEvents=False, timeMin=None,
             timeMax=None, q=None, orderBy=None):
        def run():
            if orderBy == "startTime" and not singleEvents:
                raise ValueError("400: orderBy=startTime requires singleEvents=True")
            lo = _bound(timeMin) if timeMin else None
            hi = _bound(timeMax) if timeMax else None
            items = []
            for event in self.cal.store.values():
                start, end = _aware(event["start"]), _aware(event["end"])
                if lo and end <= lo:
                    continue
                if hi and start >= hi:
                    continue
                if q and q.lower() not in event.get("summary", "").lower():
                    continue
                items.append(event)
            items.sort(key=lambda e: _aware(e["start"]))
            return {"items": items[:maxResults]}
        return Request(run)

    def patch(self, calendarId, eventId, body):
        def run():
            if "patch" in self.cal.failing:
                raise RuntimeError("simulated Calendar API failure")
            event = dict(self.cal.store[eventId])
            event.update(body)
            if _aware(event["end"]) < _aware(event["start"]):
                raise ValueError("400: The specified time range is empty")
            self.cal.store[eventId] = event
            return event
        return Request(run)

    def delete(self, calendarId, eventId):
        def run():
            if "delete" in self.cal.failing:
                raise RuntimeError("simulated Calendar API failure")
            self.cal.store.pop(eventId)
        return Request(run)


class FakeCalendar:
    """In-memory stand-in for the Google Calendar v3 service object."""

    def __init__(self, tz=TZ, failing=()):
        self.tz = tz
        self.failing = set(failing)
        self.store = {}
        self.ids = itertools.count(1)
        self._events = FakeEvents(self)

    def events(self):
        return self._events

    def settings(self):
        return SimpleNamespace(get=lambda setting: Request(lambda: {"value": self.tz}))

    def calendars(self):
        return SimpleNamespace(get=lambda calendarId: Request(lambda: {"timeZone": self.tz}))

    def add(self, summary, start, end):
        event = {"id": f"ev{next(self.ids)}", "summary": summary,
                 "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        self.store[event["id"]] = event
        return event

    def local_start(self, event):
        return _aware(event["start"]).astimezone(ZoneInfo(self.tz))

    def local_end(self, event):
        return _aware(event["end"]).astimezone(ZoneInfo(self.tz))


class ScriptedLLM:
    """OpenAI-compatible client that returns a fixed reply and records the prompt."""

    def __init__(self, reply):
        self.reply = reply
        self.system_prompt = None
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **kwargs):
        self.calls.append(kwargs)
        self.system_prompt = next((m["content"] for m in messages if m["role"] == "system"), None)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


class FakeMessage:
    def __init__(self, text, replies):
        self.text = text
        self.replies = replies

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


async def _noop(**kwargs):
    pass


@pytest.fixture
def calendar(monkeypatch):
    cal = FakeCalendar()
    monkeypatch.setattr(bot, "authenticate_user", lambda user_id: cal)
    return cal


@pytest.fixture
def say():
    """Send one Telegram message through bot.handle_message; return the bot's replies."""
    import asyncio

    def _say(text, llm_reply):
        replies = []
        update = SimpleNamespace(
            message=FakeMessage(text, replies),
            effective_user=SimpleNamespace(id=4242, first_name="Demo"),
            effective_chat=SimpleNamespace(id=4242),
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=_noop))
        llm = ScriptedLLM(llm_reply)
        asyncio.run(bot.handle_message(update, context, client=llm))
        return replies, llm
    return _say


class FailingLLM:
    """OpenAI-compatible client whose every call raises, like a 402 from OpenRouter."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        raise RuntimeError("402 This request requires more credits")
