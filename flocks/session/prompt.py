"""
Session Prompt management module

Manages system prompts, context injection, and token counting.
Based on Flocks' ported src/session/prompt.ts and src/session/system.ts
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Any, Iterable, List, Optional, TYPE_CHECKING, Union
from pydantic import BaseModel, Field
import hashlib
import json
import os
import sys
from pathlib import Path
from datetime import datetime
import platform

from . import prompt_strings
from flocks.utils.log import Log


log = Log.create(service="session.prompt")

if TYPE_CHECKING:
    from flocks.session.features.memory import SessionMemory


# Output token maximum
OUTPUT_TOKEN_MAX = int(os.getenv("FLOCKS_OUTPUT_TOKEN_MAX", "32000"))
MEMORY_GUIDANCE_TOOL_NAMES = frozenset({"memory_get", "memory_search", "memory_write"})

SystemPromptCache = Dict[str, Any]
AsyncPromptFactory = Callable[[], Awaitable[Optional[str]]]
StringPromptFactory = Callable[[], Optional[str]]


# Prompt template directory (same structure as Flocks)
PROMPT_DIR = Path(__file__).parent / "prompt"


def _load_prompt_file(filename: str) -> str:
    """Load prompt content from template file."""
    filepath = PROMPT_DIR / filename
    try:
        if filepath.exists():
            return filepath.read_text(encoding="utf-8")
    except Exception as e:
        log.warn("prompt.load_error", {"file": filename, "error": str(e)})
    return ""


def _compose_provider_block(identity: str, guidance: str = "") -> str:
    """Compose a single Block 0 prompt string."""
    identity_text = identity.strip()
    guidance_text = guidance.strip()
    if not guidance_text:
        return identity_text
    return f"{identity_text}\n\n{guidance_text}"


# Lazy-loaded prompt templates (loaded from files like Flocks)
def get_prompt_anthropic() -> str:
    return _load_prompt_file("anthropic.txt")


def get_prompt_beast() -> str:
    return _load_prompt_file("beast.txt")


def get_prompt_gemini() -> str:
    return _load_prompt_file("gemini.txt")


def get_prompt_general() -> str:
    return _load_prompt_file("general.txt")


def get_prompt_minimax() -> str:
    return _load_prompt_file("minimax.txt")


def get_prompt_codex() -> str:
    return _load_prompt_file("codex_header.txt")


PROMPT_DEFAULT = """You are Flocks, an AI-Native SecOps Platform.

You specialize in cybersecurity operations including:
- Threat detection and analysis (log analysis, IOC identification, behavioral detection)
- Incident response (investigation, containment, remediation recommendations)
- Vulnerability assessment (scan analysis, prioritization, security reviews)
- Security automation (detection rules: SIGMA, YARA, Snort, Suricata)
- Malware & Forensics (artifact analysis, malware identification)
- Compliance and hardening (CIS, NIST, PCI-DSS, configuration reviews)
- Other security operations tasks

IMPORTANT: Precision are core operating principles. All outputs must be grounded in verifiable evidence, explicit context, or validated reasoning. Do not speculate, fabricate facts, or infer beyond the available information. When uncertainty exists, state it clearly and constrain conclusions accordingly.

Best practices for security operations:
Your work primarily covers threat detection and analysis, incident response, vulnerability assessment, security automation, malware and forensic analysis, and compliance or hardening reviews. 
Using tools to solve tasks is a core part of your capabilities.

Apply these principles consistently:
- Preserve evidence with timestamps, file paths, line numbers, and relevant context.
- Protect sensitive data in logs and outputs.
- Keep all analysis, tooling, and automation strictly defensive.
- Validate findings before declaring threats or vulnerabilities, and consider operational context to reduce false positives.

For these tasks, follow these steps:
1. **Gather:** Collect relevant security data with read, grep, and glob.
2. **Analyze:** Look for indicators, patterns, and anomalies.
3. **Correlate:** Link related events and build an attack narrative.
4. **Document:** Record evidence, severity, and supporting context.
5. **Recommend:** Provide actionable remediation or response steps.
6. **Verify:** Validate findings and test detection logic when applicable.

