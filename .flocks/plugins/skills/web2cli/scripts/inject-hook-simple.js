/**
 * API Capture Hook - Simple Version (ES5 compatible)
 */
(function(){
  if (window.__apiCapture) {
    console.log('[API Capture] Already installed');
    return;
  }

  window.__capturedRequests = [];

  // Configuration
  var CONFIG = {
    maxResponseLength: 50000,
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

  function normalizeHeaders(headers) {
    var result = {};
    var key;
    if (!headers) {
      return result;
    }
    if (typeof Headers !== 'undefined' && headers instanceof Headers) {
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
    if (!headers) return '';
    return headers[name] || headers[name.toLowerCase()] || '';
  }

  function hasStaticExtension(pathname) {
    return /\.[a-z0-9]{1,8}$/i.test(pathname || '');
  }

  function shouldCapture(url, method, headers) {
    var u;
    var m = (method || 'GET').toUpperCase();
    var normalizedHeaders = normalizeHeaders(headers);
    var accept = '';
    var contentType = '';
    var looksJson = false;
    var isIgnored = false;
    var isIncluded = false;

    try {
      u = new URL(url, window.location.href);
    } catch (e) {
      return false;
    }

    if (CONFIG.sameOriginOnly && u.origin !== window.location.origin) {
      return false;
    }

    isIgnored = CONFIG.ignorePatterns.some(function(p) { return p.test(u.href); });
    if (isIgnored) {
      return false;
    }

    isIncluded = CONFIG.includePatterns.some(function(p) { return p.test(u.href); });
    if (isIncluded) {
      return true;
    }

    if (CONFIG.captureMode === 'all') {
      return true;
    }

    accept = getHeader(normalizedHeaders, 'accept');
    contentType = getHeader(normalizedHeaders, 'content-type');
    looksJson = /application\/json|text\/plain|application\/x-www-form-urlencoded/i
      .test(accept + ' ' + contentType);

    if (m !== 'GET') {
      return true;
    }

    if (!hasStaticExtension(u.pathname || '/')) {
      return true;
    }

    return looksJson;
  }

  function getPageContext() {
    return {
      url: window.location.href,
      path: window.location.pathname,
      title: document.title,
      referrer: document.referrer
    };
  }

  // Hook XMLHttpRequest
  var originalXHROpen = XMLHttpRequest.prototype.open;
  var originalXHRSend = XMLHttpRequest.prototype.send;
  var originalXHRSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function(method, url) {
    this._capture = {
      method: method.toUpperCase(),
      url: typeof url === 'string' ? url : String(url),
      startTime: Date.now(),
      headers: {}
    };
    this._pageContext = getPageContext();
    return originalXHROpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
    if (this._capture) {
      this._capture.headers[header] = value;
    }
    return originalXHRSetHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function(body) {
    var self = this;
    if (!this._capture || !shouldCapture(this._capture.url, this._capture.method, this._capture.headers)) {
      return originalXHRSend.apply(this, arguments);
    }

    var capture = this._capture;
    var pageContext = this._pageContext;
    capture.requestBody = body;

    var requestBodyDisplay = '';
    if (body) {
      try {
        var parsed = JSON.parse(body);
        requestBodyDisplay = JSON.stringify(parsed, null, 2).substring(0, 2000);
      } catch (e) {
        requestBodyDisplay = String(body).substring(0, 1000);
      }
    }

    this.addEventListener('load', function() {
      var responseDisplay = '';
      try {
        var parsed = JSON.parse(this.responseText);
        responseDisplay = JSON.stringify(parsed, null, 2).substring(0, CONFIG.maxResponseLength);
      } catch (e) {
        responseDisplay = this.responseText.substring(0, 2000);
      }

      window.__capturedRequests.push({
        type: 'XHR',
        method: capture.method,
        url: capture.url,
        status: this.status,
        requestHeaders: capture.headers,
        requestBody: requestBodyDisplay,
        response: responseDisplay,
        pageContext: pageContext,
        duration: Date.now() - capture.startTime,
        timestamp: new Date().toISOString()
      });

      console.log('[API Capture] XHR:', capture.method, capture.url, '->', this.status);
    });

    this.addEventListener('error', function() {
      window.__capturedRequests.push({
        type: 'XHR',
        method: capture.method,
        url: capture.url,
        status: 'error',
        requestHeaders: capture.headers,
        requestBody: requestBodyDisplay,
        error: 'Network error',
        pageContext: pageContext,
        duration: Date.now() - capture.startTime,
        timestamp: new Date().toISOString()
      });
    });

    return originalXHRSend.apply(this, arguments);
  };

  // Hook Fetch
  var originalFetch = window.fetch;
  window.fetch = function(url, options) {
    options = options || {};
    var startTime = Date.now();
    var method = (options.method || 'GET').toUpperCase();
    var urlStr = typeof url === 'string' ? url : (url.url || String(url));

    var requestHeaders = normalizeHeaders(options.headers || {});

    if (!shouldCapture(urlStr, method, requestHeaders)) {
      return originalFetch.apply(this, arguments);
    }

    var pageContext = getPageContext();
    var requestBodyDisplay = '';
    if (options.body) {
      try {
        if (typeof options.body === 'string') {
          requestBodyDisplay = options.body.substring(0, 2000);
        } else {
          requestBodyDisplay = JSON.stringify(options.body).substring(0, 2000);
        }
      } catch (e) {
        requestBodyDisplay = '[body unreadable]';
      }
    }

    return originalFetch.apply(this, arguments).then(function(response) {
      var cloned = response.clone();

      return cloned.text().then(function(text) {
        var responseBody = '';
        try {
          var parsed = JSON.parse(text);
          responseBody = JSON.stringify(parsed, null, 2).substring(0, CONFIG.maxResponseLength);
        } catch (e) {
          responseBody = text.substring(0, 2000);
        }

        window.__capturedRequests.push({
          type: 'Fetch',
          method: method,
          url: urlStr,
          status: response.status,
          requestHeaders: requestHeaders,
          requestBody: requestBodyDisplay,
          response: responseBody,
          pageContext: pageContext,
          duration: Date.now() - startTime,
          timestamp: new Date().toISOString()
        });

        console.log('[API Capture] Fetch:', method, urlStr, '->', response.status);
        return response;
      });
    }).catch(function(error) {
      window.__capturedRequests.push({
        type: 'Fetch',
        method: method,
        url: urlStr,
        status: 'error',
        requestHeaders: requestHeaders,
        requestBody: requestBodyDisplay,
        error: error.message,
        pageContext: pageContext,
        duration: Date.now() - startTime,
        timestamp: new Date().toISOString()
      });
      throw error;
    });
  };

  window.__apiCapture = {
    version: '3.0-simple',
    installed: new Date().toISOString(),
    getAll: function() {
      return window.__capturedRequests;
    },
    clear: function() {
      window.__capturedRequests = [];
      console.log('[API Capture] Cleared');
    },
    summary: function() {
      console.log('=== API Capture Summary ===');
      console.log('Total requests:', window.__capturedRequests.length);
      var groups = {};
      window.__capturedRequests.forEach(function(r) {
        var path = r.url.split('?')[0];
        groups[path] = (groups[path] || 0) + 1;
      });
      console.log('Endpoints:', Object.keys(groups));
    }
  };

  console.log('[API Capture] v3.0-simple installed');
  console.log('  window.__capturedRequests - captured data');
  console.log('  window.__apiCapture.summary() - show summary');
})();
