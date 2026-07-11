from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

from .base_provider import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    """Universal adapter for any OpenAI-compatible endpoint (Groq, Together, etc.)."""

    def build_chat_model(self) -> BaseChatModel:
        base_url = self.url
        # Config may carry the full chat-completions path; ChatOpenAI wants the v1 base.
        if base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]

        return ChatOpenAI(
            base_url=base_url,
            api_key=self.api_key,
            model=self.model,
            temperature=self.temperature,
            max_retries=0,  # the router owns failover; don't let the SDK retry a dead account
        )


class GeminiProvider(LLMProvider):
    """Adapter for Google Gemini via langchain-google-genai."""

    def build_chat_model(self) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            model=self.model,
            google_api_key=self.api_key,
            temperature=self.temperature,
            max_retries=0,
        )
