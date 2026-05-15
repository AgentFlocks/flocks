/**
 * API Capture Hook - Base Version (ES5 compatible)
 */
(function() {
  if (window.__apiCapture) {
    console.log('[API Capture] Already installed');
    return;
  }

  window.__capturedRequests = [];

  var CONFIG = {
    maxResponseLength: 2000,
    maxRequestBodyLength: 2000,
    maxRecentActions: 20,
    captureMode: 'smart', // 'smart' | 'all'
    sameOriginOnly: true,
    includePatterns: [],
    ignorePatterns: [
      /google-analytics/, /googletagmanager/, /hotjar/, /segment/,
      /mixpanel/, /amplitude/, /sentry/, /newrelic/, /datadog/,
      /\/sockjs\//, /\/ws\//, /\/websocket\//,
      /\.(png|jpg|jpeg|gif|webp|css|js|map|woff2?|ttf|svg|ico|mp4|mp3)$/i,
      /\/static\//, /\/assets\//, /\/images?\//, /\/fonts?\//
    ]
  };

  var recentActions = [];
  var navigationState = {
    lastNavigation: null,
    currentUrl: window.location.href
  };

  function truncateText(text, limit) {
    var value = text == null ? '' : String(text);
    if (value.length <= limit) {
      return value;
    }
    return value.substring(0, limit) + '...[truncated]';
  }

  function safeTrim(text) {
    return String(text || '').replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '');
  }

  function cloneSimple(value) {
    if (value == null || typeof value !== 'object') {
      return value;
    }
    return JSON.parse(JSON.stringify(value));
  }

  function normalizeHeaders(headers) {
    var result = {};
    var key;
    if (!headers) {
      return result;
    }
    if (typeof Headers !== 'undefined' && headers instanceof Headers && headers.forEach) {
      headers.forEach(function(value, name) {
        result[name] = value;
      });
      return result;
    }
    if (Array.isArray(headers)) {
      headers.forEach(function(entry) {
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
      return '';
    }
    return headers[name] || headers[name.toLowerCase()] || '';
  }

  function hasStaticExtension(pathname) {
    return /\.[a-z0-9]{1,8}$/i.test(pathname || '');
  }

  function normalizeUrl(url) {
    var parsed;
    var query = {};
    var queryKeys = [];
    try {
      parsed = new URL(url, window.location.href);
      parsed.searchParams.forEach(function(value, key) {
        if (!Object.prototype.hasOwnProperty.call(query, key)) {
          queryKeys.push(key);
        }
        query[key] = value;
      });
      return {
        normalizedUrl: parsed.href,
        origin: parsed.origin,
        pathname: parsed.pathname,
        query: query,
        queryKeys: queryKeys
      };
    } catch (error) {
      return {
        normalizedUrl: String(url || ''),
        origin: '',
        pathname: '',
        query: query,
        queryKeys: queryKeys
      };
    }
  }

  function inferShape(value, path, out, depth) {
    var currentPath = path || '$';
    var nextDepth = depth || 0;
    var keys;
    var i;

    if (nextDepth > 4) {
      out[currentPath] = 'depthLimit';
      return;
    }
    if (value === null) {
      out[currentPath] = 'null';
      return;
    }
    if (typeof value === 'undefined') {
      out[currentPath] = 'undefined';
      return;
    }
    if (Array.isArray(value)) {
      out[currentPath] = 'array(' + value.length + ')';
      if (value.length > 0) {
        inferShape(value[0], currentPath + '[]', out, nextDepth + 1);
      }
      return;
    }
    if (typeof value === 'object') {
      out[currentPath] = 'object';
      keys = Object.keys(value);
      for (i = 0; i < keys.length && i < 20; i++) {
        inferShape(value[keys[i]], currentPath + '.' + keys[i], out, nextDepth + 1);
      }
      return;
    }
    out[currentPath] = typeof value;
  }

  function detectGraphQL(payload) {
    var text;
    var parsed;
    var operationType = '';
    if (!payload) {
      return null;
    }
    text = typeof payload === 'string' ? payload : '';
    try {
      parsed = typeof payload === 'string' ? JSON.parse(payload) : payload;
    } catch (error) {
      parsed = null;
    }
    if (!parsed || typeof parsed !== 'object') {
      return null;
    }
    if (!parsed.query) {
      return null;
    }
    if (/mutation\s/i.test(parsed.query)) {
      operationType = 'mutation';
    } else if (/query\s/i.test(parsed.query)) {
      operationType = 'query';
    } else {
      operationType = 'graphql';
    }
    return {
      operationName: parsed.operationName || '',
      operationType: operationType,
      variablesShape: parsed.variables && typeof parsed.variables === 'object'
        ? (function() {
            var shape = {};
            inferShape(parsed.variables, '$', shape, 0);
            return shape;
          })()
        : {}
    };
  }

  function summarizeBody(body) {
    var result = {
      kind: 'empty',
      display: '',
      parsed: null,
      shape: {},
      graphql: null
    };
    var asObject = {};

    if (body == null || body === '') {
      return result;
    }

    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) {
      body.forEach(function(value, key) {
        asObject[key] = value;
      });
      result.kind = 'urlencoded';
      result.parsed = asObject;
      result.display = truncateText(JSON.stringify(asObject, null, 2), CONFIG.maxRequestBodyLength);
      inferShape(asObject, '$', result.shape, 0);
      return result;
    }

    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      result.kind = 'formData';
      if (typeof body.forEach === 'function') {
        body.forEach(function(value, key) {
          asObject[key] = Object.prototype.toString.call(value) === '[object File]' ? '[file]' : String(value);
        });
      }
      result.parsed = asObject;
      result.display = truncateText(JSON.stringify(asObject, null, 2), CONFIG.maxRequestBodyLength);
      inferShape(asObject, '$', result.shape, 0);
      return result;
    }

    if (typeof body === 'string') {
      result.display = truncateText(body, CONFIG.maxRequestBodyLength);
      try {
        result.parsed = JSON.parse(body);
        result.kind = 'json';
        result.display = truncateText(JSON.stringify(result.parsed, null, 2), CONFIG.maxRequestBodyLength);
        inferShape(result.parsed, '$', result.shape, 0);
      } catch (error) {
        result.kind = 'text';
        result.graphql = detectGraphQL(body);
      }
      if (!result.graphql && result.parsed) {
        result.graphql = detectGraphQL(result.parsed);
      }
      return result;
    }

    if (typeof body === 'object') {
      result.kind = 'object';
      result.parsed = body;
      result.display = truncateText(JSON.stringify(body, null, 2), CONFIG.maxRequestBodyLength);
      inferShape(body, '$', result.shape, 0);
      result.graphql = detectGraphQL(body);
      return result;
    }

    result.kind = typeof body;
    result.display = truncateText(String(body), CONFIG.maxRequestBodyLength);
    return result;
  }

  function summarizeResponse(text) {
    var result = {
      display: '',
      parsed: null,
      shape: {}
    };
    if (!text) {
      return result;
    }
    try {
      result.parsed = JSON.parse(text);
      result.display = truncateText(JSON.stringify(result.parsed, null, 2), CONFIG.maxResponseLength);
      inferShape(result.parsed, '$', result.shape, 0);
      return result;
    } catch (error) {
      result.display = truncateText(text, CONFIG.maxResponseLength);
      return result;
    }
  }

  function getPageContext() {
    return {
      url: window.location.href,
      path: window.location.pathname,
      title: document.title,
      referrer: document.referrer
    };
  }

  function describeElement(target) {
    var tag = target && target.tagName ? String(target.tagName).toUpperCase() : 'UNKNOWN';
    var text = safeTrim(target && target.textContent ? target.textContent : '');
    var label = text || safeTrim(target && target.value ? target.value : '');
    if (!label && target && typeof target.getAttribute === 'function') {
      label = safeTrim(
        target.getAttribute('aria-label') ||
        target.getAttribute('title') ||
        target.getAttribute('name') ||
        target.getAttribute('placeholder') ||
        ''
      );
    }
    if (!label) {
      label = (target && target.id) || (target && target.className) || tag;
    }
    return {
      action: label,
      tagName: tag,
      id: target && target.id ? String(target.id) : '',
      className: target && target.className ? String(target.className) : ''
    };
  }

  function pushRecentAction(action) {
    recentActions.push(action);
    if (recentActions.length > CONFIG.maxRecentActions) {
      recentActions.shift();
    }
  }

  function recordAction(type, detail) {
    pushRecentAction({
      type: type,
      detail: detail || {},
      action: detail && detail.action ? detail.action : '',
      url: window.location.href,
      timestamp: new Date().toISOString()
    });
  }

  function snapshotActionContext() {
    return {
      lastAction: recentActions.length ? cloneSimple(recentActions[recentActions.length - 1]) : null,
      recentActions: cloneSimple(recentActions),
      navigation: cloneSimple(navigationState)
    };
  }

  function installActionListeners() {
    if (document && document.addEventListener) {
      document.addEventListener('click', function(event) {
        recordAction('click', describeElement(event && event.target));
      }, true);
      document.addEventListener('input', function(event) {
        recordAction('input', describeElement(event && event.target));
      }, true);
      document.addEventListener('change', function(event) {
        recordAction('change', describeElement(event && event.target));
      }, true);
      document.addEventListener('submit', function(event) {
        recordAction('submit', describeElement(event && event.target));
      }, true);
      document.addEventListener('keydown', function(event) {
        recordAction('keydown', {
          action: event && event.key ? String(event.key) : 'keydown'
        });
      }, true);
    }

    if (window && window.addEventListener) {
      window.addEventListener('popstate', function() {
        navigationState.lastNavigation = {
          type: 'popstate',
          url: window.location.href,
          timestamp: new Date().toISOString()
        };
        navigationState.currentUrl = window.location.href;
        recordAction('popstate', { action: window.location.href });
      });
    }

    if (window.history && window.history.pushState) {
      var originalPushState = window.history.pushState;
      window.history.pushState = function() {
        var result = originalPushState.apply(this, arguments);
        navigationState.lastNavigation = {
          type: 'pushState',
          url: arguments.length >= 3 ? String(arguments[2]) : window.location.href,
          timestamp: new Date().toISOString()
        };
        navigationState.currentUrl = window.location.href;
        recordAction('pushState', { action: navigationState.lastNavigation.url });
        return result;
      };
    }

    if (window.history && window.history.replaceState) {
      var originalReplaceState = window.history.replaceState;
      window.history.replaceState = function() {
        var result = originalReplaceState.apply(this, arguments);
        navigationState.lastNavigation = {
          type: 'replaceState',
          url: arguments.length >= 3 ? String(arguments[2]) : window.location.href,
          timestamp: new Date().toISOString()
        };
        navigationState.currentUrl = window.location.href;
        recordAction('replaceState', { action: navigationState.lastNavigation.url });
        return result;
      };
    }
  }

  function getCaptureDecision(url, method, headers) {
    var m = (method || 'GET').toUpperCase();
    var normalizedHeaders = normalizeHeaders(headers);
    var accept = getHeader(normalizedHeaders, 'accept');
    var contentType = getHeader(normalizedHeaders, 'content-type');
    var looksJson = /application\/json|text\/plain|application\/x-www-form-urlencoded/i
      .test(accept + ' ' + contentType);
    var urlInfo = normalizeUrl(url);
    var i;

    if (CONFIG.sameOriginOnly && urlInfo.origin && urlInfo.origin !== window.location.origin) {
      return { capture: false, reason: 'crossOrigin', urlInfo: urlInfo };
    }

    for (i = 0; i < CONFIG.ignorePatterns.length; i++) {
      if (CONFIG.ignorePatterns[i].test(urlInfo.normalizedUrl)) {
        return { capture: false, reason: 'ignorePattern', urlInfo: urlInfo };
      }
    }

    for (i = 0; i < CONFIG.includePatterns.length; i++) {
      if (CONFIG.includePatterns[i].test(urlInfo.normalizedUrl)) {
        return { capture: true, reason: 'includePattern', urlInfo: urlInfo };
      }
    }

    if (CONFIG.captureMode === 'all') {
      return { capture: true, reason: 'captureModeAll', urlInfo: urlInfo };
    }

    if (m !== 'GET') {
      return { capture: true, reason: 'nonGet', urlInfo: urlInfo };
    }

    if (!hasStaticExtension(urlInfo.pathname || '/')) {
      return { capture: true, reason: 'nonStaticPath', urlInfo: urlInfo };
    }

    if (looksJson) {
      return { capture: true, reason: 'jsonLike', urlInfo: urlInfo };
    }

    return { capture: false, reason: 'filteredOut', urlInfo: urlInfo };
  }

  function buildCaptureRecord(base) {
    var requestBody = summarizeBody(base.requestBody);
    var responseBody = summarizeResponse(base.responseText);
    var requestContentType = getHeader(base.requestHeaders, 'content-type');
    var responseContentType = base.responseContentType || '';
    var actionContext = snapshotActionContext();
    return {
      captureSource: 'pageHook',
      type: base.type,
      method: base.method,
      url: base.url,
      normalizedUrl: base.urlInfo.normalizedUrl,
      origin: base.urlInfo.origin,
      pathname: base.urlInfo.pathname,
      query: base.urlInfo.query,
      queryKeys: base.urlInfo.queryKeys,
      status: base.status,
      requestHeaders: base.requestHeaders,
      requestBody: requestBody.display,
      requestBodyKind: requestBody.kind,
      requestShape: requestBody.shape,
      requestContentType: requestContentType,
      graphql: requestBody.graphql,
      response: responseBody.display,
      responseShape: responseBody.shape,
      responseContentType: responseContentType,
      pageContext: base.pageContext,
      actionContext: actionContext,
      captureReason: base.captureReason,
      duration: base.duration,
      timestamp: new Date().toISOString()
    };
  }

  installActionListeners();

  var originalXHROpen = XMLHttpRequest.prototype.open;
  var originalXHRSend = XMLHttpRequest.prototype.send;
  var originalXHRSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function(method, url) {
    this._capture = {
      method: (method || 'GET').toUpperCase(),
      url: typeof url === 'string' ? url : String(url),
      startTime: Date.now(),
      headers: {},
      pageContext: getPageContext()
    };
    return originalXHROpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
    if (this._capture) {
      this._capture.headers[header] = value;
    }
    return originalXHRSetHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function(body) {
    var capture = this._capture;
    var decision = capture ? getCaptureDecision(capture.url, capture.method, capture.headers) : null;
    if (!capture || !decision || !decision.capture) {
      return originalXHRSend.apply(this, arguments);
    }

    capture.requestBody = body;

    this.addEventListener('load', function() {
      var record = buildCaptureRecord({
        type: 'XHR',
        method: capture.method,
        url: capture.url,
        urlInfo: decision.urlInfo,
        status: this.status,
        requestHeaders: normalizeHeaders(capture.headers),
        requestBody: capture.requestBody,
        responseText: this.responseText || '',
        responseContentType: typeof this.getResponseHeader === 'function'
          ? (this.getResponseHeader('Content-Type') || '')
          : '',
        pageContext: capture.pageContext,
        captureReason: decision.reason,
        duration: Date.now() - capture.startTime
      });

      window.__capturedRequests.push(record);
      console.log(
        '[API Capture] XHR:',
        capture.method,
        record.normalizedUrl,
        '->',
        this.status,
        'action=' + (record.actionContext.lastAction ? record.actionContext.lastAction.action : 'none')
      );
    });

    this.addEventListener('error', function() {
      var record = buildCaptureRecord({
        type: 'XHR',
        method: capture.method,
        url: capture.url,
        urlInfo: decision.urlInfo,
        status: 'error',
        requestHeaders: normalizeHeaders(capture.headers),
        requestBody: capture.requestBody,
        responseText: '',
        responseContentType: '',
        pageContext: capture.pageContext,
        captureReason: decision.reason,
        duration: Date.now() - capture.startTime
      });
      record.error = 'Network error';
      window.__capturedRequests.push(record);
    });

    return originalXHRSend.apply(this, arguments);
  };

  var originalFetch = window.fetch;
  window.fetch = function(url, options) {
    options = options || {};
    var startTime = Date.now();
    var method = (options.method || 'GET').toUpperCase();
    var requestHeaders = normalizeHeaders(options.headers || {});
    var urlStr = typeof url === 'string' ? url : (url && url.url ? url.url : String(url));
    var decision = getCaptureDecision(urlStr, method, requestHeaders);

    if (!decision.capture) {
      return originalFetch.apply(this, arguments);
    }

    return originalFetch.apply(this, arguments).then(function(response) {
      var cloned = response.clone();
      return cloned.text().then(function(text) {
        var record = buildCaptureRecord({
          type: 'Fetch',
          method: method,
          url: urlStr,
          urlInfo: decision.urlInfo,
          status: response.status,
          requestHeaders: requestHeaders,
          requestBody: options.body,
          responseText: text || '',
          responseContentType: response.headers && typeof response.headers.get === 'function'
            ? (response.headers.get('content-type') || '')
            : '',
          pageContext: getPageContext(),
          captureReason: decision.reason,
          duration: Date.now() - startTime
        });

        window.__capturedRequests.push(record);
        console.log(
          '[API Capture] Fetch:',
          method,
          record.normalizedUrl,
          '->',
          response.status,
          'action=' + (record.actionContext.lastAction ? record.actionContext.lastAction.action : 'none')
        );
        return response;
      });
    }).catch(function(error) {
      var record = buildCaptureRecord({
        type: 'Fetch',
        method: method,
        url: urlStr,
        urlInfo: decision.urlInfo,
        status: 'error',
        requestHeaders: requestHeaders,
        requestBody: options.body,
        responseText: '',
        responseContentType: '',
        pageContext: getPageContext(),
        captureReason: decision.reason,
        duration: Date.now() - startTime
      });
      record.error = error && error.message ? error.message : String(error);
      window.__capturedRequests.push(record);
      throw error;
    });
  };

  window.__apiCapture = {
    version: 'web2cli-base',
    installed: new Date().toISOString(),
    config: CONFIG,
    getAll: function() {
      return window.__capturedRequests;
    },
    clear: function() {
      window.__capturedRequests = [];
      recentActions = [];
      console.log('[API Capture] Cleared');
    },
    getRecentActions: function() {
      return cloneSimple(recentActions);
    },
    getDebugState: function() {
      return {
        version: this.version,
        installed: this.installed,
        config: cloneSimple(CONFIG),
        requestCount: window.__capturedRequests.length,
        recentActions: cloneSimple(recentActions),
        navigation: cloneSimple(navigationState),
        lastRequest: window.__capturedRequests.length
          ? cloneSimple(window.__capturedRequests[window.__capturedRequests.length - 1])
          : null
      };
    },
    summary: function() {
      var groups = {};
      window.__capturedRequests.forEach(function(record) {
        groups[record.pathname] = (groups[record.pathname] || 0) + 1;
      });
      console.log('=== API Capture Summary ===');
      console.log('Total requests:', window.__capturedRequests.length);
      console.log('Endpoints:', Object.keys(groups));
      console.log('Recent actions:', recentActions.length);
      console.log('window.__apiCapture.getDebugState() - inspect capture internals');
    }
  };

  console.log('[API Capture] web2cli-base installed');
  console.log('  window.__capturedRequests - captured data');
  console.log('  window.__apiCapture.getRecentActions() - recent user interactions');
  console.log('  window.__apiCapture.getDebugState() - current capture state');
})();