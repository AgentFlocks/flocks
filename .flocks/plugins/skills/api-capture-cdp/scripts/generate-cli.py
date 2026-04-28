#!/usr/bin/env python3
"""
Generate CLI/Documentation from captured API requests (v3.0 format)

Usage:
    python generate-cli.py captured_api.json --output mycli.py --base-url https://api.example.com
    python generate-cli.py captured_api.json --docs --output api_docs.md
"""

import json
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import List, Dict, Any, Optional


def sanitize_name(name: str) -> str:
    """Convert URL/path to valid Python identifier"""
    name = re.sub(r'\?.*$', '', name)
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_')
    if name and name[0].isdigit():
        name = '_' + name
    return name.lower() or 'endpoint'


def parse_endpoint(url: str) -> Dict[str, Any]:
    """Parse URL into components"""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]
    
    return {
        'url': url,
        'scheme': parsed.scheme,
        'netloc': parsed.netloc,
        'path': parsed.path,
        'path_parts': path_parts,
        'query': parse_qs(parsed.query),
        'resource': path_parts[-1] if path_parts else 'root'
    }


def group_endpoints(requests: List[Dict]) -> Dict[str, List[Dict]]:
    """Group endpoints by unique URL (deduped)"""
    groups = {}
    
    for req in requests:
        url = req.get('url', '')
        if not url:
            continue
            
        path = url.split('?')[0]
        if path not in groups:
            groups[path] = []
        groups[path].append(req)
    
    return groups


def extract_common_headers(requests: List[Dict]) -> Dict[str, str]:
    """Extract common headers from requests"""
    all_headers = {}
    
    for req in requests:
        headers = req.get('requestHeaders', {}) or req.get('request_headers', {})
        for k, v in headers.items():
            if k.lower() not in ['cookie', 'authorization']:
                all_headers[k] = v
    
    return all_headers


def parse_request_body(body_str: str) -> Any:
    """Parse request body from string"""
    if not body_str or body_str == '{}':
        return {}
    
    try:
        # Handle JSON string
        return json.loads(body_str)
    except:
        # Return as raw if not JSON
        return {"raw": body_str}


def parse_response_body(resp_str: str) -> Any:
    """Parse response body, handle truncation"""
    if not resp_str:
        return {}
    
    if resp_str.endswith('[truncated]'):
        # Try to parse what's there
        try:
            # Find the last complete JSON object
            for i in range(len(resp_str) - 20, -1, -1):
                if resp_str[i] == '{' or resp_str[i] == '[':
                    try:
                        return json.loads(resp_str[i:])
                    except:
                        continue
        except:
            pass
    
    try:
        return json.loads(resp_str)
    except:
        return {"raw": resp_str[:500], "note": "not valid JSON"}


def generate_python_client(requests: List[Dict], base_url: str) -> str:
    """Generate Python client code"""
    groups = group_endpoints(requests)
    
    code = '''#!/usr/bin/env python3
"""
Auto-generated API Client
Generated from captured API requests

Usage:
    client = APIClient(cookie_file="cookies.json")
    result = client.get_alarms()
"""

import json
import requests
from typing import Dict, Any, Optional, List
from urllib.parse import urljoin


class APIClient:
    """Auto-generated API Client"""
    
    def __init__(self, base_url: str = "{}", cookie_file: str = "cookies.json"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        
        # Load cookies
        try:
            with open(cookie_file) as f:
                cookies = json.load(f)
            for c in cookies:
                self.session.cookies.set(c.get("name"), c.get("value"))
        except FileNotFoundError:
            print(f"Warning: Cookie file {cookie_file} not found")
        
        # Common headers
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=UTF-8",
        })
    
    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        url = f"{{self.base_url}}{{endpoint}}"
        resp = self.session.request(method, url, json=data)
        resp.raise_for_status()
        return resp.json()


'''.format(json.dumps(base_url))
    
    for path, apis in groups.items():
        sample = apis[0]
        method = sample.get('method', 'POST').lower()
        func_name = sanitize_name(path.split('/')[-1])
        
        # Get purpose if available
        purpose = sample.get('apiPurpose', {})
        desc = purpose.get('desc', '') or 'API endpoint'
        name = purpose.get('name', func_name)
        
        # Parse request body schema
        req_body = parse_request_body(sample.get('requestBody', '{}'))
        
        code += f'''
    def {func_name}(self{", data" if req_body else ""}: Optional[Dict] = None) -> Dict:
        """{desc}
        
        Endpoint: {path}
        Page: {purpose.get("page", "N/A")}
        """
        return self._request("{method.upper()}", "{path}"{", data" if req_body else ""})
'''
    
    return code


