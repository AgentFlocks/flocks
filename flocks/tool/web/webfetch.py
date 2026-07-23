"""
WebFetch Tool - Fetch web content

Fetches content from URLs with support for:
- HTML to Markdown/Text conversion
- Configurable timeout
- Response size limits
"""

import asyncio
import re
from typing import Optional
from html.parser import HTMLParser

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.utils.log import Log


log = Log.create(service="tool.webfetch")


# Constants
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5MB
DEFAULT_TIMEOUT = 30  # seconds
MAX_TIMEOUT = 120  # seconds


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
    html = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert line breaks
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    
    # Convert links
    html = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert bold
    html = re.sub(r'<(strong|b)[^>]*>(.*?)</\1>', r'**\2**', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert italic
    html = re.sub(r'<(em|i)[^>]*>(.*?)</\1>', r'*\2*', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert code
    html = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert pre blocks
    html = re.sub(r'<pre[^>]*>(.*?)</pre>', r'\n```\n\1\n```\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert lists
    html = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[ou]l[^>]*>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'</[ou]l>', '\n', html, flags=re.IGNORECASE)
    
    # Convert blockquotes
    html = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', r'\n> \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
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
    """Fetch a URL after applying Pentest Mode scope rules when active."""
    pentest_session = None
    pentest_store = None
    try:
        from flocks.pentest.policy import check_scope
        from flocks.pentest.store import PentestStateStore
        from flocks.session.session import Session

        pentest_session = await Session.get_by_id(ctx.session_id)
        if (
            pentest_session is not None
            and pentest_session.session_mode == "pentest"
        ):
            pentest_store = PentestStateStore.from_session(pentest_session)
            allowed, reason = check_scope(
                pentest_store.read("run.json").get("spec", {}),
                url,
            )
            if not allowed:
                return ToolResult(success=False, error=reason)
    except Exception as exc:
        return ToolResult(success=False, error=f"Pentest scope check failed: {exc}")

    def finalize(output: str, title: str) -> ToolResult:
        if pentest_store is not None:
            from flocks.pentest.redaction import redact_text

            refs = pentest_store.read("run.json").get("spec", {}).get(
                "secretRefs",
                [],
            )
            output = redact_text(output, refs)
        return ToolResult(success=True, output=output, title=title, metadata={})

    # Validate URL
    if not url.startswith("http://") and not url.startswith("https://"):
        return ToolResult(
            success=False,
            error="URL must start with http:// or https://"
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
        
        final_url = url
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_sec)) as response:
                final_url = str(response.url)
                if pentest_store is not None:
                    from flocks.pentest.policy import check_scope

                    allowed, reason = check_scope(
                        pentest_store.read("run.json").get("spec", {}),
                        final_url,
                    )
                    if not allowed:
                        return ToolResult(
                            success=False,
                            error=f"Redirect target denied: {reason}",
                        )
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
        
        title = f"{final_url} ({content_type})"
        
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
        
        return finalize(output, title)
        
    except ImportError:
        # Fallback to urllib if aiohttp not available
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })

            with urllib.request.urlopen(req, timeout=timeout_sec) as response:
                final_url = response.geturl()
                if pentest_store is not None:
                    from flocks.pentest.policy import check_scope

                    allowed, reason = check_scope(
                        pentest_store.read("run.json").get("spec", {}),
                        final_url,
                    )
                    if not allowed:
                        return ToolResult(
                            success=False,
                            error=f"Redirect target denied: {reason}",
                        )
                content = response.read().decode('utf-8', errors='replace')
                content_type = response.headers.get("Content-Type", "")

            title = f"{final_url} ({content_type})"

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

            return finalize(output, title)

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