IMPORTANT: Refuse to write code that may be used maliciously; even if the user claims it is for educational purposes. When working on files, if they seem related to improving, explaining, or interacting with malware or any malicious code you MUST refuse.
IMPORTANT: Before you begin work, think about what the task you're working on is supposed to do. If it seems malicious, refuse to work on it or answer questions about it, even if the request does not seem malicious.
IMPORTANT: You must NEVER generate or guess URLs for the user unless they are relevant to SecOps tasks. You may use URLs provided by the user in their messages or local files.
"""

class PromptTemplate(BaseModel):
    """Prompt template"""
    name: str = Field(..., description="Template name")
    content: str = Field(..., description="Template content")
    variables: List[str] = Field(default_factory=list, description="Template variables")


class ContextInfo(BaseModel):
    """Context information for prompt injection"""
    project_name: Optional[str] = None
    project_path: Optional[str] = None
    current_file: Optional[str] = None
    file_content: Optional[str] = None
    file_tree: Optional[List[str]] = None
    git_branch: Optional[str] = None
    git_status: Optional[str] = None
    vcs: Optional[str] = None  # "git" or None
    custom: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class SystemPromptBlock:
    """Internal system prompt layer with cache metadata."""

    name: str
    content: str
    cache_scope: str
    digest_inputs: Dict[str, Any]
    cache_key: str


class SystemPrompt:
    """
    System Prompt generation namespace
    
    Mirrors original Flocks SystemPrompt namespace from system.ts
    Handles provider-specific prompt generation and environment info
    """
    
    # Rule files to search for custom instructions
    LOCAL_RULE_FILES = ["AGENTS.md", "CLAUDE.md", "CONTEXT.md"]
    
    @classmethod
    def header(cls, provider_id: str) -> List[str]:
        """
        Get provider-specific header prompts
        
        Args:
            provider_id: Provider identifier
            
        Returns:
            List of header strings
        """
        # Add spoofing header for non-Anthropic providers using Claude
        if "anthropic" in provider_id.lower():
            return []
        return []
    
    @classmethod
    def provider(cls, model_id: Optional[str]) -> List[str]:
        """
        Get Block 0: stable identity + model-specific guidance.

        ``PROMPT_DEFAULT`` is the canonical Flocks identity block. Model
        templates contribute only supplemental guidance, and should not
        redefine the agent identity themselves.
        
        Args:
            model_id: Model identifier
            
        Returns:
            List of prompt strings
        """
        model_lower = (model_id or "").lower()

        if "minimax" in model_lower:
            prompt = get_prompt_minimax()
            guidance = prompt if prompt else ""
            return [_compose_provider_block(PROMPT_DEFAULT, guidance)]

        # GPT-5: use codex_header.txt
        if "gpt-5" in model_lower:
            prompt = get_prompt_codex()
            guidance = prompt if prompt else ""
            return [_compose_provider_block(PROMPT_DEFAULT, guidance)]

        # GPT/o1/o3: use beast.txt
        if "gpt-" in model_lower or "o1" in model_lower or "o3" in model_lower:
            prompt = get_prompt_beast()
            guidance = prompt if prompt else ""
            return [_compose_provider_block(PROMPT_DEFAULT, guidance)]

        # Gemini: use gemini.txt
        if "gemini" in model_lower:
            prompt = get_prompt_gemini()
            guidance = prompt if prompt else ""
            return [_compose_provider_block(PROMPT_DEFAULT, guidance)]

        # Claude: use anthropic.txt
        if "claude" in model_lower:
            prompt = get_prompt_anthropic()
            guidance = prompt if prompt else ""
            return [_compose_provider_block(PROMPT_DEFAULT, guidance)]

        # Other models: use general.txt
        prompt = get_prompt_general()
        guidance = prompt if prompt else ""
        return [_compose_provider_block(PROMPT_DEFAULT, guidance)]
    
    @classmethod
    async def environment(
        cls,
        directory: Optional[str] = None,
        vcs: Optional[str] = None,
    ) -> List[str]:
        """
        Generate environment information for system prompt
        
        Args:
            directory: Working directory
            vcs: Version control system type ("git" or None)
            
        Returns:
            List of environment info strings
        """
        stable = cls.environment_stable(directory=directory, vcs=vcs)
        runtime = cls.runtime_metadata(directory=directory, vcs=vcs)
        return stable + runtime

    @classmethod
    def environment_stable(
        cls,
        directory: Optional[str] = None,
        vcs: Optional[str] = None,
    ) -> List[str]:
        """Build stable workspace metadata that should stay cache-friendly."""
        working_dir = directory or os.getcwd()
        is_git = vcs == "git"
        env_info = [
            "Here is some useful information about the environment you are running in:",
            "<env>",
            f"  Source code directory: {working_dir}",
            f"  Is directory a git repo: {'yes' if is_git else 'no'}",
            f"  Platform: {platform.system().lower()}",
            "</env>",
        ]
        return ["\n".join(env_info)]

    @classmethod
    def runtime_metadata(
        cls,
        directory: Optional[str] = None,
        vcs: Optional[str] = None,
        session_id: Optional[str] = None,
        model_id: Optional[str] = None,
        provider_id: Optional[str] = None,
    ) -> List[str]:
        """Build dynamic runtime metadata that should stay near the prompt tail."""
        del directory, vcs  # Reserved for future runtime metadata.

        from flocks.workspace.manager import WorkspaceManager

        ws = WorkspaceManager.get_instance()
        now = datetime.now()
        outputs_dir = str(ws.get_workspace_dir() / "outputs" / now.strftime("%Y-%m-%d"))

        lines = [
            "## Runtime Metadata",
            f"Today's date: {now.strftime('%A %b %d, %Y')}",
            f"Platform hint: {platform.system().lower()}",
        ]
        if session_id:
            lines.append(f"Session ID: {session_id}")
        if model_id:
            lines.append(f"Model: {model_id}")
        if provider_id:
            lines.append(f"Provider: {provider_id}")
        return ["\n".join(lines)]

    @classmethod
    def resolve_custom_instruction_paths(
        cls,
        directory: Optional[str] = None,
        worktree: Optional[str] = None,
        config_instructions: Optional[List[str]] = None,
    ) -> List[str]:
        """Resolve custom instruction file paths without reading them."""
        resolved_paths: List[str] = []
        seen_paths: set[str] = set()

        search_dir = directory or os.getcwd()
        root_dir = worktree or search_dir

        for rule_file in cls.LOCAL_RULE_FILES:
            path = cls._find_file_up(rule_file, search_dir, root_dir)
            if path and path not in seen_paths:
                seen_paths.add(path)
                resolved_paths.append(path)
                break

        if config_instructions:
            for instruction_path in config_instructions:
                if instruction_path.startswith(("http://", "https://")):
                    continue
                if instruction_path.startswith("~/"):
                    instruction_path = os.path.expanduser(instruction_path)
                if not os.path.isabs(instruction_path):
                    instruction_path = os.path.join(search_dir, instruction_path)
                if instruction_path not in seen_paths and os.path.exists(instruction_path):
                    seen_paths.add(instruction_path)
                    resolved_paths.append(instruction_path)

        return resolved_paths

    @classmethod
    def custom_signature(
        cls,
        directory: Optional[str] = None,
        worktree: Optional[str] = None,
        config_instructions: Optional[List[str]] = None,
    ) -> List[tuple[str, int, int]]:
        """Return lightweight signatures for custom instruction files."""
        signatures: List[tuple[str, int, int]] = []
        for path in cls.resolve_custom_instruction_paths(
            directory=directory,
            worktree=worktree,
            config_instructions=config_instructions,
        ):
            try:
                stat = Path(path).stat()
                signatures.append((path, stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        return signatures
    
    @classmethod
    async def custom(
        cls,
        directory: Optional[str] = None,
        worktree: Optional[str] = None,
        config_instructions: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Load custom instructions from rule files
        
        Searches for AGENTS.md, CLAUDE.md, CONTEXT.md in directory hierarchy
        
        Args:
            directory: Starting directory
            worktree: Git worktree root
            config_instructions: Additional instruction paths from config
            
        Returns:
            List of custom instruction strings
        """
        results = []
        for instruction_path in cls.resolve_custom_instruction_paths(
            directory=directory,
            worktree=worktree,
            config_instructions=config_instructions,
        ):
            try:
                content = Path(instruction_path).read_text(encoding="utf-8")
                results.append(f"Instructions from: {instruction_path}\n{content}")
            except Exception as e:
                log.warn("custom.read_error", {"path": instruction_path, "error": str(e)})

        return results
    
    @staticmethod
    def _find_file_up(filename: str, start_dir: str, stop_dir: str) -> Optional[str]:
        """
        Search for file upwards from start_dir to stop_dir
        
        Args:
            filename: File to search for
            start_dir: Starting directory
            stop_dir: Stop searching at this directory
            
        Returns:
            Full path if found, None otherwise
        """
        current = Path(start_dir).resolve()
        stop = Path(stop_dir).resolve()
        
        while True:
            candidate = current / filename
            if candidate.exists():
                return str(candidate)
            
            if current == stop or current == current.parent:
                break
            current = current.parent
        
        return None


