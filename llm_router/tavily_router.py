import os
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
        while True:
            key_name = f"{env_prefix}_{i}"
            key_val = os.getenv(key_name)
            if key_val:
                accounts.append(TavilyAccount(name=key_name, api_key=key_val))
            else:
                break
            i += 1

        if not accounts:
            logger.warning(f"No Tavily API keys found matching prefix '{env_prefix}_*'.")

        return cls(accounts=accounts)


    def get_available_account(self) -> Optional[TavilyAccount]:
        """Return the first available Tavily account from the pool."""
        for acc in self.accounts:
            if acc.is_available:
                return acc
        return None

    def search(self, query: str, **kwargs) -> Dict[str, Any]:
        """Execute a search request trying available Tavily accounts in pool order with failover."""
        tested_accounts = set()

        while len(tested_accounts) < len(self.accounts):
            acc = self.get_available_account()
            if not acc:
                break

            tested_accounts.add(acc.name)
            try:
                response = acc.client.search(query=query, **kwargs)
                return response
            except Exception as e:
                logger.warning(f"Tavily search failed on account {acc.name}: {e}")
                acc.is_available = False  # Mark account as unavailable

        raise RuntimeError("All Tavily accounts in the pool are currently unavailable or failed.")