def generate_markdown_docs(requests: List[Dict], title: str = "API Documentation") -> str:
    """Generate Markdown API documentation"""
    groups = group_endpoints(requests)
    
    md = f'''# {title}

> Auto-generated API Documentation  
> Total endpoints: {len(groups)}  
> Generated by api-capture tool

---

'''
    
    # Sort by path
    for path in sorted(groups.keys()):
        apis = groups[path]
        sample = apis[0]
        
        # Get purpose info
        purpose = sample.get('apiPurpose', {})
        api_name = purpose.get('name', sanitize_name(path.split('/')[-1]))
        api_desc = purpose.get('desc', 'Unknown API')
        page = purpose.get('page', 'N/A')
        
        # Request info
        method = sample.get('method', 'POST')
        req_body = parse_request_body(sample.get('requestBody', '{}'))
        
        # Response info
        resp = parse_response_body(sample.get('response', '{}'))
        
        # Headers (exclude sensitive)
        headers = sample.get('requestHeaders', {}) or sample.get('request_headers', {})
        safe_headers = {k: v for k, v in headers.items() 
                       if k.lower() not in ['cookie', 'authorization', 'tdp-authentication']}
        
        md += f'''## {api_name}

**用途**: {api_desc}  
**页面**: {page}

###基本信息

| 属性 | 值 |
|------|-----|
| Method | `{method}` |
| Endpoint | `{path}` |
| Status | {sample.get('status', 'N/A')} |
| Duration | {sample.get('duration', 'N/A')}ms |

'''
        
        if safe_headers:
            md += '''### 请求头

```http
'''
            for k, v in safe_headers.items():
                md += f'{k}: {v}\n'
            md += '```\n\n'
        
        if req_body:
            md += '''### 请求体

```json
'''
            md += json.dumps(req_body, indent=2, ensure_ascii=False) + '\n'
            md += '```\n\n'
        
        # Response preview
        if resp and resp != {}:
            # Show structure, not full data
            resp_preview = json.dumps(resp, indent=2, ensure_ascii=False)
            if len(resp_preview) > 1500:
                resp_preview = resp_preview[:1500] + '\n... // truncated'
            
            md += '''### 响应示例

```json
'''
            md += resp_preview + '\n'
            md += '```\n\n'
        
        # Add response code check
        if resp and 'response_code' in str(resp):
            md += '''### 响应码

| response_code | 含义 |
|---------------|------|
| 0 | 成功 |
| -1 | 失败/参数错误 |
| -2 | 未授权 |

'''
        
        md += '---\n\n'
    
    # Add usage section
    md += '''## 使用示例

### Python

```python
from generated_client import APIClient

client = APIClient(
    base_url="https://your-instance.threatbook.net",
    cookie_file="cookies.json"
)

# 调用 API
result = client.tag_list()
print(result)
```

### cURL

```bash
# 示例
curl -X POST "https://your-instance.threatbook.net/api/web/tag/list" \\
  -H "Content-Type: application/json" \\
  -d '{"type":"asset"}' \\
  -b "COOKIE_STRING"
```

'''
    
    return md


def generate_postman_collection(requests: List[Dict], base_url: str) -> Dict:
    """Generate Postman collection"""
    groups = group_endpoints(requests)
    
    collection = {
        "info": {
            "name": "Captured API Collection",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": []
    }
    
    for path in sorted(groups.keys()):
        apis = groups[path]
        sample = apis[0]
        
        purpose = sample.get('apiPurpose', {})
        
        item = {
            "name": purpose.get('name', path),
            "request": {
                "method": sample.get('method', 'POST'),
                "url": {
                    "raw": f"{{{{base_url}}}}{path}",
                    "host": ["{{base_url}}"],
                    "path": path.lstrip('/').split('/')
                },
                "header": [
                    {"key": k, "value": v}
                    for k, v in (sample.get('requestHeaders') or {}).items()
                    if k.lower() not in ['cookie', 'authorization']
                ]
            }
        }
        
        # Add body if present
        req_body = parse_request_body(sample.get('requestBody', '{}'))
        if req_body:
            item["request"]["body"] = {
                "mode": "raw",
                "raw": json.dumps(req_body, ensure_ascii=False),
                "options": {"raw": {"language": "json"}}
            }
        
        collection["item"].append(item)
    
    return collection


def main():
    parser = argparse.ArgumentParser(description='Generate CLI/docs from captured APIs')
    parser.add_argument('input', help='Input JSON file with captured requests')
    parser.add_argument('--output', '-o', help='Output file')
    parser.add_argument('--base-url', '-u', default='https://example.com', help='Base URL')
    parser.add_argument('--format', '-f', choices=['python', 'markdown', 'postman'], 
                       default='markdown', help='Output format')
    parser.add_argument('--title', '-t', default='API Documentation', help='Document title')
    
    args = parser.parse_args()
    
    # Load input
    with open(args.input) as f:
        data = json.load(f)
    
    # Handle both array and object formats
    requests = data if isinstance(data, list) else data.get('requests', [])
    
    if not requests:
        print("No requests found in input file", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing {len(requests)} requests, {len(group_endpoints(requests))} unique endpoints...")
    
    # Generate output
    if args.format == 'python':
        output = generate_python_client(requests, args.base_url)
    elif args.format == 'postman':
        output = json.dumps(generate_postman_collection(requests, args.base_url), indent=2, ensure_ascii=False)
    else:
        output = generate_markdown_docs(requests, args.title)
    
    # Write output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Written to {args.output}")
    else:
        print(output)


if __name__ == '__main__':
    main()