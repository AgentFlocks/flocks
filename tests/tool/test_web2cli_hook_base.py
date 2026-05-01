import json
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "skills"
    / "web2cli"
    / "scripts"
    / "inject-hook-base.js"
)


def _run_node_case(test_logic: str) -> dict:
    node_path = shutil.which("node")
    if node_path is None:
        pytest.skip("node is required for hook runtime tests")

    hook_source = json.dumps(SCRIPT_PATH.read_text(encoding="utf-8"))
    program = f"""
const vm = require("vm");
const hookSource = {hook_source};

function createEnvironment() {{
  const logs = [];
  const docListeners = {{}};
  const winListeners = {{}};

  function FakeDocument() {{
    this.title = "Dashboard";
    this.referrer = "https://example.com/login";
  }}

  FakeDocument.prototype.addEventListener = function(type, handler) {{
    (docListeners[type] = docListeners[type] || []).push(handler);
  }};

  FakeDocument.prototype.dispatch = function(type, event) {{
    (docListeners[type] || []).forEach(function(handler) {{
      handler(event);
    }});
  }};

  function FakeXHR() {{
    this.listeners = {{}};
    this.status = 200;
    this.responseText = JSON.stringify({{ ok: true }});
  }}

  FakeXHR.prototype.open = function(method, url) {{
    this._opened = {{ method: method, url: url }};
  }};

  FakeXHR.prototype.setRequestHeader = function(name, value) {{
    this._headers = this._headers || {{}};
    this._headers[name] = value;
  }};

  FakeXHR.prototype.addEventListener = function(type, handler) {{
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }};

  FakeXHR.prototype.send = function(body) {{
    this.requestBody = body;
    (this.listeners.load || []).forEach(function(handler) {{
      handler.call(this);
    }}, this);
  }};

  const document = new FakeDocument();
  const history = {{
    length: 1,
    pushState: function() {{}},
    replaceState: function() {{}}
  }};
  const location = new URL("https://example.com/dashboard");
  const window = {{
    location: location,
    history: history,
    addEventListener: function(type, handler) {{
      (winListeners[type] = winListeners[type] || []).push(handler);
    }},
    dispatchWindowEvent: function(type, event) {{
      (winListeners[type] || []).forEach(function(handler) {{
        handler(event);
      }});
    }}
  }};

  async function fetchImpl() {{
    return {{
      status: 201,
      clone: function() {{
        return {{
          text: async function() {{
            return JSON.stringify({{ items: [1, 2, 3] }});
          }}
        }};
      }}
    }};
  }}

  window.fetch = fetchImpl;

  const context = {{
    window: window,
    document: document,
    console: {{
      log: function() {{
        logs.push(Array.from(arguments).join(" "));
      }}
    }},
    history: history,
    location: location,
    XMLHttpRequest: FakeXHR,
    URL: URL,
    Headers: typeof Headers !== "undefined" ? Headers : function Headers() {{}},
    FormData: function FormData() {{}},
    URLSearchParams: URLSearchParams,
    Blob: function Blob() {{}},
    Date: Date,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout
  }};

  return {{
    context: context,
    logs: logs,
    document: document,
    window: window
  }};
}}

(async function() {{
  const env = createEnvironment();
  vm.createContext(env.context);
  vm.runInContext(hookSource, env.context);
  {test_logic}
}})().catch(function(error) {{
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}});
"""

    result = subprocess.run(
        [node_path, "-e", program],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_hook_base_captures_recent_action_context_for_xhr():
    result = _run_node_case(
        """
  const button = {
    tagName: "BUTTON",
    textContent: "Load data",
    className: "primary",
    id: "load-btn",
    parentNode: null,
    getAttribute: function(name) {
      return "";
    }
  };

  env.document.dispatch("click", { target: button });

  const xhr = new env.context.XMLHttpRequest();
  xhr.open("POST", "/api/items/list");
  xhr.setRequestHeader("Content-Type", "application/json");
  xhr.send(JSON.stringify({ page: 1 }));

  process.stdout.write(JSON.stringify({
    version: env.context.window.__apiCapture.version,
    request: env.context.window.__capturedRequests[0],
    recentActions: env.context.window.__apiCapture.getRecentActions(),
    logs: env.logs
  }));
"""
    )

    assert result["version"] == "3.1-base"
    assert result["request"]["pageContext"]["path"] == "/dashboard"
    assert result["request"]["actionContext"]["lastAction"]["action"] == "Load data"
    assert result["recentActions"][0]["type"] == "click"
    assert any("action=Load data" in line for line in result["logs"])


def test_hook_base_exposes_debug_state_and_truncates_large_responses():
    result = _run_node_case(
        """
  env.context.window.history.pushState({}, "", "/dashboard?tab=network");

  const xhr = new env.context.XMLHttpRequest();
  xhr.responseText = new Array(2601).join("x");
  xhr.open("GET", "/api/debug");
  xhr.send();

  env.context.window.__apiCapture.summary();

  process.stdout.write(JSON.stringify({
    response: env.context.window.__capturedRequests[0].response,
    debugState: env.context.window.__apiCapture.getDebugState(),
    logs: env.logs
  }));
"""
    )

    assert result["response"].endswith("...[truncated]")
    assert any(action["type"] == "pushState" for action in result["debugState"]["recentActions"])
    assert any("window.__apiCapture.getDebugState()" in line for line in result["logs"])
