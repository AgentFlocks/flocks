"""
GitLab AI Provider SDK

Provides integration with GitLab's AI capabilities including
code suggestions and chat completions.
"""

import os
from typing import Optional, Dict, Any, List, AsyncIterator

from flocks.provider.provider import (
    BaseProvider,
    ModelInfo,
    ModelCapabilities,
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.utils.log import Log

log = Log.create(service="provider.gitlab")


class GitLabProvider(BaseProvider):
    """
    GitLab AI Provider
    
    Integrates with GitLab's AI features including:
    - GitLab Duo Chat
    - Code Suggestions
    - AI-powered code completion
    """
    
    # Default models available through GitLab
    DEFAULT_MODELS_CONFIG = [
        {
            "id": "gitlab-duo-chat",
            "name": "GitLab Duo Chat",
            "context_window": 16000,
            "max_tokens": 4096,
            "supports_tools": False,
            "supports_vision": False,
            "supports_streaming": True,
            "description": "GitLab's AI-powered chat assistant",
        },
        {
            "id": "gitlab-code-suggestions",
            "name": "GitLab Code Suggestions",
            "context_window": 8000,
            "max_tokens": 2048,
            "supports_tools": False,
            "supports_vision": False,
            "supports_streaming": True,
            "description": "AI-powered code completion and suggestions",
        },
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        gitlab_url: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize GitLab provider
        
        Args:
            api_key: GitLab personal access token (or from GITLAB_TOKEN env)
            base_url: Override API base URL
            gitlab_url: GitLab instance URL (default: gitlab.com)
            **kwargs: Additional configuration
        """
        super().__init__(provider_id="gitlab", name="GitLab AI")
        
        # Get configuration from environment if not provided
        self.api_key = api_key or os.environ.get("GITLAB_TOKEN", "")
        
        # Determine GitLab instance URL
        self.gitlab_url = gitlab_url or os.environ.get("GITLAB_URL", "https://gitlab.com")
        
        # Build API URL
        if base_url:
            self._base_url = base_url
        else:
            self._base_url = f"{self.gitlab_url}/api/v4/ai"
        
        # Build models from config
        self._models_config = self.DEFAULT_MODELS_CONFIG.copy()
        
        log.info("gitlab.initialized", {
            "gitlab_url": self.gitlab_url,
            "base_url": self._base_url
        })
    
    def get_headers(self) -> Dict[str, str]:
        """Get request headers with GitLab authentication"""
        headers = {
            "Content-Type": "application/json",
        }
        
        if self.api_key:
            # GitLab uses PRIVATE-TOKEN header for authentication
            headers["PRIVATE-TOKEN"] = self.api_key
        
        return headers
    
    def get_models(self) -> List[ModelInfo]:
        """Get available GitLab AI models"""
        models = []
        for config in self._models_config:
            models.append(ModelInfo(
                id=config["id"],
                name=config["name"],
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=config.get("supports_streaming", True),
                    supports_tools=config.get("supports_tools", False),
                    supports_vision=config.get("supports_vision", False),
                    max_tokens=config.get("max_tokens", 4096),
                    context_window=config.get("context_window", 8192),
                ),
            ))
        return models
    
    def _messages_to_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """
        Convert messages list to a single prompt string
        
        Args:
            messages: List of conversation messages
            
        Returns:
            Formatted prompt string
        """
        parts = []
        
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "system":
                parts.append(f"System: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(f"User: {content}")
        
        return "\n\n".join(parts)
    
    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """
        Create a chat completion using GitLab Duo Chat
        
        Args:
            model_id: Model to use (default: gitlab-duo-chat)
            messages: List of conversation messages
            **kwargs: Additional parameters
            
        Returns:
            Chat completion response
        """
        # Convert ChatMessage to dict
        messages_dict = [{"role": m.role, "content": m.content} for m in messages]
        prompt = self._messages_to_prompt(messages_dict)
        
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._base_url}/duo_chat/completions",
                    headers=self.get_headers(),
                    json={
                        "prompt": prompt,
                        "model": model_id,
                    },
                    timeout=60.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    return ChatResponse(
                        id=data.get("id", "gitlab-response"),
                        model=model_id,
                        content=data.get("response", data.get("content", "")),
                        finish_reason="stop",
                        usage=data.get("usage", {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0
                        })
                    )
                else:
                    log.error("gitlab.chat.error", {
                        "status": response.status_code,
                        "body": response.text
                    })
                    raise Exception(f"GitLab API error: {response.status_code}")
                    
        except Exception as e:
            log.error("gitlab.chat.error", {"error": str(e)})
            raise
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """
        Create a streaming chat completion
        
        Args:
            model_id: Model to use
            messages: List of conversation messages
            **kwargs: Additional parameters
            
        Yields:
            Streaming response chunks
        """
        # Convert ChatMessage to dict
        messages_dict = [{"role": m.role, "content": m.content} for m in messages]
        prompt = self._messages_to_prompt(messages_dict)
        
        try:
            import httpx
            import json
            
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/duo_chat/completions",
                    headers=self.get_headers(),
                    json={
                        "prompt": prompt,
                        "model": model_id,
                        "stream": True,
                    },
                    timeout=120.0
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            
                            try:
                                chunk = json.loads(data)
                                
                                yield StreamChunk(
                                    delta=chunk.get("content", chunk.get("response", "")),
                                    finish_reason=None,
                                )
                            except Exception:
                                continue
                    
                    # Final chunk
                    yield StreamChunk(
                        delta="",
                        finish_reason="stop",
                    )
                    
        except Exception as e:
            log.error("gitlab.stream.error", {"error": str(e)})
            raise
    
    async def code_suggestions(
        self,
        content: str,
        filename: str,
        language: Optional[str] = None,
        cursor_position: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get code suggestions for a file
        
        Args:
            content: File content
            filename: File name
            language: Programming language
            cursor_position: Cursor position in content
            
        Returns:
            Code suggestions response
        """
        try:
            import httpx
            
            payload = {
                "content": content,
                "filename": filename,
            }
            
            if language:
                payload["language"] = language
            if cursor_position is not None:
                payload["cursor_position"] = cursor_position
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._base_url}/code_suggestions/completions",
                    headers=self.get_headers(),
                    json=payload,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    log.error("gitlab.suggestions.error", {
                        "status": response.status_code
                    })
                    raise Exception(f"GitLab API error: {response.status_code}")
                    
        except Exception as e:
            log.error("gitlab.suggestions.error", {"error": str(e)})
            raise
    
    async def health_check(self) -> Dict[str, Any]:
        """Check GitLab AI service health"""
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.gitlab_url}/api/v4/version",
                    headers=self.get_headers(),
                    timeout=10.0
                )
                
                return {
                    "healthy": response.status_code == 200,
                    "status_code": response.status_code,
                    "gitlab_url": self.gitlab_url,
                }
                
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "gitlab_url": self.gitlab_url,
            }


# Provider factory function
def create_provider(**kwargs) -> GitLabProvider:
    """Create a GitLab provider instance"""
    return GitLabProvider(**kwargs)
