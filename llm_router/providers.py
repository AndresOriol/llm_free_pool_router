from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

from .base_provider import LLMProvider

# Seconds a single call may take before it is abandoned and the router tries
# the next account. Without one the wait is unbounded: google-genai passes
# `timeout=None` straight to httpx, which *disables* httpx's own default rather
# than falling back to it, and omits the `X-Server-Timeout` header that would
# have let the server give up too. A laptop suspended mid-request then wakes to
# a dead socket and blocks forever -- one eval run sat 43 minutes on a request
# that could never be answered.
#
# 120s is six times the p99 of 2,128 recorded calls (median 1s, p99 20s). It
# would have cut two of them, both from a degraded window that was worth
# cutting. A cut call is not a lost call: `base_provider` classifies a timeout
# as reroutable, so the cost is one retry against another member, which is what
# the pool is for.
CALL_TIMEOUT_S = 120


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
            timeout=CALL_TIMEOUT_S,
        )


class GeminiProvider(LLMProvider):
    """Adapter for Google Gemini via langchain-google-genai."""

    def build_chat_model(self) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            model=self.model,
            google_api_key=self.api_key,
            temperature=self.temperature,
            max_retries=0,
            timeout=CALL_TIMEOUT_S,
        )
