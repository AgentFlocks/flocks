/**
 * API Capture Hook - Enhanced v3.0
 * 
 * Captures:
 * - Method, URL, Headers, Request Body, Response
 * - Page context (current URL, referrer)
 * - User action context (clicks, form submissions)
 * 
 * Storage: window.__capturedRequests
 * Usage: 
 *   - Get all: JSON.stringify(window.__capturedRequests)
 *   - Clear: window.__capturedRequests = []
 *   - Export docs: window.__apiCapture.exportDocs()
 */

(function(){
  // Prevent double-injection
  if (window.__apiCapture && window.__apiCapture.version === '3.0') {
    console.log('[API Capture] v3.0 already installed');
    return;
  }
  
  window.__capturedRequests = [];
  window.__apiActionMap = {};  // URL path -> user action mapping
  
  // Configuration
  const CONFIG = {
    maxResponseLength: 50000,
    captureMode: 'smart', // 'smart' | 'all'
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
  
  function normalizeHeaders(headers) {
    const result = {};
    if (!headers) {
      return result;
    }
    if (typeof Headers !== 'undefined' && headers instanceof Headers) {
      headers.forEach((value, name) => {
        result[name] = value;
      });
      return result;
    }
    if (Array.isArray(headers)) {
      headers.forEach((entry) => {
        if (entry && entry.length >= 2) {
          result[entry[0]] = entry[1];
        }
      });
      return result;
    }
    for (const [key, value] of Object.entries(headers)) {
      result[key] = value;
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
    let parsedUrl;
    try {
      parsedUrl = new URL(url, window.location.href);
    } catch (e) {
      return false;
    }
    
    if (CONFIG.sameOriginOnly && parsedUrl.origin !== window.location.origin) {
      return false;
    }
    
    const isIgnored = CONFIG.ignorePatterns.some(p => p.test(parsedUrl.href));
    if (isIgnored) {
      return false;
    }
    
    const isIncluded = CONFIG.includePatterns.some(p => p.test(parsedUrl.href));
    if (isIncluded) {
      return true;
    }
    
    if (CONFIG.captureMode === 'all') {
      return true;
    }
    
    const normalizedHeaders = normalizeHeaders(headers);
    const accept = getHeader(normalizedHeaders, 'accept');
    const contentType = getHeader(normalizedHeaders, 'content-type');
    const looksJson = /application\/json|text\/plain|application\/x-www-form-urlencoded/i
      .test(`${accept} ${contentType}`);
    
    if ((method || 'GET').toUpperCase() !== 'GET') {
      return true;
    }
    
    if (!hasStaticExtension(parsedUrl.pathname || '/')) {
      return true;
    }
    
    return looksJson;
  }
  
  function safeStringify(obj, maxLength) {
    maxLength = maxLength || CONFIG.maxResponseLength;
    try {
      if (typeof obj === 'string') {
        return obj.length > maxLength ? obj.substring(0, maxLength) + '...[truncated]' : obj;
      }
      const str = JSON.stringify(obj, null, 2);
      return str.length > maxLength ? str.substring(0, maxLength) + '\n...[truncated]' : str;
    } catch (e) {
      return '[unserializable: ' + e.message + ']';
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
  
  // ========== Capture User Actions ==========
  
  // Track clicks to build action -> API mapping
  document.addEventListener('click', function(e) {
    const target = e.target;
    const closest = target.closest('a, button, [role="button"], .ant-menu-item, .nav-item');
    
    if (closest) {
      const textContent = closest.textContent || '';
      const actionText = textContent.trim().substring(0, 50) || 
                         closest.getAttribute('aria-label') || 
                         closest.getAttribute('title') ||
                         closest.tagName;
      const href = closest.getAttribute('href') || '';
      
      // Store action context
      const path = window.location.pathname;
      if (!window.__apiActionMap[path]) {
        window.__apiActionMap[path] = [];
      }
      window.__apiActionMap[path].push({
        action: actionText,
        timestamp: Date.now(),
        type: 'click'
      });
    }
  }, true);
  
  // Track navigation (URL changes)
  let lastUrl = window.location.href;
  const originalPushState = history.pushState;
  history.pushState = function() {
    originalPushState.apply(this, arguments);
    lastUrl = window.location.href;
  };
  
  window.addEventListener('popstate', function() {
    lastUrl = window.location.href;
  });
  
  // ========== Hook XMLHttpRequest ==========
  
  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;
  const originalXHRSetHeader = XMLHttpRequest.prototype.setRequestHeader;
  
  XMLHttpRequest.prototype.open = function(method, url, ...args) {
    this._capture = { 
      method: method.toUpperCase(), 
      url: typeof url === 'string' ? url : String(url),
      startTime: Date.now(),
      headers: {}
    };
    this._pageContext = getPageContext();
    return originalXHROpen.call(this, method, url, ...args);
  };
  
  XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
    if (this._capture) {
      this._capture.headers[header] = value;
    }
    return originalXHRSetHeader.call(this, header, value);
  };
  
  XMLHttpRequest.prototype.send = function(body) {
    if (!this._capture || !shouldCapture(this._capture.url, this._capture.method, this._capture.headers)) {
      return originalXHRSend.call(this, body);
    }
    
    const capture = this._capture;
    const pageContext = this._pageContext;
    capture.requestBody = body;
    
    // Try to parse and format request body
    let requestBodyDisplay = '';
    if (body) {
      try {
        const parsed = JSON.parse(body);
        requestBodyDisplay = safeStringify(parsed, 2000);
      } catch (e) {
        requestBodyDisplay = String(body).substring(0, 1000);
      }
    }
    
    this.addEventListener('load', function() {
      let responseDisplay = '';
      try {
        const parsed = JSON.parse(this.responseText);
        responseDisplay = safeStringify(parsed);
      } catch (e) {
        responseDisplay = this.responseText.substring(0, 2000);
      }
      
      // Try to infer API purpose from URL
      const apiPurpose = inferApiPurpose(capture.url, pageContext);
      
      window.__capturedRequests.push({
        type: 'XHR',
        method: capture.method,
        url: capture.url,
        status: this.status,
        requestHeaders: capture.headers,
        requestBody: requestBodyDisplay,
        response: responseDisplay,
        pageContext: pageContext,
        apiPurpose: apiPurpose,
        duration: Date.now() - capture.startTime,
        timestamp: new Date().toISOString()
      });
      
      console.log('[API Capture] ✓', capture.method, capture.url, '->', this.status);
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
    
    return originalXHRSend.call(this, body);
  };
  
  // ========== Hook Fetch ==========
  
  const originalFetch = window.fetch;
  window.fetch = async function(url, options = {}) {
    const startTime = Date.now();
    const method = (options.method || 'GET').toUpperCase();
    const urlStr = typeof url === 'string' ? url : (url.url || String(url));
    
    const requestHeaders = normalizeHeaders(options.headers || {});
    if (!shouldCapture(urlStr, method, requestHeaders)) {
      return originalFetch.apply(this, arguments);
    }
    
    const pageContext = getPageContext();
    let requestBodyDisplay = '';
    if (options.body) {
      try {
        if (typeof options.body === 'string') {
          requestBodyDisplay = options.body.substring(0, 2000);
        } else {
          requestBodyDisplay = safeStringify(options.body, 2000);
        }
      } catch (e) {
        requestBodyDisplay = '[body unreadable]';
      }
    }
    
    try {
      const response = await originalFetch.apply(this, arguments);
      const cloned = response.clone();
      
      let responseBody = '';
      try {
        const text = await cloned.text();
        try {
          const parsed = JSON.parse(text);
          responseBody = safeStringify(parsed);
        } catch (e) {
          responseBody = text.substring(0, 2000);
        }
      } catch (e) {
        responseBody = '[unreadable]';
      }
      
      const apiPurpose = inferApiPurpose(urlStr, pageContext);
      
      window.__capturedRequests.push({
        type: 'Fetch',
        method: method,
        url: urlStr,
        status: response.status,
        requestHeaders: requestHeaders,
        requestBody: requestBodyDisplay,
        response: responseBody,
        pageContext: pageContext,
        apiPurpose: apiPurpose,
        duration: Date.now() - startTime,
        timestamp: new Date().toISOString()
      });
      
      console.log('[API Capture] ✓', method, urlStr, '->', response.status);
      return response;
      
    } catch (error) {
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
    }
  };
  
  // ========== Helper Functions ==========
  
  function inferApiPurpose(url, pageContext) {
    let path = '';
    try {
      path = new URL(url, window.location.href).pathname;
    } catch (e) {
      path = String(url).split('?')[0];
    }
    const endpoint = path.replace(/^\/+|\/+$/g, '');
    const lastSegment = endpoint.split('/').pop() || 'root';
    
    // Common API patterns
    const purposeMap = {
      'dashboard/qps': { name: 'QPS监控', desc: '获取实时QPS数据' },
      'dashboard/threaten_event': { name: '威胁事件', desc: '获取威胁事件统计' },
      'dashboard/security': { name: '安全态势', desc: '获取安全仪表盘数据' },
      'alarm-host/host-list': { name: '告警主机列表', desc: '获取告警主机列表(分页)' },
      'alarm-host/count': { name: '告警统计', desc: '获取告警数量统计' },
      'tag/list': { name: '标签列表', desc: '获取标签列表' },
      'machine/list': { name: '机器列表', desc: '获取机器/agent列表' },
      'device/getList': { name: '设备列表', desc: '获取设备列表' },
      'log/trend': { name: '日志趋势', desc: '获取日志趋势数据' },
      'log/searchBySql': { name: '日志搜索', desc: '按SQL条件搜索日志' },
      'host/exportFallHostSumList': { name: '导 出告警', desc: '导出告警主机汇总' },
      'threatMonitor': { name: '威胁监控', desc: '实时威胁监控数据' },
      'attack': { name: '外部攻击', desc: '外部攻击聚合数据' },
      'incidents/external': { name: '外部攻击事件', desc: '外部攻击事件列表' },
      'incidents/lateral': { name: '内网渗透', desc: '内网渗透事件' },
      'incidents/compromise': { name: '失陷破坏', desc: '失陷主机事件' },
      'asset/serviceList': { name: '资产列表', desc: '获取资产服务列表' },
      'investigation/logquery': { name: '日志调查', desc: '日志调查查询' },
      'block': { name: '处置列表', desc: '威胁处置记录' }
    };
    
    // Match by endpoint
    for (const [pattern, info] of Object.entries(purposeMap)) {
      if (endpoint.includes(pattern) || path.endsWith('/' + pattern) || path === '/' + pattern) {
        return { ...info, endpoint: path, page: pageContext.path };
      }
    }
    
    // Fallback: infer from page context
    return {
      name: lastSegment,
      desc: '页面: ' + (pageContext.path || 'unknown'),
      endpoint: path,
      page: pageContext.path
    };
  }
  
  // ========== Export Functions ==========
  
  function generateMarkdownDocs(requests) {
    const apiGroups = {};
    
    // Group by API endpoint
    requests.forEach(req => {
      const path = req.url.split('?')[0];
      if (!apiGroups[path]) {
        apiGroups[path] = [];
      }
      apiGroups[path].push(req);
    });
    
    let md = '# API 文档\n\n';
    md += '> Generated by API Capture Hook v3.0\n\n';
    md += `> Total APIs: ${Object.keys(apiGroups).length}\n\n`;
    md += '---\n\n';
    
    for (const [endpoint, apis] of Object.entries(apiGroups)) {
      const sample = apis[0];
      const purpose = sample.apiPurpose || {};
      
      md += `## ${purpose.name || endpoint}\n\n`;
      md += `**用途**: ${purpose.desc || '未知'}\n\n`;
      md += `**页面**: ${purpose.page || 'N/A'}\n\n`;
      md += '### 基本信息\n\n';
      md += `- **Method**: `${sample.method}`\n`;
      md += `- **Endpoint**: `${sample.url}`\n`;
      md += `- **Status**: ${sample.status}\n`;
      md += `- **Duration**: ${sample.duration}ms\n\n`;
      
      md += '### 请求头\n\n```http\n';
      const headers = sample.requestHeaders || {};
      Object.entries(headers).forEach(([k, v]) => {
        if (k.toLowerCase() !== 'cookie') {
          md += `${k}: ${v}\n`;
        }
      });
      if (headers['Cookie']) {
        md += `Cookie: [hidden]\n`;
      }
      md += '```\n\n';
      
      if (sample.requestBody) {
        md += '### 请求体\n\n```json\n';
        md += sample.requestBody + '\n';
        md += '```\n\n';
      }
      
      if (sample.response) {
        md += '### 响应示例\n\n```json\n';
        // Show first 1000 chars of response
        const respPreview = sample.response.substring(0, 2000);
        md += respPreview + '\n';
        if (sample.response.length > 2000) {
          md += '\n// ... (truncated)\n';
        }
        md += '```\n\n';
      }
      
      md += '---\n\n';
    }
    
    return md;
  }
  
  function generatePythonClient(requests) {
    let code = '#!/usr/bin/env python3\n';
    code += '"""Auto-generated ThreatBook API Client"""\n\n';
    code += 'import requests\n\n';
    code += 'BASE_URL = "https://your-instance.threatbook.net"\n\n';
    code += 'class ThreatBookClient:\n';
    code += '    def __init__(self, cookie_file="tdp_cookie.json"):\n';
    code += '        # Load cookies\n';
    code += '        import json\n';
    code += '        with open(cookie_file) as f:\n';
    code += '            cookies = json.load(f)\n';
    code += '        self.session = requests.Session()\n';
    code += '        # Set cookies...\n\n';
    
    // Add methods for each unique endpoint
    const seen = new Set();
    requests.forEach(req => {
      const path = req.url.split('?')[0];
      if (seen.has(path)) return;
      seen.add(path);
      
      const method = req.method.toLowerCase();
      const funcName = path.split('/').pop().replace(/-/g, '_');
      
      code += `\n`;
      code += `    def ${funcName}(self):\n`;
      code += `        """${req.apiPurpose?.desc || path}"""\n`;
      code += `        return self.session.${method}("${path}")\n`;
    });
    
    return code;
  }
  
  // Export functions to window
  window.__apiCapture = {
    version: '3.0',
    installed: new Date().toISOString(),
    config: CONFIG,
    
    // Get all captured requests
    getAll: function() {
      return window.__capturedRequests;
    },
    
    // Clear captured requests
    clear: function() {
      window.__capturedRequests = [];
      console.log('[API Capture] Cleared all captures');
    },
    
    // Export as Markdown docs
    exportDocs: function() {
      return generateMarkdownDocs(window.__capturedRequests);
    },
    
    // Export as Python client
    exportClient: function() {
      return generatePythonClient(window.__capturedRequests);
    },
    
    // Download captured data
    downloadJson: function(filename = 'api_capture.json') {
      const data = JSON.stringify(window.__capturedRequests, null, 2);
      const blob = new Blob([data], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    },
    
    // Download markdown docs
    downloadDocs: function(filename = 'api_docs.md') {
      const docs = generateMarkdownDocs(window.__capturedRequests);
      const blob = new Blob([docs], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    },
    
    // Print summary to console
    summary: function() {
      console.log('=== API Capture Summary ===');
      console.log('Total requests:', window.__capturedRequests.length);
      
      const groups = {};
      window.__capturedRequests.forEach(r => {
        const path = r.url.split('?')[0];
        groups[path] = (groups[path] || 0) + 1;
      });
      
      console.log('\nEndpoints:');
      for (const [path, count] of Object.entries(groups)) {
        console.log(`  ${count}x ${path}`);
      }
      
      console.log('\nCommands:');
      console.log('  window.__apiCapture.getAll()      - Get all captures');
      console.log('  window.__apiCapture.exportDocs()  - Get Markdown docs');
      console.log('  window.__apiCapture.downloadJson() - Download JSON');
      console.log('  window.__apiCapture.downloadDocs() - Download Markdown');
    }
  };
  
  console.log('[API Capture] v3.0 installed ✓');
  console.log('  - Captures: headers, body, response, page context');
  console.log('  - Auto-detects API purpose from URL patterns');
  console.log('  - Run window.__apiCapture.summary() for overview');
})();