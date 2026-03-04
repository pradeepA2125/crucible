from .anthropic_transport import AnthropicJsonTransport
from .contracts import ModelJsonTransport
from .gemini_transport import GeminiJsonTransport
from .groq_transport import GroqJsonTransport
from .huggingface_transport import HuggingFaceJsonTransport
from .openai_transport import OpenAIJsonTransport

__all__ = [
    "ModelJsonTransport",
    "OpenAIJsonTransport",
    "AnthropicJsonTransport",
    "GeminiJsonTransport",
    "GroqJsonTransport",
    "HuggingFaceJsonTransport",
    "OpenAIReasoningEngine",
]


def __getattr__(name: str) -> object:
    if name == "OpenAIReasoningEngine":
        from .openai_reasoner import OpenAIReasoningEngine

        return OpenAIReasoningEngine
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