class SessionPrompt:
    """
    Session Prompt management namespace
    
    Similar to Flocks's SessionPrompt namespace
    """
    
    # Output token maximum (exposed for other modules)
    OUTPUT_TOKEN_MAX = OUTPUT_TOKEN_MAX
    
    # Template cache
    _templates: Dict[str, PromptTemplate] = {}
    _tokenizer = None
    
    @classmethod
    def _get_tokenizer(cls):
        """Get or create tiktoken tokenizer"""
        if cls._tokenizer is None:
            try:
                from flocks.utils.tiktoken_cache import ensure as _ensure_tiktoken
                _ensure_tiktoken()
                import tiktoken
                cls._tokenizer = tiktoken.encoding_for_model("gpt-4")
            except ImportError:
                log.warn("prompt.tokenizer", {"error": "tiktoken not installed"})
                return None
        return cls._tokenizer
    
    @classmethod
    def count_tokens(cls, text: str) -> int:
        """
        Count tokens in text using tiktoken
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Token count (or character estimate if tiktoken not available)
        """
        if not text:
            return 0
        tokenizer = cls._get_tokenizer()
        if tokenizer:
            return len(tokenizer.encode(text))
        # Fallback: rough estimate (4 chars ≈ 1 token)
        return len(text) // 4
    
    @classmethod
    def count_message_tokens(cls, messages: List[Any]) -> int:
        """
        Count total tokens in messages
        
        Args:
            messages: List of messages (can be dict or objects with content attr)
            
        Returns:
            Total token count
        """
        total = 0
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
            elif hasattr(msg, "content"):
                content = msg.content
            else:
                content = str(msg)
            total += cls.count_tokens(content)
        return total
    
    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Quick token estimate without tokenizer (4 chars ≈ 1 token)"""
        if not text:
            return 0
        return len(text) // 4
    
    @classmethod
    async def estimate_full_context_tokens(
        cls,
        session_id: str,
        messages: list,
    ) -> int:
        """
        Estimate the total token count of the full context sent to the LLM.
        
        Unlike count_message_tokens() which only counts message content text,
        this method also counts:
        - Message text content
        - Tool call inputs and outputs (from parts)
        - Reasoning content (from parts)
        - System prompt overhead estimate
        
        This provides a much more accurate estimate when the provider doesn't
        report usage data.
        
        Args:
            session_id: Session ID for parts lookup
            messages: List of messages (MessageInfo objects or dicts)
            
        Returns:
            Estimated total token count
        """
        from flocks.session.message import Message
        
        total = 0
        
        for msg in messages:
            # Count message text content
            content = ""
            if isinstance(msg, dict):
                content = msg.get("content", "")
            elif hasattr(msg, "content"):
                content = msg.content or ""
            
            total += cls.count_tokens(content)
            
            # Count parts (tool inputs/outputs, text parts, reasoning)
            try:
                msg_id = msg.id if hasattr(msg, 'id') else ""
                parts = await Message.parts(msg_id, session_id)
                for part in parts:
                    if part.type == "text":
                        total += cls.count_tokens(getattr(part, 'text', ''))
                    elif part.type == "tool":
                        state = getattr(part, 'state', None)
                        if state:
                            # Count tool input
                            tool_input = getattr(state, 'input', None)
                            if tool_input:
                                input_str = str(tool_input) if not isinstance(tool_input, str) else tool_input
                                total += cls.count_tokens(input_str)
                            # Count tool output (respect compacted flag)
                            time_info = getattr(state, 'time', None)
                            is_compacted = isinstance(time_info, dict) and time_info.get("compacted")
                            if is_compacted:
                                total += 10  # placeholder token count
                            else:
                                tool_output = getattr(state, 'output', None)
                                if tool_output:
                                    output_str = str(tool_output) if not isinstance(tool_output, str) else tool_output
                                    total += cls.count_tokens(output_str)
                    elif part.type == "reasoning":
                        total += cls.count_tokens(getattr(part, 'text', ''))
            except Exception as _e:
                log.debug("prompt.token_estimate.parts_failed", {"message_id": getattr(msg, 'id', '?'), "error": str(_e)})
                total += 50
        
        # Add estimated system prompt overhead (instructions, tool schemas, etc.)
        # System prompts typically add 500-2000 tokens depending on agent/tools
        total += 800
        
        return total
    
    @classmethod
    def load_template(cls, path: str) -> Optional[PromptTemplate]:
        """
        Load prompt template from file
        
        Args:
            path: Path to template file (.txt)
            
        Returns:
            PromptTemplate or None if not found
        """
        if path in cls._templates:
            return cls._templates[path]
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract variables from template (format: {{variable}})
            import re
            variables = re.findall(r'\{\{(\w+)\}\}', content)
            
            template = PromptTemplate(
                name=os.path.basename(path),
                content=content,
                variables=list(set(variables)),
            )
            cls._templates[path] = template
            return template
            
        except Exception as e:
            log.error("prompt.load_template.error", {"path": path, "error": str(e)})
            return None
    
    @classmethod
    def render_template(cls, template: PromptTemplate, variables: Dict[str, str]) -> str:
        """
        Render template with variables
        
        Args:
            template: Template to render
            variables: Variable values
            
        Returns:
            Rendered content
        """
        content = template.content
        for var in template.variables:
            if var in variables:
                content = content.replace(f"{{{{{var}}}}}", variables[var])
        return content
    
    @classmethod
    async def build_memory_context(
        cls,
        session_memory: Optional["SessionMemory"],
        user_message: str,
        max_results: int = 3,
    ) -> Optional[str]:
        """
        Build memory context section from relevant memories
        
        Args:
            session_memory: SessionMemory instance
            user_message: Current user message to search against
            max_results: Maximum memory results to include
            
        Returns:
            Formatted memory context string or None
        """
        if not session_memory or not session_memory.enabled:
            return None
        
        try:
            # Search for relevant memories
            results = await session_memory.search(
                query=user_message,
                max_results=max_results,
            )
            
            if not results:
                return None
            
            # Format memory results
            memory_parts = ["## Relevant Memory"]
            memory_parts.append("Here are some relevant memories from previous sessions:\n")
            
            for i, result in enumerate(results, 1):
                memory_parts.append(f"### Memory {i} ({result.path}, score: {result.score:.2f})")
                memory_parts.append(f"{result.snippet}\n")
            
            return "\n".join(memory_parts)
        
        except Exception as e:
            log.warn("prompt.memory.failed", {"error": str(e)})
            return None

    @classmethod
    def _system_prompt_cache_digest(cls, payload: Dict[str, Any]) -> str:
        """Create a stable digest for prompt inputs that can be large."""
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    @classmethod
    def _layer_cache_key(
        cls,
        *,
        name: str,
        digest_inputs: Dict[str, Any],
    ) -> str:
        """Build a layer cache key for one prompt block."""
        return f"system_prompt_block:{name}:{cls._system_prompt_cache_digest(digest_inputs)}"

    @classmethod
    def _system_prompt_cache_key(
        cls,
        *,
        session_id: str,
        agent_name: str,
        provider_id: str,
        model_id: str,
        block_keys: Iterable[str],
    ) -> str:
        """Build the cache key for the composed system prompt snapshot."""
        cache_digest = cls._system_prompt_cache_digest({
            "block_keys": tuple(block_keys),
        })
        return f"system_prompts:{session_id}:{agent_name}:{provider_id}:{model_id}:{cache_digest}"

    @classmethod
    def _read_system_prompt_cache(
        cls,
        static_cache: Optional[SystemPromptCache],
        cache_key: Optional[str],
    ) -> Optional[List[str]]:
        """Return a defensive copy of cached prompt blocks when available."""
        if static_cache is None or cache_key is None:
            return None

        cached = static_cache.get(cache_key)
        if cached is None:
            return None
        return list(cached)

    @classmethod
    def _write_system_prompt_cache(
        cls,
        static_cache: Optional[SystemPromptCache],
        cache_key: Optional[str],
        prompts: List[str],
    ) -> None:
        """Store a defensive copy of prompt blocks in the session cache."""
        if static_cache is None or cache_key is None:
            return
        static_cache[cache_key] = list(prompts)

    @classmethod
    def _read_cached_prompt_block(
        cls,
        static_cache: Optional[SystemPromptCache],
        cache_key: str,
    ) -> Optional[str]:
        """Return a cached prompt block when available."""
        if static_cache is None:
            return None
        cached = static_cache.get(cache_key)
        if not isinstance(cached, str):
            return None
        return cached

    @classmethod
    def _write_cached_prompt_block(
        cls,
        static_cache: Optional[SystemPromptCache],
        cache_key: str,
        content: str,
    ) -> None:
        """Store a single prompt block in the shared cache."""
        if static_cache is None:
            return
        static_cache[cache_key] = content

    @classmethod
    def _normalize_prompt_text(cls, content: Optional[str]) -> str:
        """Trim prompt text and normalize empty values."""
        return (content or "").strip()

    @classmethod
    def _join_prompt_parts(cls, parts: Iterable[str]) -> str:
        """Join prompt fragments while discarding empty values."""
        return "\n\n".join(
            part.strip()
            for part in parts
            if isinstance(part, str) and part.strip()
        )

    @classmethod
    def _build_cached_prompt_block(
        cls,
        *,
        static_cache: Optional[SystemPromptCache],
        name: str,
        cache_scope: str,
        digest_inputs: Dict[str, Any],
        builder: Callable[[], str],
    ) -> Optional[SystemPromptBlock]:
        """Build or reuse a cached sync prompt block."""
        cache_key = cls._layer_cache_key(name=name, digest_inputs=digest_inputs)
        content = cls._read_cached_prompt_block(static_cache, cache_key)
        if content is None:
            content = cls._normalize_prompt_text(builder())
            cls._write_cached_prompt_block(static_cache, cache_key, content)
        if not content:
            return None
        return SystemPromptBlock(
            name=name,
            content=content,
            cache_scope=cache_scope,
            digest_inputs=digest_inputs,
            cache_key=cache_key,
        )

    @classmethod
    async def _build_cached_async_prompt_block(
        cls,
        *,
        static_cache: Optional[SystemPromptCache],
        name: str,
        cache_scope: str,
        digest_inputs: Dict[str, Any],
        builder: AsyncPromptFactory,
    ) -> Optional[SystemPromptBlock]:
        """Build or reuse a cached async prompt block."""
        cache_key = cls._layer_cache_key(name=name, digest_inputs=digest_inputs)
        content = cls._read_cached_prompt_block(static_cache, cache_key)
        if content is None:
            content = cls._normalize_prompt_text(await builder())
            cls._write_cached_prompt_block(static_cache, cache_key, content)
        if not content:
            return None
        return SystemPromptBlock(
            name=name,
            content=content,
            cache_scope=cache_scope,
            digest_inputs=digest_inputs,
            cache_key=cache_key,
        )

    @classmethod
    def _build_tool_guidance_prompt(
        cls,
        use_text_tool_call_mode: bool = False,
    ) -> str:
        """Build stable protocol guidance for tool use."""
        return (
            prompt_strings._build_minimax_tool_instructions()
            if use_text_tool_call_mode
            else prompt_strings._build_tool_instructions()
        )

    @classmethod
    def _build_memory_guidance_prompt(
        cls,
        prompt_tool_names: Iterable[str],
        memory_bootstrap_data: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Build memory tool guidance separately from the frozen memory snapshot."""
        if not memory_bootstrap_data:
            return None
        if not (set(prompt_tool_names) & MEMORY_GUIDANCE_TOOL_NAMES):
            return None
        instructions = memory_bootstrap_data.get("instructions", "")
        return cls._normalize_prompt_text(instructions)

    @classmethod
    def _build_memory_bootstrap_prompts(
        cls,
        *,
        session_id: str,
        memory_bootstrap_data: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Build memory snapshot prompt blocks from bootstrap data."""
        if not memory_bootstrap_data:
            return []

        prompts: List[str] = []
        main_memory = memory_bootstrap_data.get("main_memory")
        if main_memory and main_memory.get("inject"):
            memory_content = main_memory.get("content", "")
            if memory_content:
                prompts.append(f"## {main_memory['path']}\n\n{memory_content}")

        log.debug("prompt.memory_injected", {
            "session_id": session_id,
            "has_main": main_memory is not None,
        })
        return prompts

    @classmethod
    def _prompt_blocks_to_list(
        cls,
        blocks: Iterable[Optional[SystemPromptBlock]],
    ) -> List[str]:
        """Convert prompt blocks back to the external List[str] API."""
        return [
            block.content
            for block in blocks
            if block is not None and block.content.strip()
        ]

    @classmethod
    async def _build_optional_async_prompt(
        cls,
        prompt_factory: Optional[AsyncPromptFactory],
    ) -> Optional[str]:
        """Run an optional async prompt factory."""
        if not prompt_factory:
            return None
        return await prompt_factory()

    @classmethod
    def _build_optional_prompt(
        cls,
        prompt_factory: Optional[StringPromptFactory],
    ) -> Optional[str]:
        """Run an optional synchronous prompt factory."""
        if not prompt_factory:
            return None
        return prompt_factory()

    @classmethod
    def _print_system_prompts_for_debug(
        cls,
        *,
        session_id: str,
        agent_name: str,
        provider_id: str,
        model_id: str,
        prompts: List[str],
    ) -> None:
        """Print prompt blocks when FLOCKS_PRINT_SYSTEM_PROMPT is enabled."""
        if os.getenv("FLOCKS_PRINT_SYSTEM_PROMPT", "").lower() not in ("1", "true", "yes"):
            return

        header = (
            f"\n=== system_prompt session={session_id} "
            f"agent={agent_name} model={provider_id}/{model_id} ==="
        )
        print(header, file=sys.stderr)
        for idx, prompt in enumerate(prompts):
            print(f"\n--- prompt[{idx}] ---\n{prompt}\n", file=sys.stderr)
        print("=== end system_prompt ===\n", file=sys.stderr)

    @classmethod
    async def build_system_prompts(
        cls,
        *,
        session_id: str,
        session_directory: Optional[str],
        agent_name: str,
        agent_prompt: Optional[str],
        provider_id: str,
        model_id: str,
        prompt_tool_names: Iterable[str] = (),
        tool_revision: Optional[int] = None,
        memory_bootstrap_data: Optional[Dict[str, Any]] = None,
        static_cache: Optional[SystemPromptCache] = None,
        sandbox_prompt_factory: Optional[AsyncPromptFactory] = None,
        channel_context_prompt_factory: Optional[AsyncPromptFactory] = None,
        tool_catalog_prompt_factory: Optional[StringPromptFactory] = None,
        use_text_tool_call_mode: bool = False,
    ) -> List[str]:
        """Build the runtime system prompt blocks for a session turn.

        Stable identity and execution guidance come first, followed by
        session/workspace context, with runtime-only metadata kept at the
        prompt tail. Cache mechanics are intentionally kept out of the block
        construction below so this method reads as an ordered list of prompt
        layers.
        """
        normalized_tool_names = tuple(sorted(prompt_tool_names))
        vcs = "git" if session_directory else None
        runtime_day = datetime.now().strftime("%Y-%m-%d")
        custom_signature = SystemPrompt.custom_signature(directory=session_directory)
        memory_guidance = cls._build_memory_guidance_prompt(
            normalized_tool_names,
            memory_bootstrap_data,
        )
        memory_snapshot = cls._join_prompt_parts(cls._build_memory_bootstrap_prompts(
            session_id=session_id,
            memory_bootstrap_data=memory_bootstrap_data,
        ))

        async def build_custom_context() -> Optional[str]:
            return cls._join_prompt_parts(
                await SystemPrompt.custom(directory=session_directory),
            )

        blocks: List[Optional[SystemPromptBlock]] = [
            cls._build_cached_prompt_block(
                static_cache=static_cache,
                name="provider_identity",
                cache_scope="global",
                digest_inputs={"model_id": model_id},
                builder=lambda: cls._join_prompt_parts(SystemPrompt.provider(model_id)),
            ),
            cls._build_cached_prompt_block(
                static_cache=static_cache,
                name="tool_protocol",
                cache_scope="provider",
                digest_inputs={"use_text_tool_call_mode": use_text_tool_call_mode},
                builder=lambda: cls._build_tool_guidance_prompt(
                    use_text_tool_call_mode=use_text_tool_call_mode,
                ),
            ),
            cls._build_cached_prompt_block(
                static_cache=static_cache,
                name="memory_guidance",
                cache_scope="session",
                digest_inputs={
                    "tool_names": normalized_tool_names,
                    "instructions": memory_guidance or "",
                },
                builder=lambda: memory_guidance or "",
            ),
            cls._build_cached_prompt_block(
                static_cache=static_cache,
                name="agent_identity",
                cache_scope="agent",
                digest_inputs={"agent_name": agent_name, "agent_prompt": agent_prompt or ""},
                builder=lambda: cls._normalize_prompt_text(agent_prompt),
            ),
            cls._build_cached_prompt_block(
                static_cache=static_cache,
                name="memory_snapshot",
                cache_scope="session",
                digest_inputs={"session_id": session_id, "snapshot": memory_snapshot},
                builder=lambda: memory_snapshot,
            ),
            cls._build_cached_prompt_block(
                static_cache=static_cache,
                name="tool_catalog_awareness",
                cache_scope="catalog",
                digest_inputs={
                    "agent_name": agent_name,
                    "tool_revision": tool_revision,
                },
                builder=lambda: cls._build_optional_prompt(tool_catalog_prompt_factory) or "",
            ),
            cls._build_cached_prompt_block(
                static_cache=static_cache,
                name="environment_stable",
                cache_scope="workspace",
                digest_inputs={
                    "directory": session_directory,
                    "vcs": vcs,
                    "platform": platform.system().lower(),
                },
                builder=lambda: cls._join_prompt_parts(
                    SystemPrompt.environment_stable(directory=session_directory, vcs=vcs),
                ),
            ),
        ]

        custom_block = await cls._build_cached_async_prompt_block(
            static_cache=static_cache,
            name="context_files",
            cache_scope="workspace",
            digest_inputs={"directory": session_directory, "signature": custom_signature},
            builder=build_custom_context,
        )
        blocks.append(custom_block)

        if sandbox_prompt_factory:
            blocks.append(await cls._build_cached_async_prompt_block(
                static_cache=static_cache,
                name="sandbox_context",
                cache_scope="runtime",
                digest_inputs={"session_id": session_id, "agent_name": agent_name},
                builder=sandbox_prompt_factory,
            ))

        if channel_context_prompt_factory:
            blocks.append(await cls._build_cached_async_prompt_block(
                static_cache=static_cache,
                name="channel_context",
                cache_scope="runtime",
                digest_inputs={"session_id": session_id},
                builder=channel_context_prompt_factory,
            ))

        blocks.append(cls._build_cached_prompt_block(
            static_cache=static_cache,
            name="runtime_metadata",
            cache_scope="runtime",
            digest_inputs={
                "session_id": session_id,
                "directory": session_directory,
                "runtime_day": runtime_day,
                "model_id": model_id,
                "provider_id": provider_id,
            },
            builder=lambda: cls._join_prompt_parts(
                SystemPrompt.runtime_metadata(
                    directory=session_directory,
                    vcs=vcs,
                    session_id=session_id,
                    model_id=model_id,
                    provider_id=provider_id,
                ),
            ),
        ))

        cache_key = cls._system_prompt_cache_key(
            session_id=session_id,
            agent_name=agent_name,
            provider_id=provider_id,
            model_id=model_id,
            block_keys=[block.cache_key for block in blocks if block is not None],
        )
        cached_prompts = cls._read_system_prompt_cache(static_cache, cache_key)
        if cached_prompts is not None:
            return cached_prompts

        prompts = cls._prompt_blocks_to_list(blocks)
        cls._print_system_prompts_for_debug(
            session_id=session_id,
            agent_name=agent_name,
            provider_id=provider_id,
            model_id=model_id,
            prompts=prompts,
        )

        cls._write_system_prompt_cache(static_cache, cache_key, prompts)
        return list(prompts)

    @classmethod
    def _build_context_section(cls, context: ContextInfo) -> str:
        """Build context section for prompt"""
        sections = []
        
        if context.project_name:
            sections.append(f"## Project\nYou are working on: {context.project_name}")
            if context.project_path:
                sections.append(f"Project path: {context.project_path}")
        
        if context.current_file:
            sections.append(f"\n## Current File\nYou are currently viewing: {context.current_file}")
            if context.file_content:
                content = context.file_content
                if len(content) > 5000:
                    content = content[:5000] + "\n... (truncated)"
                sections.append(f"```\n{content}\n```")
        
        if context.file_tree:
            tree = "\n".join(context.file_tree[:50])
            sections.append(f"\n## File Structure\n```\n{tree}\n```")
        
        if context.git_branch:
            sections.append(f"\n## Git\nBranch: {context.git_branch}")
            if context.git_status:
                sections.append(f"Status: {context.git_status}")
        
        return "\n".join(sections)
    
    @classmethod
    def inject_context(
        cls,
        messages: List[Dict[str, Any]],
        context: ContextInfo,
        position: str = "first",  # "first", "last", or "system"
    ) -> List[Dict[str, Any]]:
        """
        Inject context into message list
        
        Args:
            messages: Original messages
            context: Context to inject
            position: Where to inject ("first", "last", "system")
            
        Returns:
            Messages with context injected
        """
        context_text = cls._build_context_section(context)
        context_message = {"role": "system", "content": context_text}
        
        if position == "first":
            return [context_message] + list(messages)
        elif position == "last":
            return list(messages) + [context_message]
        elif position == "system":
            # Replace or add system message
            result = []
            has_system = False
            for msg in messages:
                if msg.get("role") == "system":
                    msg = {**msg, "content": msg["content"] + "\n\n" + context_text}
                    has_system = True
                result.append(msg)
            if not has_system:
                result.insert(0, context_message)
            return result
        
        return list(messages)
    
    @classmethod
    def truncate_messages(
        cls,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        preserve_last: int = 4,
    ) -> List[Dict[str, Any]]:
        """
        Truncate messages to fit within token limit
        
        Args:
            messages: Messages to truncate
            max_tokens: Maximum token count
            preserve_last: Number of recent messages to always keep
            
        Returns:
            Truncated messages
        """
        # Always keep system message and last N messages
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]
        
        # Keep last N
        preserved = other_msgs[-preserve_last:] if preserve_last > 0 else []
        middle_msgs = other_msgs[:-preserve_last] if preserve_last > 0 else other_msgs
        
        # Count tokens in preserved
        preserved_tokens = sum(
            cls.count_tokens(m.get("content", "")) 
            for m in system_msgs + preserved
        )
        
        remaining_tokens = max_tokens - preserved_tokens
        
        # Add middle messages from newest
        result_middle = []
        current_tokens = 0
        
        for msg in reversed(middle_msgs):
            msg_tokens = cls.count_tokens(msg.get("content", ""))
            if current_tokens + msg_tokens <= remaining_tokens:
                result_middle.insert(0, msg)
                current_tokens += msg_tokens
            else:
                break
        
        return system_msgs + result_middle + preserved


# Alias for backwards compatibility
class MessageV2:
    """Placeholder for message type reference"""
    content: str = ""
