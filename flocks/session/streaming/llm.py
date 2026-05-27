"""
LLM interaction module

Handles LLM API calls, streaming, and response processing.
Based on Flocks' ported src/session/llm.ts
"""

from typing import List, Dict, Any, Optional, AsyncIterator, Callable
from dataclasses import dataclass, field
import asyncio
from datetime import datetime

from flocks.utils.log import Log
from flocks.provider.provider import Provider, ChatMessage
from flocks.session.message import Message, MessageInfo, MessageRole, TokenUsage

log = Log.create(service="llm")


class LLMError(Exception):
    """LLM operation error"""
    pass


@dataclass
class ProviderMetadata:
    """
    Provider-specific metadata from LLM response
    
    Matches TypeScript ProviderMetadata from ai SDK
    """
    anthropic: Optional[Dict[str, Any]] = None
    bedrock: Optional[Dict[str, Any]] = None
    google: Optional[Dict[str, Any]] = None
    openai: Optional[Dict[str, Any]] = None
    
    def get_cache_creation_tokens(self) -> int:
        """Get cache creation input tokens from provider metadata"""
        if self.anthropic:
            return self.anthropic.get("cacheCreationInputTokens", 0)
        if self.bedrock:
            usage = self.bedrock.get("usage", {})
            return usage.get("cacheWriteInputTokens", 0)
        return 0


@dataclass
class LLMUsage:
    """
    LLM usage information
    
    Matches TypeScript LanguageModelUsage from ai SDK
    """
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0
    
    def to_token_usage(self, metadata: Optional[ProviderMetadata] = None) -> TokenUsage:
        """Convert to TokenUsage model"""
        # Handle provider-specific adjustments
        excludes_cached = metadata and (metadata.anthropic or metadata.bedrock)
        adjusted_input = self.input_tokens if excludes_cached else (self.input_tokens - self.cached_input_tokens)
        
        cache_write = metadata.get_cache_creation_tokens() if metadata else 0
        
        return TokenUsage(
            input=max(0, adjusted_input),
            output=self.output_tokens,
            reasoning=self.reasoning_tokens,
            cache_read=self.cached_input_tokens,
            cache_write=cache_write,
        )


@dataclass
class LLMResponse:
    """LLM response with content and metadata"""
    content: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    metadata: Optional[ProviderMetadata] = None
    finish_reason: str = "stop"
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class StreamResult:
    """Streaming result with async text getter"""
    text: asyncio.Future
    usage: Optional[LLMUsage] = None
    metadata: Optional[ProviderMetadata] = None


class ResponseCache:
    """
    Simple response cache for LLM responses
    
    Caches responses keyed by message hash for repeated queries
    """
    
    _cache: Dict[str, LLMResponse] = {}
    _max_size: int = 100
    _ttl_seconds: int = 3600  # 1 hour
    _timestamps: Dict[str, datetime] = {}
    
    @classmethod
    def get(cls, key: str) -> Optional[LLMResponse]:
        """Get cached response if exists and not expired"""
        if key not in cls._cache:
            return None
        
        # Check TTL
        timestamp = cls._timestamps.get(key)
        if timestamp and (datetime.now() - timestamp).total_seconds() > cls._ttl_seconds:
            del cls._cache[key]
            del cls._timestamps[key]
            return None
        
        return cls._cache[key]
    
    @classmethod
    def set(cls, key: str, response: LLMResponse) -> None:
        """Cache a response"""
        # Evict oldest if at capacity
        if len(cls._cache) >= cls._max_size:
            oldest_key = min(cls._timestamps, key=cls._timestamps.get)
            del cls._cache[oldest_key]
            del cls._timestamps[oldest_key]
        
        cls._cache[key] = response
        cls._timestamps[key] = datetime.now()
    
    @classmethod
    def clear(cls) -> None:
        """Clear all cached responses"""
        cls._cache.clear()
        cls._timestamps.clear()


