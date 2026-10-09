"""
WebFetch Tool - Fetch web content

Fetches content from URLs with support for:
- HTML to Markdown/Text conversion
- Configurable timeout
- Response size limits
"""

import asyncio
import ipaddress
import os
import re
import socket
from typing import Optional
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.utils.log import Log


log = Log.create(service="tool.webfetch")


# Constants
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5MB
DEFAULT_TIMEOUT = 30  # seconds
MAX_TIMEOUT = 120  # seconds
MAX_REDIRECTS = 5

# SSRF guard: webfetch must not reach loopback / private / link-local / reserved
# networks (including the cloud metadata endpoint 169.254.169.254). Operators can
# allow specific internal hosts/IPs for local testing via a comma-separated env
# list, e.g. FLOCKS_WEBFETCH_ALLOW_INTERNAL="127.0.0.1,localhost".
_ALLOW_INTERNAL_ENV = "FLOCKS_WEBFETCH_ALLOW_INTERNAL"


def _internal_allowlist() -> set[str]:
    raw = os.environ.get(_ALLOW_INTERNAL_ENV, "") or ""
    return {token.strip().lower() for token in raw.split(",") if token.strip()}


def _ip_is_internal(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _ssrf_error(url: str) -> Optional[str]:
    """Return an error message if *url* resolves to an internal address.

    DNS-resolves the host and rejects the request when any resolved address is
    loopback/private/link-local/reserved, so a public hostname that points at an
    internal IP (DNS rebinding) is also caught. Returns None when the target is
    allowed. Resolution failures return None so the request fails naturally with
    the normal "Request failed" path rather than being masked as an SSRF block.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return "Invalid URL: missing host"

    allow = _internal_allowlist()
    if host.lower() in allow:
        return None

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return None

    for info in infos:
        ip = info[4][0]
        # Strip IPv6 zone id if present (e.g. fe80::1%en0)
        ip = ip.split("%", 1)[0]
        if ip.lower() in allow:
            continue
        if _ip_is_internal(ip):
            return (
                f"Blocked request to internal or reserved address ({ip}). "
                "webfetch does not allow access to loopback, private, link-local, "
                "or metadata networks."
            )
    return None


DESCRIPTION = """Fetch content from a specified URL and return its contents in a readable format.

Usage:
- The URL must be a fully-formed, valid URL starting with http:// or https://
- By default, returns content in markdown format (HTML is converted)
- Supports text, markdown, and html output formats
- Has a default timeout of 30 seconds (configurable up to 120 seconds)
- Response size is limited to 5MB"""


class HTMLTextExtractor(HTMLParser):
    """Extract text content from HTML, skipping script/style tags"""
    
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {'script', 'style', 'noscript', 'iframe', 'object', 'embed'}
        self.current_skip = False
        self._skip_stack = []
    
    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.skip_tags:
            self._skip_stack.append(True)
            self.current_skip = True
        else:
            self._skip_stack.append(False)
    
    def handle_endtag(self, tag):
        if self._skip_stack:
            self._skip_stack.pop()
        self.current_skip = any(self._skip_stack)
    
    def handle_data(self, data):
        if not self.current_skip:
            text = data.strip()
            if text:
                self.text_parts.append(text)
    
    def get_text(self) -> str:
        return ' '.join(self.text_parts)


def html_to_markdown(html: str) -> str:
    """
    Convert HTML to Markdown
    
    Simple conversion that handles common HTML elements.
    
    Args:
        html: HTML content
        
    Returns:
        Markdown content
    """
    # Remove script and style tags
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert headers
    for i in range(6, 0, -1):
        html = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', r'\n' + '#' * i + r' \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert paragraphs
    html = re.sub(r'<p\b[^>]*>(.*?)</p>', r'\n\1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert line breaks
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    
    # Convert links
    html = re.sub(r'<a\b[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert bold
    # ``\b`` keeps ``<b>`` from swallowing ``<body>``/``<br>``/``<blockquote>`` (and ``<i>`` from ``<img>``/``<input>``).
    html = re.sub(r'<(strong|b)\b[^>]*>(.*?)</\1>', r'**\2**', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert italic
    html = re.sub(r'<(em|i)\b[^>]*>(.*?)</\1>', r'*\2*', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert code
    html = re.sub(r'<code\b[^>]*>(.*?)</code>', r'`\1`', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert pre blocks
    html = re.sub(r'<pre\b[^>]*>(.*?)</pre>', r'\n```\n\1\n```\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert lists
    html = re.sub(r'<li\b[^>]*>(.*?)</li>', r'\n- \1', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[ou]l[^>]*>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'</[ou]l>', '\n', html, flags=re.IGNORECASE)
    
    # Convert blockquotes
    html = re.sub(r'<blockquote\b[^>]*>(.*?)</blockquote>', r'\n> \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert horizontal rules
    html = re.sub(r'<hr[^>]*/?>', '\n---\n', html, flags=re.IGNORECASE)
    
    # Remove remaining HTML tags
    html = re.sub(r'<[^>]+>', '', html)
    
    # Decode HTML entities
    html = html.replace('&nbsp;', ' ')
    html = html.replace('&amp;', '&')
    html = html.replace('&lt;', '<')
    html = html.replace('&gt;', '>')
    html = html.replace('&quot;', '"')
    html = html.replace('&#39;', "'")
    
    # Clean up whitespace
    html = re.sub(r'\n\s*\n\s*\n', '\n\n', html)
    html = re.sub(r' +', ' ', html)
    
    return html.strip()


def extract_text_from_html(html: str) -> str:
    """
    Extract plain text from HTML
    
    Args:
        html: HTML content
        
    Returns:
        Plain text
    """
    parser = HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.get_text()


@ToolRegistry.register_function(
    name="webfetch",
    group="检索",
    description=DESCRIPTION,
    category=ToolCategory.BROWSER,
    parameters=[
        ToolParameter(
            name="url",
            type=ParameterType.STRING,
            description="The URL to fetch content from",
            required=True
        ),
        ToolParameter(
            name="format",
            type=ParameterType.STRING,
            description="The format to return content in (text, markdown, or html). Defaults to markdown.",
            required=False,
            default="markdown",
            enum=["text", "markdown", "html"]
        ),
        ToolParameter(
            name="timeout",
            type=ParameterType.INTEGER,
            description="Optional timeout in seconds (max 120)",
            required=False,
            default=DEFAULT_TIMEOUT
        ),
    ]
)
async def webfetch_tool(
    ctx: ToolContext,
    url: str,
    format: str = "markdown",
    timeout: Optional[int] = None,
) -> ToolResult:
    """
    Fetch content from a URL
    
    Args:
        ctx: Tool context
        url: URL to fetch
        format: Output format (text, markdown, html)
        timeout: Timeout in seconds
        
    Returns:
        ToolResult with fetched content
    """
    # Validate URL
    if not url.startswith("http://") and not url.startswith("https://"):
        return ToolResult(
            success=False,
            error="URL must start with http:// or https://"
        )
    if format not in ("text", "markdown", "html"):
        return ToolResult(
            success=False,
            error=f"Invalid format '{format}': expected one of text, markdown, html"
        )
    if timeout is not None and timeout < 0:
        return ToolResult(
            success=False,
            error=f"Invalid timeout {timeout}: must be a non-negative number of seconds (max {MAX_TIMEOUT})"
        )
    
    # Request permission
    await ctx.ask(
        permission="webfetch",
        patterns=[url],
        always=["*"],
        metadata={
            "url": url,
            "format": format,
            "timeout": timeout
        }
    )
    
    # SSRF guard: reject internal/reserved targets before any request is made.
    ssrf_error = _ssrf_error(url)
    if ssrf_error:
        return ToolResult(success=False, error=ssrf_error)

    # Calculate timeout
    timeout_sec = min(timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)

    try:
        import aiohttp

        # Build headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        # Set Accept header based on format
        if format == "markdown":
            headers["Accept"] = "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
        elif format == "text":
            headers["Accept"] = "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
        elif format == "html":
            headers["Accept"] = "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, */*;q=0.1"
        else:
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        from contextlib import AsyncExitStack

        from flocks.net.aio import make_session

        async with AsyncExitStack() as stack:
            # Follow redirects manually so every hop is SSRF-checked; aiohttp's
            # automatic redirect handling would otherwise let a public URL bounce
            # to an internal address. Each hop gets its own session because the
            # outbound proxy policy (flocks.json network.proxy) decides per target
            # whether it goes direct (intranet whitelist) or through the proxy, and a
            # SOCKS proxy is bound to the session's connector.
            current_url = url
            response_cm = None
            for _ in range(MAX_REDIRECTS + 1):
                session = await stack.enter_async_context(make_session(target_url=current_url))
                response_cm = session.get(
                    current_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_sec),
                    allow_redirects=False,
                )
                response = await response_cm.__aenter__()
                if response.status in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                    next_url = urljoin(current_url, response.headers["Location"])
                    await response_cm.__aexit__(None, None, None)
                    if not next_url.startswith("http://") and not next_url.startswith("https://"):
                        return ToolResult(success=False, error="URL must start with http:// or https://")
                    redirect_error = _ssrf_error(next_url)
                    if redirect_error:
                        return ToolResult(success=False, error=redirect_error)
                    current_url = next_url
                    continue
                break
            else:
                await response_cm.__aexit__(None, None, None)
                return ToolResult(success=False, error="Too many redirects")

            try:
                if response.status != 200:
                    return ToolResult(
                        success=False,
                        error=f"Request failed with status code: {response.status}"
                    )

                # Check content length
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                    return ToolResult(
                        success=False,
                        error="Response too large (exceeds 5MB limit)"
                    )

                content = await response.text()

                if len(content.encode('utf-8')) > MAX_RESPONSE_SIZE:
                    return ToolResult(
                        success=False,
                        error="Response too large (exceeds 5MB limit)"
                    )

                content_type = response.headers.get("Content-Type", "")
            finally:
                await response_cm.__aexit__(None, None, None)

        title = f"{url} ({content_type})"
        
        # Process content based on format
        if format == "markdown":
            if "text/html" in content_type:
                output = html_to_markdown(content)
            else:
                output = content
        elif format == "text":
            if "text/html" in content_type:
                output = extract_text_from_html(content)
            else:
                output = content
        else:  # html
            output = content
        
        return ToolResult(
            success=True,
            output=output,
            title=title,
            metadata={}
        )
        
    except ImportError:
        # Fallback to urllib if aiohttp not available
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })

            # outbound: env — aiohttp is a hard dependency, this fallback only follows HTTP_PROXY
            with urllib.request.urlopen(req, timeout=timeout_sec) as response:
                content = response.read().decode('utf-8', errors='replace')
                content_type = response.headers.get("Content-Type", "")

            title = f"{url} ({content_type})"

            if format == "markdown":
                if "text/html" in content_type:
                    output = html_to_markdown(content)
                else:
                    output = content
            elif format == "text":
                if "text/html" in content_type:
                    output = extract_text_from_html(content)
                else:
                    output = content
            else:
                output = content

            return ToolResult(
                success=True,
                output=output,
                title=title,
                metadata={}
            )

        except urllib.error.HTTPError as e:
            return ToolResult(
                success=False,
                error=f"Request failed with status code: {e.code}"
            )
        except urllib.error.URLError as e:
            return ToolResult(
                success=False,
                error=f"Request failed: {str(e.reason)}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Request failed: {str(e)}"
            )
            
    except asyncio.TimeoutError:
        return ToolResult(
            success=False,
            error="Request timed out"
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Request failed: {str(e)}"
        )
