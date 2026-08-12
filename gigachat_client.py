"""Shared GigaChat client for all modules."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables once
load_dotenv(Path(__file__).parent / ".env")

# Shared client instance
_client = None


def get_gigachat_client():
    """Get or create a shared GigaChat client."""
    global _client
    if _client is None:
        from gigachat import GigaChatAsyncClient
        credentials = os.environ.get("GIGACHAT_CREDENTIALS", "")
        if not credentials:
            logger.error("GIGACHAT_CREDENTIALS not set")
            return None

        # GigaChat's API is served under the Russian Mintsifry root CA,
        # which isn't in standard trust stores — verify_ssl_certs=False is
        # a deliberate, kept choice for that reason, not an oversight.
        _client = GigaChatAsyncClient(
            credentials=credentials,
            verify_ssl_certs=False,
            timeout=30,
            max_retries=3,
        )
    return _client
