import os
import time
import logging
from typing import List, Optional, Dict, Any

from tavily import TavilyClient

logger = logging.getLogger("TavilyRouter")

class TavilyAccount:
    """Represents a single Tavily API key/account and its status."""

    def __init__(self, name: str, api_key: str):
        self.name = name
        self.api_key = api_key
        self.client = TavilyClient(api_key=api_key)
        self.is_available = True
        self.cooldown_until = 0.0
        self.consecutive_failures = 0


    def check_availability(self) -> bool:
        """Check if account has finished cooldown."""
        if not self.is_available and time.time() > self.cooldown_until:
            self.is_available = True
            self.consecutive_failures = 0
            logger.info(f"Tavily account {self.name} finished cooldown and is available again.")
        return self.is_available

    def trigger_cooldown(self, duration: float = 60.0):
        """Temporarily block this account upon rate limit or error."""
        self.is_available = False
        self.consecutive_failures += 1
        # Exponential backoff capped at 300 seconds
        actual_duration = min(duration * (2 ** (self.consecutive_failures - 1)), 300.0)
        self.cooldown_until = time.time() + actual_duration
        logger.warning(f"Tavily account {self.name} rate limited/failed. Cooldown for {actual_duration}s.")


class TavilyPoolRouter:
    """Pool router for Tavily search accounts to handle failover and rate limits."""

    def __init__(self, accounts: Optional[List[TavilyAccount]] = None):
        self.accounts = accounts or []

    @classmethod
    def from_env(cls, env_prefix: str = "TAVILY_API_KEY") -> "TavilyPoolRouter":
        """Build pool automatically from numbered environment variables (e.g. TAVILY_API_KEY_1, TAVILY_API_KEY_2)."""
        from dotenv import load_dotenv
        from pathlib import Path

        # Load package .env if present
        package_env = Path(__file__).resolve().parent / ".env"
        load_dotenv(package_env)
        load_dotenv()

        accounts = []
        # Check numbered suffix keys (TAVILY_API_KEY_1, TAVILY_API_KEY_2, ...)
        i = 1
        while i <= 20:
            key_name = f"{env_prefix}_{i}"
            key_val = os.getenv(key_name)
            if key_val:
                accounts.append(TavilyAccount(name=key_name, api_key=key_val))
            i += 1

        if not accounts:
            logger.warning(f"No Tavily API keys found matching prefix '{env_prefix}_*'.")

        return cls(accounts=accounts)




    def get_available_account(self) -> Optional[TavilyAccount]:
        """Return the first available Tavily account from the pool."""
        for acc in self.accounts:
            if acc.check_availability():
                return acc
        return None

    def search(self, query: str, **kwargs) -> Dict[str, Any]:
        """Execute a search request trying available Tavily accounts in pool order with failover."""
        tested_accounts = set()

        while len(tested_accounts) < len(self.accounts):
            acc = self.get_available_account()
            if not acc or acc.name in tested_accounts:
                break

            tested_accounts.add(acc.name)
            try:
                response = acc.client.search(query=query, **kwargs)
                return response
            except Exception as e:
                err_msg = str(e).lower()
                logger.warning(f"Tavily search failed on account {acc.name}: {e}")

                # Rate limit or quota exhaustion check
                if "rate" in err_msg or "limit" in err_msg or "quota" in err_msg or "429" in err_msg:
                    acc.trigger_cooldown(60.0)
                else:
                    acc.trigger_cooldown(30.0)

        raise RuntimeError("All Tavily accounts in the pool are currently unavailable or failed.")

    def qna_search(self, query: str, **kwargs) -> str:
        """Execute a quick Q&A search with Tavily."""
        tested_accounts = set()

        while len(tested_accounts) < len(self.accounts):
            acc = self.get_available_account()
            if not acc or acc.name in tested_accounts:
                break

            tested_accounts.add(acc.name)
            try:
                return acc.client.qna_search(query=query, **kwargs)
            except Exception as e:
                err_msg = str(e).lower()
                logger.warning(f"Tavily Q&A search failed on account {acc.name}: {e}")
                if "rate" in err_msg or "limit" in err_msg or "quota" in err_msg or "429" in err_msg:
                    acc.trigger_cooldown(60.0)
                else:
                    acc.trigger_cooldown(30.0)

        raise RuntimeError("All Tavily accounts in the pool are currently unavailable or failed.")
