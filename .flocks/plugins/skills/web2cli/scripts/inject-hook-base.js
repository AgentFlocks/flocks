/**
 * API Capture Hook - Base Version (ES5 compatible)
 *
 * Goals:
 * - Keep the default hook broadly compatible
 * - Capture XHR and fetch with page context
 * - Track recent user actions for request attribution
 * - Provide safer formatting and richer debug helpers
 */
(function () {
  if (window.__apiCapture) {
    console.log("[API Capture] Already installed");
    return;
  }

  window.__capturedRequests = [];
  window.__apiActionLog = [];

  var CONFIG = {
    version: "3.1-base",
    maxResponseLength: 50000,
    maxRequestBodyLength: 2000,
    maxStoredActions: 30,
    actionWindowMs: 15000,
    captureMode: "smart", // 'smart' | 'all'
    sameOriginOnly: true,
    includePatterns: [],
    ignorePatterns: [
      /google-analytics/,
      /googletagmanager/,
      /hotjar/,
      /segment/,
      /mixpanel/,
      /amplitude/,
      /sentry/,
      /newrelic/,
      /datadog/,
      /\/sockjs\//,
      /\/ws\//,
      /\/websocket\//,
      /\.(png|jpg|jpeg|gif|webp|css|js|map|woff2?|ttf|svg|ico|mp4|mp3)$/i,
      /\/static\//,
      /\/assets\//,
      /\/images?\//,
      /\/fonts?\//
    ]
  };

  function trimText(value, maxLength, suffix) {
    var text = value == null ? "" : String(value);
    var limit = maxLength || CONFIG.maxResponseLength;
    var tail = suffix || "...[truncated]";
    if (text.length <= limit) {
      return text;
    }
    return text.substring(0, limit) + tail;
  }

  function safeStringify(obj, maxLength) {
    var limit = maxLength || CONFIG.maxResponseLength;
    try {
      if (typeof obj === "string") {
        return trimText(obj, limit);
      }
      return trimText(JSON.stringify(obj, null, 2), limit, "\n...[truncated]");
    } catch (e) {
      return "[unserializable: " + e.message + "]";
    }
  }

  function normalizeHeaders(headers) {
    var result = {};
    var key;
    if (!headers) {
      return result;
    }
    if (typeof Headers !== "undefined" && headers instanceof Headers) {
      headers.forEach(function (value, name) {
        result[name] = value;
      });
      return result;
    }
    if (Array.isArray(headers)) {
      headers.forEach(function (entry) {
        if (entry && entry.length >= 2) {
          result[entry[0]] = entry[1];
        }
      });
      return result;
    }
    for (key in headers) {
      if (Object.prototype.hasOwnProperty.call(headers, key)) {
        result[key] = headers[key];
      }
    }
    return result;
  }

  function getHeader(headers, name) {
    if (!headers) {
      return "";
    }
    return headers[name] || headers[name.toLowerCase()] || "";
  }

  function hasStaticExtension(pathname) {
    return /\.[a-z0-9]{1,8}$/i.test(pathname || "");
  }

  function shouldCapture(url, method, headers) {
    var parsedUrl;
    var normalizedHeaders;
    var accept;
    var contentType;
    var looksStructured;
    var isIgnored;
    var isIncluded;

    try {
      parsedUrl = new URL(url, window.location.href);
    } catch (e) {
      return false;
    }

    if (CONFIG.sameOriginOnly && parsedUrl.origin !== window.location.origin) {
      return false;
    }

    isIgnored = CONFIG.ignorePatterns.some(function (pattern) {
      return pattern.test(parsedUrl.href);
    });
    if (isIgnored) {
      return false;
    }

    isIncluded = CONFIG.includePatterns.some(function (pattern) {
      return pattern.test(parsedUrl.href);
    });
    if (isIncluded) {
      return true;
    }

    if (CONFIG.captureMode === "all") {
      return true;
    }

    normalizedHeaders = normalizeHeaders(headers);
    accept = getHeader(normalizedHeaders, "accept");
    contentType = getHeader(normalizedHeaders, "content-type");
    looksStructured = /application\/json|text\/plain|application\/x-www-form-urlencoded/i.test(
      accept + " " + contentType
    );

    if ((method || "GET").toUpperCase() !== "GET") {
      return true;
    }

    if (!hasStaticExtension(parsedUrl.pathname || "/")) {
      return true;
    }

    return looksStructured;
  }

  function formatBody(body, maxLength) {
    if (body == null || body === "") {
      return "";
    }
    if (typeof body === "string") {
      try {
        return safeStringify(JSON.parse(body), maxLength);
      } catch (e) {
        return trimText(body, maxLength || CONFIG.maxRequestBodyLength);
      }
    }
    if (typeof FormData !== "undefined" && body instanceof FormData) {
      return "[form-data]";
    }
    if (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) {
      return trimText(body.toString(), maxLength || CONFIG.maxRequestBodyLength);
    }
    return safeStringify(body, maxLength || CONFIG.maxRequestBodyLength);
  }

  function formatResponseText(text) {
    if (text == null || text === "") {
      return "";
    }
    try {
      return safeStringify(JSON.parse(text), CONFIG.maxResponseLength);
    } catch (e) {
      return trimText(text, 2000);
    }
  }

  function getPageContext() {
    return {
      url: window.location.href,
      path: window.location.pathname,
      title: document.title,
      referrer: document.referrer,
      hash: window.location.hash || "",
      historyLength: window.history && typeof window.history.length === "number" ? window.history.length : 0
    };
  }

  function describeElement(element) {
    var className;
    var textContent;
    if (!element) {
      return {
        tag: "unknown",
        text: "",
        id: "",
        classes: ""
      };
    }

    className = typeof element.className === "string" ? element.className : "";
    textContent = trimText(
      (element.textContent || "").replace(/\s+/g, " ").trim(),
      80,
      "..."
    );

    return {
      tag: (element.tagName || "unknown").toLowerCase(),
      text: textContent,
      id: element.id || "",
      classes: trimText(className, 120, "..."),
      name: element.getAttribute ? element.getAttribute("name") || "" : "",
      type: element.getAttribute ? element.getAttribute("type") || "" : "",
      role: element.getAttribute ? element.getAttribute("role") || "" : "",
      href: element.getAttribute ? element.getAttribute("href") || "" : "",
      title: element.getAttribute ? element.getAttribute("title") || "" : "",
      ariaLabel: element.getAttribute ? element.getAttribute("aria-label") || "" : ""
    };
  }

  function createActionLabel(elementInfo, fallback) {
    return (
      elementInfo.text ||
      elementInfo.ariaLabel ||
      elementInfo.title ||
      elementInfo.name ||
      elementInfo.href ||
      elementInfo.tag ||
      fallback ||
      "unknown"
    );
  }

  function addAction(entry) {
    var action = entry || {};
    action.timestamp = Date.now();
    action.isoTime = new Date(action.timestamp).toISOString();
    action.page = window.location.pathname;
    action.pageUrl = window.location.href;

    window.__apiActionLog.push(action);
    if (window.__apiActionLog.length > CONFIG.maxStoredActions) {
      window.__apiActionLog = window.__apiActionLog.slice(-CONFIG.maxStoredActions);
    }
  }

  function getRecentActions() {
    var cutoff = Date.now() - CONFIG.actionWindowMs;
    return window.__apiActionLog.filter(function (action) {
      return action.timestamp >= cutoff;
    });
  }

  function getActionContext() {
    var recentActions = getRecentActions();
    return {
      lastAction: recentActions.length ? recentActions[recentActions.length - 1] : null,
      recentActions: recentActions.slice(Math.max(recentActions.length - 5, 0))
    };
  }

  function findClosestActionable(element) {
    var current = element;
    while (current) {
      if (!current.tagName) {
        current = current.parentNode;
        continue;
      }
      if (
        current.tagName === "A" ||
        current.tagName === "BUTTON" ||
        current.tagName === "FORM" ||
        (current.getAttribute &&
          (current.getAttribute("role") === "button" ||
            current.getAttribute("type") === "submit"))
      ) {
        return current;
      }
      current = current.parentNode;
    }
    return null;
  }

  function recordNavigation(type) {
    addAction({
      type: type,
      action: type,
      navigation: {
        url: window.location.href,
        path: window.location.pathname,
        hash: window.location.hash || ""
      }
    });
  }

  document.addEventListener(
    "click",
    function (event) {
      var target = findClosestActionable(event.target);
      var elementInfo;
      if (!target) {
        return;
      }

      elementInfo = describeElement(target);
      addAction({
        type: "click",
        action: createActionLabel(elementInfo, "click"),
        element: elementInfo
      });
    },
    true
  );

  document.addEventListener(
    "submit",
    function (event) {
      var elementInfo = describeElement(event.target);
      addAction({
        type: "submit",
        action: createActionLabel(elementInfo, "submit"),
        element: elementInfo
      });
    },
    true
  );

  if (window.history) {
    var originalPushState = window.history.pushState;
    var originalReplaceState = window.history.replaceState;

    if (typeof originalPushState === "function") {
      window.history.pushState = function () {
        var result = originalPushState.apply(this, arguments);
        recordNavigation("pushState");
        return result;
      };
    }

    if (typeof originalReplaceState === "function") {
      window.history.replaceState = function () {
        var result = originalReplaceState.apply(this, arguments);
        recordNavigation("replaceState");
        return result;
      };
    }
  }

  window.addEventListener("popstate", function () {
    recordNavigation("popstate");
  });

  function storeCapture(entry) {
    window.__capturedRequests.push(entry);
  }

  function logCapture(prefix, method, url, status, duration, actionContext) {
    var lastAction = actionContext && actionContext.lastAction ? actionContext.lastAction.action : "none";
    console.log(
      "[API Capture] " +
        prefix +
        ": " +
        method +
        " " +
        url +
        " -> " +
        status +
        " (" +
        duration +
        "ms, action=" +
        lastAction +
        ")"
    );
  }

  var originalXHROpen = XMLHttpRequest.prototype.open;
  var originalXHRSend = XMLHttpRequest.prototype.send;
  var originalXHRSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function (method, url) {
    this._capture = {
      method: (method || "GET").toUpperCase(),
      url: typeof url === "string" ? url : String(url),
      startTime: Date.now(),
      headers: {}
    };
    this._pageContext = getPageContext();
    return originalXHROpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function (header, value) {
    if (this._capture) {
      this._capture.headers[header] = value;
    }
    return originalXHRSetHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function (body) {
    var capture = this._capture;
    var pageContext = this._pageContext;
    var requestBodyDisplay;

    if (!capture || !shouldCapture(capture.url, capture.method, capture.headers)) {
      return originalXHRSend.apply(this, arguments);
    }

    requestBodyDisplay = formatBody(body, CONFIG.maxRequestBodyLength);

    this.addEventListener("load", function () {
      var actionContext = getActionContext();
      var entry = {
        type: "XHR",
        method: capture.method,
        url: capture.url,
        status: this.status,
        requestHeaders: capture.headers,
        requestBody: requestBodyDisplay,
        response: formatResponseText(this.responseText),
        pageContext: pageContext,
        actionContext: actionContext,
        duration: Date.now() - capture.startTime,
        timestamp: new Date().toISOString()
      };

      storeCapture(entry);
      logCapture("XHR", capture.method, capture.url, this.status, entry.duration, actionContext);
    });

    this.addEventListener("error", function () {
      var actionContext = getActionContext();
      var entry = {
        type: "XHR",
        method: capture.method,
        url: capture.url,
        status: "error",
        requestHeaders: capture.headers,
        requestBody: requestBodyDisplay,
        error: "Network error",
        pageContext: pageContext,
        actionContext: actionContext,
        duration: Date.now() - capture.startTime,
        timestamp: new Date().toISOString()
      };

      storeCapture(entry);
      logCapture("XHR", capture.method, capture.url, "error", entry.duration, actionContext);
    });

    return originalXHRSend.apply(this, arguments);
  };

  var originalFetch = window.fetch;
  window.fetch = function (url, options) {
    var fetchOptions = options || {};
    var startTime = Date.now();
    var method = (fetchOptions.method || "GET").toUpperCase();
    var urlStr = typeof url === "string" ? url : url.url || String(url);
    var requestHeaders = normalizeHeaders(fetchOptions.headers || {});
    var pageContext;
    var requestBodyDisplay;

    if (!shouldCapture(urlStr, method, requestHeaders)) {
      return originalFetch.apply(this, arguments);
    }

    pageContext = getPageContext();
    requestBodyDisplay = formatBody(fetchOptions.body, CONFIG.maxRequestBodyLength);

    return originalFetch.apply(this, arguments).then(function (response) {
      var cloned = response.clone();
      return cloned
        .text()
        .then(function (text) {
          var actionContext = getActionContext();
          var entry = {
            type: "Fetch",
            method: method,
            url: urlStr,
            status: response.status,
            requestHeaders: requestHeaders,
            requestBody: requestBodyDisplay,
            response: formatResponseText(text),
            pageContext: pageContext,
            actionContext: actionContext,
            duration: Date.now() - startTime,
            timestamp: new Date().toISOString()
          };

          storeCapture(entry);
          logCapture("Fetch", method, urlStr, response.status, entry.duration, actionContext);
          return response;
        })
        .catch(function () {
          var actionContext = getActionContext();
          var entry = {
            type: "Fetch",
            method: method,
            url: urlStr,
            status: response.status,
            requestHeaders: requestHeaders,
            requestBody: requestBodyDisplay,
            response: "[unreadable]",
            pageContext: pageContext,
            actionContext: actionContext,
            duration: Date.now() - startTime,
            timestamp: new Date().toISOString()
          };

          storeCapture(entry);
          logCapture("Fetch", method, urlStr, response.status, entry.duration, actionContext);
          return response;
        });
    }).catch(function (error) {
      var actionContext = getActionContext();
      var entry = {
        type: "Fetch",
        method: method,
        url: urlStr,
        status: "error",
        requestHeaders: requestHeaders,
        requestBody: requestBodyDisplay,
        error: error && error.message ? error.message : String(error),
        pageContext: pageContext,
        actionContext: actionContext,
        duration: Date.now() - startTime,
        timestamp: new Date().toISOString()
      };

      storeCapture(entry);
      logCapture("Fetch", method, urlStr, "error", entry.duration, actionContext);
      throw error;
    });
  };

  function summarizeEndpoints() {
    var groups = {};
    window.__capturedRequests.forEach(function (request) {
      var path = (request.url || "").split("?")[0];
      groups[path] = (groups[path] || 0) + 1;
    });
    return groups;
  }

  function getDebugState() {
    return {
      version: CONFIG.version,
      config: CONFIG,
      totalRequests: window.__capturedRequests.length,
      recentActions: getRecentActions(),
      endpoints: summarizeEndpoints(),
      pageContext: getPageContext()
    };
  }

  window.__apiCapture = {
    version: CONFIG.version,
    installed: new Date().toISOString(),
    config: CONFIG,
    getAll: function () {
      return window.__capturedRequests;
    },
    getRecentActions: function () {
      return getRecentActions();
    },
    getDebugState: function () {
      return getDebugState();
    },
    clear: function () {
      window.__capturedRequests = [];
      window.__apiActionLog = [];
      console.log("[API Capture] Cleared requests and actions");
    },
    debug: function () {
      console.log("[API Capture] Debug state");
      console.log(safeStringify(getDebugState(), 4000));
    },
    summary: function () {
      var groups = summarizeEndpoints();
      var paths = Object.keys(groups).sort();
      console.log("=== API Capture Summary ===");
      console.log("Version:", CONFIG.version);
      console.log("Total requests:", window.__capturedRequests.length);
      console.log("Recent actions:", getRecentActions().length);
      console.log("");
      console.log("Endpoints:");
      paths.forEach(function (path) {
        console.log("  " + groups[path] + "x " + path);
      });
      if (!paths.length) {
        console.log("  (none)");
      }
      console.log("");
      console.log("Commands:");
      console.log("  window.__apiCapture.getAll()");
      console.log("  window.__apiCapture.getRecentActions()");
      console.log("  window.__apiCapture.getDebugState()");
      console.log("  window.__apiCapture.debug()");
      console.log("  window.__apiCapture.clear()");
    }
  };

  console.log("[API Capture] " + CONFIG.version + " installed");
  console.log("  - Captures XHR/fetch with page context");
  console.log("  - Tracks recent user actions and navigation");
  console.log("  - Run window.__apiCapture.summary() for an overview");
})();
