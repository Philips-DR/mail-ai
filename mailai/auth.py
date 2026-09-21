"""Google credentials, supplied rather than discovered.

The config object is the whole point: as long as a tool reaches into the environment for
its own credentials, "where identity lives" is a fact every tool in the suite has to agree
on, and the second tool either duplicates the lookup or imports the first to borrow it.
`from_environment()` is the named escape hatch, and only a front door may call it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Read-only, and deliberately the narrowest scope that does M0 and M1. Sending needs
# gmail.send and drafting needs gmail.compose; those get added by the milestone that earns
# them, not in advance. A token minted for a scope the code cannot yet use is an unforced
# risk -- and widening the scope forces a re-consent anyway, so there is nothing saved.
READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


@dataclass(frozen=True)
class AuthConfig:
    """Where the OAuth client lives and where the token is cached."""

    credentials_path: Path
    token_path: Path
    scopes: tuple[str, ...] = tuple(READONLY_SCOPES)

    @staticmethod
    def from_environment() -> "AuthConfig":
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mail-ai"
        return AuthConfig(
            credentials_path=Path(os.environ.get("MAIL_AI_CREDENTIALS", "credentials.json")),
            token_path=Path(os.environ.get("MAIL_AI_TOKEN", base / "token.json")),
        )


class MissingCredentials(Exception):
    """No OAuth client JSON, which is a setup problem rather than a bug."""


def load_credentials(config: AuthConfig):
    """A usable Credentials object, refreshing or re-consenting as needed.

    Imported lazily so every pure module in this package stays importable with no Google
    libraries installed and no credentials configured -- which is what lets the test suite
    run anywhere.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if config.token_path.exists():
        try:
            creds = Credentials.from_authorized_user_info(
                json.loads(config.token_path.read_text(encoding="utf-8")), list(config.scopes)
            )
        except (ValueError, json.JSONDecodeError):
            creds = None  # a corrupt token is a token to replace, not a crash

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds, config.token_path)
            return creds
        except Exception:
            # A refresh token goes stale after seven days while the OAuth app is in
            # "Testing". That is the normal state here, not an edge case, so fall through
            # to consent rather than reporting a failure.
            creds = None

    return authorize(config)


def authorize(config: AuthConfig):
    """Always runs the browser flow, whatever is cached. This is what `auth` needs."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not config.credentials_path.exists():
        raise MissingCredentials(
            f"no OAuth client JSON at {config.credentials_path}.\n"
            f"  Download it from Google Cloud (APIs & Services > Credentials, type "
            f"'Desktop app') and save it there, or point MAIL_AI_CREDENTIALS at it."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.credentials_path), list(config.scopes)
    )
    # Port 0: a desktop client may use any loopback port, so nothing is hardcoded and
    # nothing needs registering in the console.
    creds = flow.run_local_server(port=0, prompt="consent")
    _save(creds, config.token_path)
    return creds


def _save(creds, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    # The refresh token is a long-lived secret.
    path.chmod(0o600)


def has_token(config: AuthConfig) -> bool:
    return config.token_path.exists()
