from __future__ import annotations

import auth_utils


def test_sync_supabase_client_options_include_storage() -> None:
    """Guard against importing the legacy options class in supabase-py 2.31+."""

    if auth_utils.ClientOptions is None:
        return

    options = auth_utils.ClientOptions()
    assert hasattr(options, "storage")

