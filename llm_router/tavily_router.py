import os
import logging
from typing import List, Optional

from tavily import TavilyClient

logger = logging.getLogger("TavilyRouter")

# ~5,000 tokens a page: a reference page arrives whole, and three of them do not
# evict the conversation carrying them (15.3.1).
MAX_PAGE_CHARS = 20_000
FETCH_TIMEOUT = 10.0

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")


class NoSearchPool(SystemExit):
    """Raised before the run when no Tavily account is configured."""


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

    def check(self) -> int:
        """Refuse before the run rather than during it. Returns the account count."""
        count = len(self.accounts)
        if not count:
            raise NoSearchPool(
                "No Tavily account is configured, so this agent cannot search. Put "
                "at least one key in llm_router/.env as TAVILY_API_KEY_1 "
                "(TAVILY_API_KEY_2, _3, ... are picked up automatically).")
        if count == 1:
            logger.warning(
                "Only one Tavily account is configured. Search still works and has "
                "nothing to fail over to: when its monthly credits run out, every "
                "search fails until the next reset.")
        logger.info(f"{count} Tavily account(s) in the search pool.")
        return count

    def _fetch_page(self, url: str, timeout: float = FETCH_TIMEOUT) -> str:
        """One page as markdown, or a readable explanation of why not."""
        import httpx
        from markdownify import markdownify

        try:
            response = httpx.get(url, headers={"User-Agent": _UA}, timeout=timeout,
                                    follow_redirects=True)
            response.raise_for_status()
            text = markdownify(response.text)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            logger.info(f"Could not fetch {url}: {exc!r}")
            return (f"(could not fetch this page: {exc}. The URL and title above "
                    f"are still real -- cite them only for what the search snippet "
                    f"supports, or find another source.)")

        if len(text) > MAX_PAGE_CHARS:
            return (text[:MAX_PAGE_CHARS]
                    + f"\n\n...(page truncated at {MAX_PAGE_CHARS:,} of "
                        f"{len(text):,} characters. Search for a narrower question if "
                        f"what you need was further down.)")
        return text

    def search(self, query: str, **kwargs) -> str:
        """Execute a search request trying available Tavily accounts in pool order with failover."""
        tested_accounts = set()

        while len(tested_accounts) < len(self.accounts):
            acc = self.get_available_account()
            if not acc:
                break

            tested_accounts.add(acc.name)
            try:
                results = acc.client.search(query=query, **kwargs)
                hits = results.get("results") or []
                if not hits:
                    return (f"No results for {query!r}. Rephrase it as a different "
                            f"question rather than repeating this one.")

                blocks = []
                for hit in hits:
                    url, title = hit.get("url", ""), hit.get("title", "(untitled)")
                    blocks.append(f"## {title}\n**URL:** {url}\n\n{self._fetch_page(url)}\n\n---\n")

                return f"Found {len(blocks)} result(s) for '{query}':\n\n" + "\n".join(blocks)

            except Exception as e:
                logger.warning(f"Tavily search failed on account {acc.name}: {e}")
                acc.is_available = False  # Mark account as unavailable

        raise RuntimeError("All Tavily accounts in the pool are currently unavailable or failed.")