class LLM:
    """
    LLM interaction namespace
    
    Mirrors original Flocks LLM namespace from llm.ts
    
    .. note::
        The primary execution path (``runner.py`` → ``StreamProcessor``) calls
        the provider directly via ``Provider.stream()`` and does NOT go through
        ``LLM.chat()`` / ``LLM.chat_stream()``. These methods and
        ``ResponseCache`` are retained for potential standalone / testing use
        but are effectively unused in production.
    """
    
    @classmethod
    def _hash_messages(cls, messages: List[Dict[str, Any]], model: str) -> str:
        """Create hash key for message list"""
        import hashlib
        content = f"{model}:" + "|".join(
            f"{m.get('role', '')}:{m.get('content', '')[:100]}"
            for m in messages
        )
        return hashlib.md5(content.encode()).hexdigest()
    
    @classmethod
    async def chat(
        cls,
        session_id: str,
        messages: List[MessageInfo],
        model: str,
        provider_id: str,
        use_cache: bool = False,
        **kwargs
    ) -> MessageInfo:
        """
        Send chat request to LLM
        
        Args:
            session_id: Session ID
            messages: Message history
            model: Model ID
            provider_id: Provider ID
            use_cache: Whether to use response cache
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            Assistant message
        """
        try:
            # Get provider
            provider = Provider.get(provider_id)
            if not provider:
                raise LLMError(f"Provider {provider_id} not found")
            
            # Convert messages to provider format
            chat_messages = []
            for msg in messages:
                content = await Message.get_text_content(msg)
                chat_messages.append(ChatMessage(
                    role=msg.role.value,
                    content=content,
                ))
            
            # Check cache
            cache_key = None
            if use_cache:
                cache_key = cls._hash_messages(
                    [{"role": m.role, "content": m.content} for m in chat_messages],
                    model
                )
                cached = ResponseCache.get(cache_key)
                if cached:
                    log.info("llm.chat.cache_hit", {"session_id": session_id})
                    return await cls._create_assistant_message(
                        session_id, model, cached.content, cached.usage, cached.metadata
                    )
            
            # Call provider
            log.info("llm.chat.start", {
                "session_id": session_id,
                "model": model,
                "provider": provider_id,
                "message_count": len(chat_messages),
            })
            
            with log.time("llm.chat"):
                response = await provider.chat(
                    model_id=model,
                    messages=chat_messages,
                    **kwargs
                )
            
            # Parse usage and metadata
            usage = LLMUsage(
                input_tokens=response.usage.get("prompt_tokens", 0) if response.usage else 0,
                output_tokens=response.usage.get("completion_tokens", 0) if response.usage else 0,
                total_tokens=response.usage.get("total_tokens", 0) if response.usage else 0,
            )
            
            # Cache response if enabled
            if cache_key:
                ResponseCache.set(cache_key, LLMResponse(
                    content=response.content,
                    usage=usage,
                ))
            
            # Create assistant message
            assistant_message = await cls._create_assistant_message(
                session_id, model, response.content, usage
            )
            
            log.info("llm.chat.complete", {
                "session_id": session_id,
                "message_id": assistant_message.id,
                "tokens": assistant_message.tokens.model_dump() if assistant_message.tokens else None,
            })
            
            return assistant_message
            
        except Exception as e:
            log.error("llm.chat.error", {
                "session_id": session_id,
                "error": str(e),
            })
            raise LLMError(f"LLM chat failed: {str(e)}")
    
    @classmethod
    async def _create_assistant_message(
        cls,
        session_id: str,
        model: str,
        content: str,
        usage: Optional[LLMUsage] = None,
        metadata: Optional[ProviderMetadata] = None,
    ) -> MessageInfo:
        """Create assistant message with proper token usage"""
        token_usage = None
        if usage:
            token_usage = usage.to_token_usage(metadata)
        
        return await Message.create(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content=content,
            model=model,
            tokens=token_usage,
        )
    
    @classmethod
    async def chat_stream(
        cls,
        session_id: str,
        messages: List[MessageInfo],
        model: str,
        provider_id: str,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Send streaming chat request to LLM
        
        Args:
            session_id: Session ID
            messages: Message history
            model: Model ID
            provider_id: Provider ID
            **kwargs: Additional parameters
            
        Yields:
            Text chunks
        """
        try:
            # Get provider
            provider = Provider.get(provider_id)
            if not provider:
                raise LLMError(f"Provider {provider_id} not found")
            
            # Convert messages
            chat_messages = []
            for msg in messages:
                content = await Message.get_text_content(msg)
                chat_messages.append(ChatMessage(
                    role=msg.role.value,
                    content=content,
                ))
            
            log.info("llm.stream.start", {
                "session_id": session_id,
                "model": model,
                "provider": provider_id,
            })
            
            # Stream response
            full_content = []
            async for chunk in provider.chat_stream(
                model_id=model,
                messages=chat_messages,
                **kwargs
            ):
                # Extract text from StreamChunk
                text = chunk.delta if hasattr(chunk, 'delta') else str(chunk)
                full_content.append(text)
                yield text
            
            # Create assistant message with full content
            await Message.create(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content="".join(full_content),
                model=model,
            )
            
            log.info("llm.stream.complete", {
                "session_id": session_id,
                "length": len(full_content),
            })
            
        except Exception as e:
            log.error("llm.stream.error", {
                "session_id": session_id,
                "error": str(e),
            })
            raise LLMError(f"LLM stream failed: {str(e)}")
    
    @classmethod
    async def stream(
        cls,
        agent: Any,
        user: Any,
        model: Any,
        messages: List[Dict[str, Any]],
        system: List[str],
        tools: Dict[str, Any],
        abort: Any,
        session_id: str,
        small: bool = False,
        retries: int = 3,
    ) -> StreamResult:
        """
        Stream LLM response (TypeScript LLM.stream compatible)
        
        This is the advanced streaming interface matching TypeScript
        
        Args:
            agent: Agent info
            user: User message info
            model: Model info
            messages: Formatted messages
            system: System prompts
            tools: Tool definitions
            abort: Abort signal
            session_id: Session ID
            small: Use small/fast model
            retries: Number of retries
            
        Returns:
            StreamResult with async text future
        """
        # Create future for final text
        loop = asyncio.get_event_loop()
        text_future = loop.create_future()
        
        async def _stream():
            full_text = []
            try:
                provider_id = model.get("providerID") if isinstance(model, dict) else getattr(model, "providerID", "openai")
                model_id = model.get("modelID") if isinstance(model, dict) else getattr(model, "id", "gpt-4")
                
                provider = Provider.get(provider_id)
                if not provider:
                    raise LLMError(f"Provider {provider_id} not found")
                
                # Build messages with system prompt
                chat_messages = []
                if system:
                    chat_messages.append(ChatMessage(role="system", content="\n\n".join(system)))
                
                for msg in messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        # Extract text from content parts
                        text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                        content = "\n".join(text_parts)
                    chat_messages.append(ChatMessage(role=role, content=content))
                
                async for chunk in provider.chat_stream(
                    model_id=model_id,
                    messages=chat_messages,
                ):
                    text = chunk.delta if hasattr(chunk, 'delta') else str(chunk)
                    full_text.append(text)
                
                result = "".join(full_text)
                text_future.set_result(result)
                
            except Exception as e:
                text_future.set_exception(e)
        
        # Start streaming in background
        asyncio.create_task(_stream())
        
        return StreamResult(text=text_future)
    
    @classmethod
    async def generate_title(
        cls,
        messages: List[MessageInfo],
        model: str = "gpt-4o-mini",
        provider_id: str = "openai",
    ) -> str:
        """
        Generate a title for the conversation
        
        Args:
            messages: Message history
            model: Model ID
            provider_id: Provider ID
            
        Returns:
            Generated title
        """
        try:
            if not messages:
                return "New Session"
            
            # Get first few messages
            context_messages = messages[:3]
            context_parts = []
            for msg in context_messages:
                text = await Message.get_text_content(msg)
                context_parts.append(f"{msg.role.value}: {text[:200]}")
            context = "\n".join(context_parts)
            
            # Create title generation prompt
            provider = Provider.get(provider_id)
            if not provider:
                return "New Session"
            
            title_messages = [
                ChatMessage(
                    role="user",
                    content=f"Generate a short, descriptive title (max 50 chars) for this conversation:\n\n{context}\n\nTitle:",
                )
            ]
            
            response = await provider.chat(
                model_id=model,
                messages=title_messages,
                max_tokens=50,
                temperature=0.7,
            )
            
            title = response.content.strip().strip('"\'')
            
            # Clean up thinking tags
            title = title.replace("<think>", "").replace("</think>", "").strip()
            
            # Get first non-empty line
            for line in title.split("\n"):
                line = line.strip()
                if line:
                    title = line
                    break
            
            log.info("title.generated", {"title": title})
            
            return title[:50]  # Limit length
            
        except Exception as e:
            log.error("title.generation.error", {"error": str(e)})
            return "New Session"
    
    @classmethod
    async def count_tokens(
        cls,
        text: str,
        model: str = "gpt-4",
    ) -> int:
        """
        Estimate token count for text using a lightweight chars/4 heuristic.

        Args:
            text: Text to count
            model: Model ID (kept for API compatibility)

        Returns:
            Estimated token count
        """
        del model
        if not text:
            return 0
        return len(text) // 4
    
    @classmethod
    async def retry_with_backoff(
        cls,
        func: Callable,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
    ) -> Any:
        """
        Retry a function with exponential backoff
        
        Args:
            func: Async function to retry
            max_retries: Maximum number of retries
            initial_delay: Initial delay in seconds
            backoff_factor: Backoff multiplier
            
        Returns:
            Function result
        """
        delay = initial_delay
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                return await func()
            except Exception as e:
                last_error = e
                
                if attempt < max_retries:
                    log.warn("llm.retry", {
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "delay": delay,
                        "error": str(e),
                    })
                    
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
                else:
                    log.error("llm.retry.failed", {
                        "attempts": max_retries + 1,
                        "error": str(e),
                    })
        
        raise last_error or LLMError("Retry failed")
