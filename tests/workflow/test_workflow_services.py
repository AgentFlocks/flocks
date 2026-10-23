"""Services a backend hands workflow python nodes by name (flocks.coordination.workflow_services).

Case list (编号 | 场景):
  WS1 without a backend's services a node has only its own globals
  WS2 an in-process node calls a service directly
  WS3 an isolated node reaches a service through the host, method by method,
      with the call's arguments and result carried over
  WS4 only the methods a service exposes can be called: from the node (no
      proxy for others, none private) and at the host (a forged request)
  WS5 a service cannot take one of a node's own names
  WS6 the service at the host follows the node's cancel state
  WS7 one execution of a node talks to one instance of each service, so what
      a service keeps on itself lasts from one call to the next; the next
      execution gets instances of its own
"""

from __future__ import annotations

import pytest

from flocks import coordination
from flocks.workflow import NodeExecutionError
from flocks.workflow.repl_runtime import HostProcessPythonExecRuntime, PythonExecRuntime, _workflow_services


class Ledger:
    """A backend's service: records what it was asked."""

    exposed_methods = ("record", "total")
    rpc_timeout_s = 5.0

    def __init__(self, cancel_checker=None, calls=None):
        self.cancel_checker = cancel_checker
        self.calls = calls if calls is not None else []

    def record(self, amount, *, note=""):
        self.calls.append((amount, note))
        return {"recorded": amount, "note": note}

    def total(self):
        return sum(amount for amount, _ in self.calls)

    def drop_everything(self):  # not exposed
        self.calls.clear()
        return "dropped"

    def _private(self):
        return "private"


@pytest.fixture
def ledger(monkeypatch):
    calls: list = []
    seen_checkers: list = []

    def services(cancel_checker):
        seen_checkers.append(cancel_checker)
        return {"ledger": Ledger(cancel_checker, calls), "tool": Ledger(cancel_checker, calls)}

    monkeypatch.setattr(coordination, "workflow_services", services)
    return calls, seen_checkers


def test_ws1_without_services_a_node_has_its_own_globals_only():
    outputs, _ = PythonExecRuntime().execute("outputs['has'] = 'ledger' in globals()", {})
    assert outputs == {"has": False}


def test_ws2_an_in_process_node_calls_a_service_directly(ledger):
    calls, _ = ledger
    outputs, _ = PythonExecRuntime().execute(
        "outputs['r'] = ledger.record(3, note='a'); outputs['t'] = ledger.total()", {},
    )
    assert outputs == {"r": {"recorded": 3, "note": "a"}, "t": 3}
    assert calls == [(3, "a")]


def test_ws3_an_isolated_node_reaches_a_service_through_the_host(ledger):
    calls, _ = ledger
    outputs, _ = HostProcessPythonExecRuntime().execute(
        "outputs['r'] = ledger.record(5, note='b'); outputs['t'] = ledger.total()", {},
    )
    assert outputs == {"r": {"recorded": 5, "note": "b"}, "t": 5}
    assert calls == [(5, "b")]


def test_ws4_only_exposed_methods_can_be_called(ledger):
    calls, _ = ledger
    calls.append((7, "kept"))
    for code in ("ledger.drop_everything()", "ledger._private()"):
        with pytest.raises(NodeExecutionError, match="not available"):
            HostProcessPythonExecRuntime().execute(code, {})
    assert calls == [(7, "kept")]

    runtime = HostProcessPythonExecRuntime()
    services = _workflow_services(None)  # what one execution's RPCs are served from
    forged = runtime._handle_rpc_request(
        msg={"type": "rpc", "token": "t", "id": "1",
             "rpc": {"kind": "service", "service": "ledger", "method": "drop_everything", "args": [], "kwargs": {}}},
        token="t", services=services,
    )
    assert forged["ok"] is False and "not available" in forged["error"]
    assert calls == [(7, "kept")]
    unknown = runtime._handle_rpc_request(
        msg={"type": "rpc", "token": "t", "id": "2",
             "rpc": {"kind": "service", "service": "nobody", "method": "record", "args": [1], "kwargs": {}}},
        token="t", services=services,
    )
    assert unknown["ok"] is False
    allowed = runtime._handle_rpc_request(
        msg={"type": "rpc", "token": "t", "id": "3",
             "rpc": {"kind": "service", "service": "ledger", "method": "record", "args": [1], "kwargs": {}}},
        token="t", services=services,
    )
    assert allowed["ok"] is True and calls == [(7, "kept"), (1, "")]


def test_ws5_a_service_cannot_take_a_nodes_own_name(ledger):
    outputs, _ = PythonExecRuntime().execute("outputs['tool'] = type(tool).__name__", {})
    assert outputs["tool"] != "Ledger"
    outputs, _ = HostProcessPythonExecRuntime().execute("outputs['tool'] = type(tool).__name__", {})
    assert outputs["tool"] == "_ToolProxy"


def test_ws6_the_host_hands_the_service_the_nodes_cancel_state(monkeypatch):
    asked: list = []

    class Probe:
        exposed_methods = ("cancelled_now",)

        def __init__(self, cancel_checker):
            self.cancel_checker = cancel_checker

        def cancelled_now(self):
            asked.append(bool(self.cancel_checker and self.cancel_checker()))
            return asked[-1]

    monkeypatch.setattr(coordination, "workflow_services", lambda cancel_checker: {"probe": Probe(cancel_checker)})
    state = {"cancelled": False}

    outputs, _ = HostProcessPythonExecRuntime(cancel_checker=lambda: state["cancelled"]).execute(
        "outputs['seen'] = probe.cancelled_now()", {},
    )

    assert outputs == {"seen": False} and asked == [False]


class Counter:
    """A backend's service that keeps its count on itself."""

    exposed_methods = ("add", "count")

    def __init__(self):
        self._count = 0

    def add(self, amount):
        self._count += amount

    def count(self):
        return self._count


@pytest.mark.parametrize("runtime_class", [PythonExecRuntime, HostProcessPythonExecRuntime])
def test_ws7_one_execution_talks_to_one_instance_of_each_service(monkeypatch, runtime_class):
    built: list = []

    def services(cancel_checker):
        built.append(Counter())
        return {"counter": built[-1]}

    monkeypatch.setattr(coordination, "workflow_services", services)
    code = "counter.add(5); counter.add(2); outputs['count'] = counter.count()"
    runtime = runtime_class()

    assert runtime.execute(code, {})[0] == {"count": 7}
    assert runtime.execute(code, {})[0] == {"count": 7}
    assert len(built) == 2 and [counter.count() for counter in built] == [7, 7]
