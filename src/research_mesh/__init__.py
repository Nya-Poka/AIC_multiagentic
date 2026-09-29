"""Research Mesh minimal AIP research collaboration system."""

__version__ = "0.7.0"

from .llm import LLMCompletionRequest, LLMCompletionResponse, LLMGatewayClient
from .schemas import ResearchReport, ResearchRequest

__all__ = [
    "__version__",
    "LLMCompletionRequest",
    "LLMCompletionResponse",
    "LLMGatewayClient",
    "ResearchReport",
    "ResearchRequest",
]
