import os
import sys
import types
from unittest.mock import Mock

import pytest

os.environ.setdefault("MISTRAL_API_KEY", "test-key")

if "mistralai" not in sys.modules:
    fake = types.ModuleType("mistralai")
    fake.Mistral = lambda *a, **k: None
    sys.modules["mistralai"] = fake

import brain


def test_model_roles_are_configurable_defaults(monkeypatch):
    calls = []

    class Chat:
        def complete(self, **kwargs):
            calls.append(kwargs)
            content = "1. plan" if not kwargs.get("response_format") else '{"response_type":"terminate"}'
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])

    class Client:
        def __init__(self, api_key):
            self.chat = Chat()

    monkeypatch.setattr(brain, "Mistral", Client)
    monkeypatch.setattr(brain.time, "sleep", lambda *_: None)
    client = brain.LLMClient()
    assert client.initial_plan("open youtube") == "1. plan"
    assert client.ask_worker("prompt")["response_type"] == "terminate"
    assert calls[0]["model"] == "mistral-large-latest"
    assert calls[1]["model"] == "mistral-small-latest"


def make_jarvis(monkeypatch, supervisor_responses=None):
    j = object.__new__(brain.Jarvis)
    j.i3 = Mock()
    j.i3.get_focused.return_value = {"name": "Firefox", "class": "firefox"}
    j.tools = Mock()
    j.tools.run.return_value = {"tool": "wm_events", "data": "Firefox"}
    j.executor = Mock()
    j.executor.execute.return_value = True
    j.llm = Mock()
    seq = iter(supervisor_responses or [{"response_type":"review","status":"ok","summary":"fine","directive":"continue"}])
    j.llm.ask_supervisor.side_effect = lambda prompt: next(seq)
    j.supervisor_tool_limit = 5
    j.review_interval = 10
    j.repetition_threshold = 3
    j.settle_timeout = 0
    j.settle_poll = 0
    return j


def test_three_repeated_action_cycles_trigger_supervisor(monkeypatch):
    j = make_jarvis(monkeypatch)
    state = brain.AgentState("go to youtube")
    actions = [{"type":"key_combo","keys":"ctrl+l"},{"type":"type_text","text":"youtube.com"},{"type":"key_combo","keys":"enter"}]
    for _ in range(3):
        j._record_cycle(state, actions, "retry", "page", True, {"name":"Firefox", "class":"firefox"}, None)
    j._maybe_review(state)
    assert j.llm.ask_supervisor.call_count == 1
    assert state.latest_supervisor_directive == "continue"


def test_periodic_review_uses_last_ten_cycles(monkeypatch):
    j = make_jarvis(monkeypatch)
    captured = []
    j.llm.ask_supervisor.side_effect = lambda prompt: captured.append(prompt) or {"response_type":"review","status":"ok","summary":"ok","directive":"keep going"}
    state = brain.AgentState("task")
    for i in range(12):
        actions = [{"type":"key_combo","keys":f"ctrl+{i}"}]
        j._record_cycle(state, actions, str(i), "", True, None, None)
    state.cycle_count = 20
    j._maybe_review(state)
    assert j.llm.ask_supervisor.call_count == 1
    assert '"cycle": 3' in captured[0]
    assert '"cycle": 12' in captured[0]
    assert '"cycle": 2' not in captured[0]


def test_supervisor_tool_usage_is_capped(monkeypatch):
    tool_requests = [{"response_type":"tool_request","tool_name":"wm_events","parameters":{"count":1},"reasoning":"observe"} for _ in range(10)]
    j = make_jarvis(monkeypatch, tool_requests)
    state = brain.AgentState("task")
    j._record_cycle(state, [{"type":"key_combo","keys":"ctrl+l"}], "", "", True, None, None)
    j._run_supervisor_review(state, "test cap")
    assert j.tools.run.call_count == 5
    assert "Supervisor could not complete" in state.latest_supervisor_directive


def test_navigation_guard_blocks_immediate_duplicate(monkeypatch):
    actions = [{"type":"key_combo","keys":"ctrl+l"},{"type":"type_text","text":"https://youtube.com"},{"type":"key_combo","keys":"enter"}]
    fp = brain.normalize_actions(actions)
    state = brain.AgentState("open youtube")
    state.navigation.fingerprint = fp
    state.navigation.completed_at = brain.time.time()
    j = make_jarvis(monkeypatch)
    j.settle_timeout = 999
    assert brain.is_navigation_batch(actions) == (True, "https://youtube.com")
    assert state.navigation.fingerprint == fp


def test_state_change_resets_repetition_detection(monkeypatch):
    j = make_jarvis(monkeypatch)
    state = brain.AgentState("task")
    actions = [{"type":"key_combo","keys":"ctrl+l"}]
    j.i3.get_focused.side_effect = [{"name":"B"}, {"name":"C"}]
    j._record_cycle(state, actions, "", "", True, {"name":"A"}, None)
    j._record_cycle(state, actions, "", "", True, {"name":"B"}, None)
    assert state.repeated_action_count == 1

def test_repeated_short_action_pattern_triggers_supervisor(monkeypatch):
    j = make_jarvis(monkeypatch)
    state = brain.AgentState("task")
    j.i3.get_focused.return_value = {"name":"same", "class":"app"}
    a = [{"type":"key_combo","keys":"tab"}]
    b = [{"type":"key_combo","keys":"enter"}]
    for actions in (a, b, a, b):
        j._record_cycle(state, actions, "", "", True, {"name":"same", "class":"app"}, None)
    j._maybe_review(state)
    assert j.llm.ask_supervisor.call_count == 1
