"""
LLM Slug Generator - Generate descriptive filenames using LLM

Uses LLM to generate a 1-2 word slug for session memory filenames.
"""

from typing import Optional
import re

from flocks.provider import Provider
from flocks.utils.log import Log

log = Log.create(service="hooks.slug_generator")


async def generate_slug_via_llm(
    conversation: str,
    config: any,
    session_id: str,
    timeout_seconds: int = 15,
) -> Optional[str]:
    """
    Generate a slug using LLM
    
    Args:
        conversation: Conversation summary
        config: Configuration object
        session_id: Session ID (for logging)
        timeout_seconds: Timeout in seconds
        
    Returns:
        slug string or None (on failure)
        
    Examples:
        >>> await generate_slug_via_llm("user: Design API\\nassistant: Sure...")
        "api-design"
    """
    try:
        # Construct prompt
        prompt = f"""Based on this conversation, generate a short 1-2 word filename slug (lowercase, hyphen-separated, no file extension).

Conversation summary:
{conversation[:2000]}

Reply with ONLY the slug, nothing else. Examples: "vendor-pitch", "api-design", "bug-fix"
"""
        
        # Get provider configuration
        provider_id = getattr(config.memory.search.embedding, 'provider', 'openai')
        if provider_id == "auto":
            provider_id = "openai"
        
        # Call LLM (use lightweight model)
        response = await Provider.chat(
            messages=[{"role": "user", "content": prompt}],
            provider_id=provider_id,
            model="gpt-3.5-turbo",  # Fast lightweight model
            max_tokens=50,
            temperature=0.7,
        )
        
        # Extract and clean slug
        if response and response.get('content'):
            text = response['content'].strip()
            
            # Clean format
            slug = text.lower().replace(" ", "-").replace("_", "-")
            
            # Remove invalid characters
            slug = re.sub(r'[^a-z0-9-]', '', slug)
            slug = re.sub(r'-+', '-', slug)
            slug = slug.strip('-')
            
            # Limit length
            slug = slug[:30]
            
            if slug:
                log.debug("slug_generator.success", {
                    "session_id": session_id,
                    "slug": slug,
                })
                return slug
        
        log.warn("slug_generator.no_result", {
            "session_id": session_id,
        })
        return None
        
    except Exception as e:
        log.error("slug_generator.error", {
            "session_id": session_id,
            "error": str(e),
        })
        return None
