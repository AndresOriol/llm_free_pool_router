from typing import Tuple, List, Dict
from base_provider import LLMProvider

class OpenAICompatibleProvider(LLMProvider):
    """Universal adapter for Groq, OpenAI, Together, Mistral, etc."""

    def _build_request(self, messages: List[Dict[str, str]]) -> Tuple[str, dict, dict]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
        }
        return self.url, headers, payload

    def _parse_response(self, res_json: dict) -> str:
        return res_json["choices"][0]["message"]["content"]


class GeminiProvider(LLMProvider):
    """Adapter for Google Gemini (REST v1beta)."""

    def _build_request(self, messages: List[Dict[str, str]]) -> Tuple[str, dict, dict]:
        # Build a clean URL without the ?key= parameter
        url_clean = f"{self.url}/{self.model}:generateContent"
        
        # Pass the key securely in the headers
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key  # <--- Standard Google API header
        }
        
        gemini_contents = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
            
        payload = {"contents": gemini_contents}
        return url_clean, headers, payload

    def _parse_response(self, res_json: dict) -> str:
        return res_json["candidates"][0]["content"]["parts"][0]["text"]