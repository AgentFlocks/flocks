"""
Provider SDK implementations

This module contains all provider SDK implementations for different AI platforms.
Each provider implements the BaseProvider interface for consistent API access.

Providers:
- Core Providers (Batch 1+2):
  - OpenAI, Anthropic, Google, Azure
  - OpenAI Compatible, Mistral, Groq, Cohere, Together

- Extended Providers (Batch 3):
  - xAI, DeepInfra, Cerebras, Perplexity, OpenRouter
  - Google Vertex, Local

- Enterprise Providers (Batch 5):
  - Gateway, GitLab

- Additional Providers (Batch 6):
  - GitHub Copilot, GitHub Copilot Enterprise
  - Vercel AI, Flocks
  - SAP AI Core, Cloudflare AI Gateway
"""

# Core providers
from flocks.provider.sdk.openai import OpenAIProvider
from flocks.provider.sdk.anthropic import AnthropicProvider
from flocks.provider.sdk.google import GoogleProvider
from flocks.provider.sdk.azure import AzureProvider
from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider
from flocks.provider.sdk.mistral import MistralProvider
from flocks.provider.sdk.groq import GroqProvider
from flocks.provider.sdk.cohere import CohereProvider
from flocks.provider.sdk.together import TogetherProvider

# Extended providers
from flocks.provider.sdk.xai import XAIProvider
from flocks.provider.sdk.deepinfra import DeepInfraProvider
from flocks.provider.sdk.cerebras import CerebrasProvider
from flocks.provider.sdk.perplexity import PerplexityProvider
from flocks.provider.sdk.openrouter import OpenRouterProvider
from flocks.provider.sdk.vertex import VertexProvider
from flocks.provider.sdk.local import LocalProvider

# Enterprise providers
from flocks.provider.sdk.gateway import GatewayProvider
from flocks.provider.sdk.gitlab import GitLabProvider

# Additional providers (Batch 6)
from flocks.provider.sdk.github_copilot import (
    GitHubCopilotProvider,
    GitHubCopilotEnterpriseProvider,
)
from flocks.provider.sdk.vercel import VercelProvider
from flocks.provider.sdk.opencode import OpenCodeProvider as FlocksCompatProvider
from flocks.provider.sdk.sap_ai_core import SAPAICoreProvider
from flocks.provider.sdk.cloudflare_gateway import CloudflareGatewayProvider

# Final providers (Batch 7)
from flocks.provider.sdk.vertex_anthropic import VertexAnthropicProvider
from flocks.provider.sdk.azure_cognitive import AzureCognitiveServicesProvider
from flocks.provider.sdk.zenmux import ZenMuxProvider


__all__ = [
    # Core providers
    "OpenAIProvider",
    "AnthropicProvider",
    "GoogleProvider",
    "AzureProvider",
    "OpenAICompatibleProvider",
    "MistralProvider",
    "GroqProvider",
    "CohereProvider",
    "TogetherProvider",
    # Extended providers
    "XAIProvider",
    "DeepInfraProvider",
    "CerebrasProvider",
    "PerplexityProvider",
    "OpenRouterProvider",
    "VertexProvider",
    "LocalProvider",
    # Enterprise providers
    "GatewayProvider",
    "GitLabProvider",
    # Additional providers (Batch 6)
    "GitHubCopilotProvider",
    "GitHubCopilotEnterpriseProvider",
    "VercelProvider",
    "FlocksCompatProvider",
    "SAPAICoreProvider",
    "CloudflareGatewayProvider",
    # Final providers (Batch 7)
    "VertexAnthropicProvider",
    "AzureCognitiveServicesProvider",
    "ZenMuxProvider",
]
