"""
Session Prompt management module

Manages system prompts, context injection, and token counting.
Based on Flocks' ported src/session/prompt.ts and src/session/system.ts
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
import os
from pathlib import Path
from datetime import datetime
import platform

from flocks.utils.log import Log


log = Log.create(service="session.prompt")


# Output token maximum
OUTPUT_TOKEN_MAX = int(os.getenv("FLOCKS_OUTPUT_TOKEN_MAX", "32000"))


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


# Lazy-loaded prompt templates (loaded from files like Flocks)
def get_prompt_anthropic() -> str:
    return _load_prompt_file("anthropic.txt")


def get_prompt_beast() -> str:
    return _load_prompt_file("beast.txt")


def get_prompt_gemini() -> str:
    return _load_prompt_file("gemini.txt")


def get_prompt_qwen() -> str:
    return _load_prompt_file("qwen.txt")


def get_prompt_codex() -> str:
    return _load_prompt_file("codex_header.txt")


# Fallback prompts if files not found
PROMPT_ANTHROPIC = """You are Flocks, an AI-Native SecOps Platform.

You specialize in cybersecurity operations including threat detection, incident response, vulnerability assessment, log analysis, detection rule creation, and security automation.

When asked about your capabilities, respond that you are an AI-Native SecOps Platform specializing in:
- Threat Detection & Analysis (log analysis, IOC identification, threat hunting)
- Incident Response (investigation, containment, remediation)
- Vulnerability Assessment (scan analysis, prioritization, configuration reviews)
- Security Automation (SIGMA, YARA, Snort, Suricata detection rules)
- Malware & Forensics (artifact analysis, malware identification)
- Compliance & Hardening (CIS, NIST, PCI-DSS, configuration audits)

IMPORTANT: Assist with defensive security tasks only. Refuse to create malicious tools or exploits. Support security analysis, detection rules, vulnerability explanations, defensive tools, and security automation.
"""

PROMPT_GPT = """You are Flocks, a SecOps agent - please keep going until the user's security query is completely resolved.
Your security analysis should be thorough. You MUST iterate and keep going until the security problem is solved.

IMPORTANT: Assist with defensive security only. Support threat detection, incident response, vulnerability assessment, and security automation.
"""

PROMPT_GEMINI = """You are Flocks, an advanced AI SecOps agent specializing in cybersecurity operations.
Focus on threat detection, security analysis, incident response, vulnerability assessment, and defensive automation.

IMPORTANT: Defensive security only - no malicious tools or exploits.
"""

PROMPT_DEFAULT = """You are Flocks, an AI-Native SecOps Platform.

When asked about your capabilities, respond that you are an AI-Native SecOps Platform specializing in:
- Threat Detection & Analysis (log analysis, IOC identification, threat hunting)
- Incident Response (investigation, containment, remediation)
- Vulnerability Assessment (scan analysis, prioritization, configuration reviews)
- Security Automation (SIGMA, YARA, Snort, Suricata detection rules)
- Malware & Forensics (artifact analysis, malware identification)
- Compliance & Hardening (CIS, NIST, PCI-DSS, configuration audits)

You specialize in cybersecurity operations including:
- Threat detection and analysis (log analysis, IOC identification, behavioral detection)
- Incident response (investigation, containment, remediation recommendations)
- Vulnerability assessment (scan analysis, prioritization, security reviews)
- Security automation (detection rules: SIGMA, YARA, Snort, Suricata)
- Compliance and hardening (CIS, NIST, PCI-DSS, configuration reviews)

