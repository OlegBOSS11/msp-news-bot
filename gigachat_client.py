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
        # which isn't in standard trust stores — that's why TLS verification
        # was disabled outright before. The correct fix is to verify against
        # that CA explicitly rather than skip verification: set
        # GIGACHAT_CA_BUNDLE to the path of the Mintsifry root cert (get it
        # from https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer,
        # convert to PEM if needed) and TLS verification turns on.
        # Falls back to the old insecure behavior if that's not configured,
        # so this doesn't break a server that hasn't set it up yet — but
        # that fallback is a known gap (MITM on credentials/questions/answers),
        # not a permanent choice, and gets logged loudly on every startup.
        ca_bundle = os.environ.get("GIGACHAT_CA_BUNDLE", "")
        if ca_bundle and Path(ca_bundle).is_file():
            _client = GigaChatAsyncClient(
                credentials=credentials,
                verify_ssl_certs=True,
                ca_bundle_file=ca_bundle,
                timeout=30,
                max_retries=3,
            )
        else:
            logger.warning(
                "GIGACHAT_CA_BUNDLE not set (or file missing) — falling back to "
                "verify_ssl_certs=False. This disables TLS verification for all "
                "GigaChat traffic (credentials, user questions, AI answers) and "
                "should be fixed: set GIGACHAT_CA_BUNDLE to the Mintsifry root "
                "CA path. See CONTRIBUTING.md / DEPLOY.md."
            )
            _client = GigaChatAsyncClient(
                credentials=credentials,
                verify_ssl_certs=False,
                timeout=30,
                max_retries=3,
            )
    return _client
