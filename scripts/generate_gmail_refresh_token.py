"""One-time local helper that prints a Gmail OAuth refresh token.

Pass Google's downloaded Desktop OAuth JSON with ``--credentials`` or enter
the client values interactively. The token is never written to disk.
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def required_setting(name: str, *, hidden: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        prompt = f"{name}: "
        value = (getpass.getpass(prompt) if hidden else input(prompt)).strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Gmail OAuth refresh token")
    parser.add_argument(
        "--credentials",
        type=Path,
        help="Path to the Desktop OAuth client JSON downloaded from Google Cloud",
    )
    args = parser.parse_args()

    if args.credentials:
        if not args.credentials.is_file():
            raise SystemExit(f"Credentials JSON not found: {args.credentials}")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(args.credentials), [SCOPE]
        )
    else:
        client_id = required_setting("GMAIL_CLIENT_ID")
        client_secret = required_setting("GMAIL_CLIENT_SECRET", hidden=True)
        configuration = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(configuration, [SCOPE])
    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    if not credentials.refresh_token:
        raise SystemExit(
            "Google did not return a refresh token. Revoke the app grant and retry."
        )
    print("\nGMAIL_REFRESH_TOKEN (store it now; do not commit it):")
    print(credentials.refresh_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