IMPORTANT: Assist with defensive security tasks only. Refuse to create malicious tools, exploits for offensive use, or malware. Support security analysis, detection rules, vulnerability explanations, defensive tools, and security automation.
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
    def provider(cls, model_id: str) -> List[str]:
        """
        Get provider-specific base prompt based on model
        
        Loads from template files (same as Flocks).
        
        Args:
            model_id: Model identifier
            
        Returns:
            List of prompt strings
        """
        model_lower = model_id.lower()
        
        # GPT-5: use codex_header.txt
        if "gpt-5" in model_lower:
            prompt = get_prompt_codex()
            return [prompt] if prompt else [PROMPT_GPT]
        
        # GPT/o1/o3: use beast.txt
        if "gpt-" in model_lower or "o1" in model_lower or "o3" in model_lower:
            prompt = get_prompt_beast()
            return [prompt] if prompt else [PROMPT_GPT]
        
        # Gemini: use gemini.txt
        if "gemini" in model_lower:
            prompt = get_prompt_gemini()
            return [prompt] if prompt else [PROMPT_GEMINI]
        
        # Claude: use anthropic.txt
        if "claude" in model_lower:
            prompt = get_prompt_anthropic()
            return [prompt] if prompt else [PROMPT_ANTHROPIC]
        
        # Other models: use qwen.txt
        prompt = get_prompt_qwen()
        return [prompt] if prompt else [PROMPT_DEFAULT]
    
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
        working_dir = directory or os.getcwd()
        is_git = vcs == "git"

        from flocks.workspace.manager import WorkspaceManager
        ws = WorkspaceManager.get_instance()
        outputs_dir = str(ws.get_default_outputs_dir())

        env_info = [
            "Here is some useful information about the environment you are running in:",
            "<env>",
            f"  Workspace outputs directory: {outputs_dir}",
            f"  Source code directory: {working_dir}",
            f"  Is directory a git repo: {'yes' if is_git else 'no'}",
            f"  Platform: {platform.system().lower()}",
            f"  Today's date: {datetime.now().strftime('%A %b %d, %Y')}",
            "</env>",
        ]
        
        return ["\n".join(env_info)]
    
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
        found_paths = set()
        
        search_dir = directory or os.getcwd()
        root_dir = worktree or search_dir
        
        # Search for local rule files
        for rule_file in cls.LOCAL_RULE_FILES:
            path = cls._find_file_up(rule_file, search_dir, root_dir)
            if path and path not in found_paths:
                found_paths.add(path)
                try:
                    content = Path(path).read_text(encoding="utf-8")
                    results.append(f"Instructions from: {path}\n{content}")
                except Exception as e:
                    log.warn("custom.read_error", {"path": path, "error": str(e)})
                break  # Only load first found local rule file
        
        # Load additional instruction files from config
        if config_instructions:
            for instruction_path in config_instructions:
                # Handle URL instructions (skip for now)
                if instruction_path.startswith(("http://", "https://")):
                    continue
                
                # Expand ~ to home directory
                if instruction_path.startswith("~/"):
                    instruction_path = os.path.expanduser(instruction_path)
                
                # Resolve path
                if not os.path.isabs(instruction_path):
                    instruction_path = os.path.join(search_dir, instruction_path)
                
                if instruction_path not in found_paths and os.path.exists(instruction_path):
                    found_paths.add(instruction_path)
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
    async def build_system_prompt(
        cls,
        agent_name: str = "assistant",
        model_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        context: Optional[ContextInfo] = None,
        custom_instructions: Optional[str] = None,
        include_environment: bool = True,
        include_custom: bool = True,
        include_memory: bool = True,
        session_memory: Optional["SessionMemory"] = None,
        user_message: Optional[str] = None,
    ) -> str:
        """
        Build complete system prompt with all components
        
        Args:
            agent_name: Agent name
            model_id: Model identifier for provider-specific prompts
            provider_id: Provider identifier
            context: Context information
            custom_instructions: Additional custom instructions
            include_environment: Whether to include environment info
            include_custom: Whether to include custom instruction files
            include_memory: Whether to include memory context (NEW)
            session_memory: SessionMemory instance (NEW)
            user_message: Current user message for memory search (NEW)
            
        Returns:
            Complete system prompt
        """
        parts = []
        
        # Provider-specific base prompt
        if model_id:
            parts.extend(SystemPrompt.provider(model_id))
        else:
            parts.append(f"You are {agent_name}, an AI assistant for software development.")
        
        # Header (provider-specific)
        if provider_id:
            parts.extend(SystemPrompt.header(provider_id))
        
        # Environment information
        if include_environment:
            directory = context.project_path if context else None
            vcs = context.vcs if context else None
            env_parts = await SystemPrompt.environment(directory=directory, vcs=vcs)
            parts.extend(env_parts)
        
        # Context injection
        if context:
            context_parts = cls._build_context_section(context)
            if context_parts:
                parts.append(context_parts)
        
        # Memory context (NEW)
        if include_memory and session_memory and user_message:
            memory_context = await cls.build_memory_context(
                session_memory=session_memory,
                user_message=user_message,
                max_results=3,
            )
            if memory_context:
                parts.append(memory_context)
        
        # Custom instructions from files
        if include_custom:
            directory = context.project_path if context else None
            custom_parts = await SystemPrompt.custom(directory=directory)
            parts.extend(custom_parts)
        
        # Additional custom instructions
        if custom_instructions:
            parts.append(f"\n## Additional Instructions\n{custom_instructions}")
        
        return "\n\n".join(parts)
    
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
