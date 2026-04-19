"""
Google (Gemini) provider implementation - High Quality Version
"""

import os
import json
import re
from typing import List, AsyncIterator, Optional, Dict, Any

from flocks.provider.provider import (
    BaseProvider,
    ModelInfo,
    ModelCapabilities,
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.utils.log import Log

log = Log.create(service="provider.google")


class GoogleProvider(BaseProvider):
    """Google (Gemini) provider with ReAct text-to-tool parsing and robust error handling"""

    CATALOG_ID = "google"

    def __init__(self):
        super().__init__(provider_id="google", name="Google")
        self._api_key = os.getenv("GOOGLE_API_KEY")
        self._client = None
    
    def is_configured(self) -> bool:
        """Check if provider is configured"""
        api_key = self._config.api_key if self._config else self._api_key
        return bool(api_key)

    def get_meta(self):
        from flocks.provider.model_catalog import get_provider_meta
        return get_provider_meta("google") or super().get_meta()

    def _get_client(self):
        """Get or create Google Generative AI client"""
        if self._client is None:
            try:
                from google import genai
                api_key = self._config.api_key if self._config else self._api_key
                if not api_key:
                    raise ValueError("Google API key not configured")
                self._client = genai.Client(api_key=api_key)
            except ImportError:
                raise ImportError("google-genai package not installed. Install with: pip install google-genai")
        return self._client
    
    def get_models(self) -> List[ModelInfo]:
        """Return configured models"""
        return list(getattr(self, "_config_models", []))
    
    def _convert_messages(self, messages: List[ChatMessage]) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """
        Robust ReAct-style message conversion for Gemini 3.
        Rewrites history as text to bypass binary thought_signature requirements.
        """
        system_msg = "You are a professional SecOps assistant. Use tools to complete tasks. " \
                     "If you decide to call a tool, you MUST use the function calling API. "
        
        session_id = None
        if messages:
            session_id = getattr(messages[0], "sessionID", None)
            
        raw_gemini_messages = []
        processed_mwps = False

        # Try database-backed parts for perfect reasoning retrieval
        if session_id:
            try:
                from flocks.session.message import MessageSync
                mwps = MessageSync.list_with_parts(session_id)
                for mwp in mwps:
                    info = mwp.info
                    role = getattr(info, "role", "assistant")
                    if role == "system":
                        for p in mwp.parts:
                            if p.type == "text":
                                system_msg += "\n" + p.text
                        continue
                    
                    parts = []
                    for p in mwp.parts:
                        if p.type == "reasoning" and p.text:
                            parts.append({"text": f"Thought: {p.text}"})
                        elif p.type == "text" and p.text:
                            parts.append({"text": p.text})
                        elif p.type == "tool" and hasattr(p, "state"):
                            if role == "assistant":
                                parts.append({"text": f"Action: Called tool '{p.tool}' with arguments {json.dumps(p.state.input, ensure_ascii=False)}."})
                            elif role == "tool":
                                output = getattr(p.state, "output", "")
                                if getattr(p.state, "status", "") == "error":
                                    output = f"Error: {getattr(p.state, 'error', 'Unknown error')}"
                                parts.append({"text": f"Observation from tool '{p.tool}':\n{output}"})
                        elif p.type == "file":
                            mime = getattr(p, "mime", "")
                            if mime.startswith("image/"):
                                parts.append({
                                    "inline_data": {
                                        "data": p.url.split(",")[-1] if "," in p.url else p.url,
                                        "mime_type": mime
                                    }
                                })
                    
                    gemini_role = "model" if role == "assistant" else "user"
                    if parts:
                        raw_gemini_messages.append({"role": gemini_role, "parts": parts})
                processed_mwps = True
            except Exception as e:
                log.warning("provider.google.db_sync_failed", {"error": str(e)})

        # Fallback to standard messages
        if not processed_mwps:
            for msg in messages:
                role = msg.role
                if role == "system":
                    system_msg += "\n" + str(msg.content)
                    continue
                
                parts = []
                reasoning = getattr(msg, "reasoning", None) or msg.custom_settings.get("reasoning")
                if reasoning:
                    parts.append({"text": f"Thought: {reasoning}"})
                
                if msg.content and isinstance(msg.content, str):
                    parts.append({"text": msg.content})
                elif isinstance(msg.content, list):
                    for p in msg.content:
                        if isinstance(p, dict) and p.get("type") == "text":
                            parts.append({"text": p["text"]})
                
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        f = tc.get("function", {})
                        parts.append({"text": f"Action: Called tool '{f.get('name')}' with arguments {f.get('arguments')}."})
                
                gemini_role = "model" if role == "assistant" else "user"
                if role == "tool":
                    parts.append({"text": f"Observation from tool '{msg.name}':\n{msg.content}"})
                
                if parts:
                    raw_gemini_messages.append({"role": gemini_role, "parts": parts})

        # Merge roles and ensure user start
        final_messages = []
        for msg in raw_gemini_messages:
            if not final_messages:
                final_messages.append(msg)
            elif final_messages[-1]["role"] == msg["role"]:
                final_messages[-1]["parts"].extend(msg["parts"])
            else:
                final_messages.append(msg)
        
        if final_messages and final_messages[0]["role"] == "model":
            final_messages.insert(0, {"role": "user", "parts": [{"text": "Continue analysis."}]})
            
        return system_msg, final_messages

    def _parse_react_text(self, text: str) -> List[Dict[str, Any]]:
        """Extract tool calls from ReAct-style text"""
        tool_calls = []
        pattern = r"Action:\s*Called\s*tool\s*'([^']+)'\s*with\s*arguments\s*(\{.*?\})\s*\.?"
        matches = re.finditer(pattern, text, re.DOTALL)
        for match in matches:
            name = match.group(1)
            args_str = match.group(2)
            try:
                json.loads(args_str)
                tool_calls.append({
                    "id": f"call_parsed_{os.urandom(4).hex()}",
                    "type": "function",
                    "function": {"name": name, "arguments": args_str}
                })
            except Exception:
                continue
        return tool_calls

    async def chat(self, model_id: str, messages: List[ChatMessage], **kwargs) -> ChatResponse:
        client = self._get_client()
        system_msg, gemini_messages = self._convert_messages(messages)
        
        config = {
            "temperature": kwargs.get("temperature", 0.7),
            "max_output_tokens": 8192
        }
        if system_msg:
            config["system_instruction"] = system_msg
            
        tools = self._convert_tools(kwargs.get("tools", []))
        if tools:
            config["tools"] = tools

        response = await client.aio.models.generate_content(
            model=model_id, 
            contents=gemini_messages, 
            config=config
        )
        
        content = ""
        reasoning = ""
        tool_calls = []
        finish_reason = "stop"
        
        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        content += part.text
                    elif hasattr(part, "thought") and part.thought:
                        reasoning += part.thought
                    elif hasattr(part, "function_call") and part.function_call:
                        call = part.function_call
                        tool_calls.append({
                            "id": f"call_{os.urandom(4).hex()}", 
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.args)}
                        })
            
            if content and "Action: Called tool" in content:
                parsed = self._parse_react_text(content)
                if parsed:
                    tool_calls.extend(parsed)
            
            if tool_calls:
                finish_reason = "tool_calls"
        
        if not content and not tool_calls:
            try:
                content = response.text or ""
            except Exception:
                content = ""
        
        # Real usage metadata
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if response.usage_metadata:
            usage["prompt_tokens"] = response.usage_metadata.prompt_token_count or 0
            usage["completion_tokens"] = response.usage_metadata.candidates_token_count or 0
            usage["total_tokens"] = response.usage_metadata.total_token_count or 0

        return ChatResponse(
            id="gemini-" + model_id, 
            model=model_id, 
            content=content, 
            finish_reason=finish_reason, 
            usage=usage, 
            tool_calls=tool_calls if tool_calls else None, 
            reasoning=reasoning if reasoning else None
        )

    async def chat_stream(self, model_id: str, messages: List[ChatMessage], **kwargs) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        system_msg, gemini_messages = self._convert_messages(messages)
        
        config = {
            "temperature": kwargs.get("temperature", 0.7), 
            "max_output_tokens": 8192
        }
        if system_msg:
            config["system_instruction"] = system_msg
            
        tools = self._convert_tools(kwargs.get("tools", []))
        if tools:
            config["tools"] = tools
        
        response = await client.aio.models.generate_content_stream(
            model=model_id, 
            contents=gemini_messages, 
            config=config
        )
        
        full_content = ""
        has_calls = False
        async for chunk in response:
            delta = ""
            reasoning = None
            tool_calls = None
            
            if chunk.candidates:
                candidate = chunk.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text: 
                            delta += part.text
                            full_content += part.text
                        elif hasattr(part, "thought") and part.thought:
                            reasoning = part.thought
                        elif hasattr(part, "function_call") and part.function_call:
                            has_calls = True
                            call = part.function_call
                            if tool_calls is None:
                                tool_calls = []
                            tool_calls.append({
                                "index": 0, 
                                "id": f"call_{os.urandom(4).hex()}", 
                                "type": "function",
                                "function": {"name": call.name, "arguments": json.dumps(call.args)}
                            })
            
            usage = None
            if chunk.usage_metadata:
                usage = {
                    "prompt_tokens": chunk.usage_metadata.prompt_token_count or 0,
                    "completion_tokens": chunk.usage_metadata.candidates_token_count or 0,
                    "total_tokens": chunk.usage_metadata.total_token_count or 0,
                }

            if delta or reasoning or tool_calls:
                yield StreamChunk(
                    delta=delta, 
                    reasoning=reasoning, 
                    tool_calls=tool_calls,
                    event_type="reasoning" if reasoning and not delta else "text",
                    usage=usage, 
                    finish_reason=None
                )
            elif usage:
                yield StreamChunk(delta="", usage=usage, finish_reason=None)
        
        if "Action: Called tool" in full_content:
            parsed = self._parse_react_text(full_content)
            if parsed:
                has_calls = True
                yield StreamChunk(delta="", tool_calls=parsed, finish_reason=None)
        
        yield StreamChunk(delta="", finish_reason="tool-calls" if has_calls else "stop")

    def _convert_tools(self, flocks_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Clean and convert tools for Gemini"""
        if not flocks_tools:
            return []
            
        def clean_schema(obj):
            if not isinstance(obj, dict):
                if isinstance(obj, list):
                    return [clean_schema(item) for item in obj]
                return obj
            res = {}
            for k, v in obj.items():
                if k.lower() in ["additional_properties", "additionalproperties"]:
                    continue
                if k == "anyof":
                    if isinstance(v, list) and len(v) > 0:
                        return clean_schema(v[0])
                    continue
                res[k] = clean_schema(v)
            return res

        function_declarations = []
        for tool in flocks_tools:
            if "function" not in tool:
                continue
            func = tool["function"]
            params = clean_schema(func.get("parameters", {"type": "object", "properties": {}}))
            function_declarations.append({
                "name": func["name"], 
                "description": func.get("description", ""), 
                "parameters": params
            })
        return [{"function_declarations": function_declarations}]

    async def embed(self, text: str, model: Optional[str] = None, **kwargs) -> List[float]:
        """Generate embedding with error handling"""
        client = self._get_client()
        model = model or "models/text-embedding-004"
        try:
            result = await client.aio.models.embed_content(model=model, content=text, **kwargs)
            return result.embeddings[0].values
        except Exception as e:
            log.error("provider.google.embed_failed", {"error": str(e), "model": model})
            raise
    
    async def embed_batch(self, texts: List[str], model: Optional[str] = None, batch_size: Optional[int] = 100, **kwargs) -> List[List[float]]:
        """Generate batch embeddings with error handling"""
        client = self._get_client()
        model = model or "models/text-embedding-004"
        all_embeddings = []
        try:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                result = await client.aio.models.embed_content(model=model, content=batch, **kwargs)
                for embedding in result.embeddings:
                    all_embeddings.append(embedding.values)
            return all_embeddings
        except Exception as e:
            log.error("provider.google.embed_batch_failed", {"error": str(e), "model": model})
            raise
    
    def get_embedding_models(self) -> List[str]:
        return ["models/text-embedding-004", "models/embedding-001"]
