"""
WebSearch Tool - Web search

Performs web searches using external search APIs.
Requires API configuration to function.
"""

import json
from typing import Optional
from datetime import datetime

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.utils.log import Log


log = Log.create(service="tool.websearch")


# API configuration (can be overridden)
API_BASE_URL = "https://mcp.exa.ai"
DEFAULT_NUM_RESULTS = 8
DEFAULT_TIMEOUT = 25  # seconds


def get_description() -> str:
    """Get tool description with current date"""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""Search the web for real-time information about any topic.

Use this tool when you need:
- Up-to-date information that might not be in training data
- Current events or technology news
- Documentation for libraries, frameworks, or tools
- Verification of current facts

Today's date: {today}
Use the current year when searching for recent information.

Parameters:
- query: Search query (be specific for better results)
- numResults: Number of results to return (default: 8)
- type: Search type - auto, fast, or deep"""


@ToolRegistry.register_function(
    name="websearch",
    group="检索",
    description=get_description(),
    category=ToolCategory.SEARCH,
    parameters=[
        ToolParameter(
            name="query",
            type=ParameterType.STRING,
            description="Web search query",
            required=True
        ),
        ToolParameter(
            name="numResults",
            type=ParameterType.INTEGER,
            description="Number of search results to return (default: 8)",
            required=False,
            default=DEFAULT_NUM_RESULTS
        ),
        ToolParameter(
            name="type",
            type=ParameterType.STRING,
            description="Search type - 'auto': balanced, 'fast': quick, 'deep': comprehensive",
            required=False,
            default="auto",
            enum=["auto", "fast", "deep"]
        ),
    ]
)
async def websearch_tool(
    ctx: ToolContext,
    query: str,
    numResults: int = DEFAULT_NUM_RESULTS,
    type: str = "auto",
) -> ToolResult:
    """
    Perform a web search
    
    Args:
        ctx: Tool context
        query: Search query
        numResults: Number of results
        type: Search type
        
    Returns:
        ToolResult with search results
    """
    if not query:
        return ToolResult(
            success=False,
            error="Search query is required"
        )
    
    # Request permission
    await ctx.ask(
        permission="websearch",
        patterns=[query],
        always=["*"],
        metadata={
            "query": query,
            "numResults": numResults,
            "type": type
        }
    )
    
    title = f"Web search: {query}"
    
    try:
        import aiohttp
        
        # Build request
        request_data = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": query,
                    "type": type,
                    "numResults": numResults,
                    "livecrawl": "fallback"
                }
            }
        }
        
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE_URL}/mcp",
                json=request_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return ToolResult(
                        success=False,
                        error=f"Search error ({response.status}): {error_text}",
                        title=title
                    )
                
                response_text = await response.text()
                
                # Parse SSE response
                for line in response_text.split("\n"):
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if data.get("result", {}).get("content"):
                            content = data["result"]["content"]
                            if content:
                                return ToolResult(
                                    success=True,
                                    output=content[0].get("text", ""),
                                    title=title,
                                    metadata={}
                                )
                
                return ToolResult(
                    success=True,
                    output="No search results found. Please try a different query.",
                    title=title,
                    metadata={}
                )
                
    except ImportError:
        return ToolResult(
            success=False,
            error="Web search requires aiohttp library. Install with: pip install aiohttp",
            title=title
        )
    except Exception as e:
        if "timeout" in str(e).lower() or "abort" in str(e).lower():
            return ToolResult(
                success=False,
                error="Search request timed out",
                title=title
            )
        
        return ToolResult(
            success=False,
            error=f"Search failed: {str(e)}",
            title=title
        )
