from __future__ import annotations

import json
import logging
import threading
import time
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests_oauthlib import OAuth2Session

from .config import Settings
from .exceptions import AuthenticationError

logger = logging.getLogger(__name__)


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None
    state: str | None = None

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != urlparse(self.server.redirect_uri).path:
            self.send_response(404)
            self.end_headers()
            return

        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        _OAuthCallbackHandler.code = params.get("code", [None])[0]
        _OAuthCallbackHandler.error = params.get("error", [None])[0]
        _OAuthCallbackHandler.state = params.get("state", [None])[0]

        body = b"AniList authorization received. You can close this browser window."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        return


class _CallbackServer(HTTPServer):
    def __init__(self, server_address, handler_class, redirect_uri):
        super().__init__(server_address, handler_class)
        self.redirect_uri = redirect_uri


class AniListAuth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token: dict | None = self._load_token()
        self.session: OAuth2Session | None = None

    def _load_token(self) -> dict | None:
        path = Path(self.settings.token_file)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load token file: %s", exc)
            return None

    def _save_token(self, token: dict) -> None:
        path = Path(self.settings.token_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        token = dict(token)
        token["created_at"] = int(time.time())
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="token-", suffix=".tmp", dir=path.parent)
        try:
            with open(fd, "w", encoding="utf-8", closefd=True) as handle:
                json.dump(token, handle, indent=2)
                handle.flush()
            Path(temp_name).replace(path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def _build_session(self) -> OAuth2Session:
        if not self.token:
            raise AuthenticationError("No AniList token is available.")
        self.session = OAuth2Session(self.settings.client_id, token=self.token)
        return self.session

    def is_logged_in(self) -> bool:
        """Return True when a stored AniList access token is still valid.

        AniList currently issues long-lived access tokens and does not support
        refresh tokens, so an expired token requires a new authorization flow.
        """
        if not self.token:
            return False

        created_at = int(self.token.get("created_at", 0) or 0)
        expires_in = int(self.token.get("expires_in", 0) or 0)
        if created_at and expires_in and time.time() >= created_at + expires_in - 300:
            logger.info("AniList access token is expired or near expiry; re-authentication required.")
            self.token = None
            self.session = None
            return False

        self._build_session()
        return True

    def logout(self) -> None:
        """Clear the locally stored AniList session."""
        self.token = None
        self.session = None
        path = Path(self.settings.token_file)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove stored AniList token: %s", exc)

    def reauthorize(self) -> None:
        """Force a fresh OAuth authorization flow."""
        self.logout()
        self.login()

    def login(self) -> None:
        if self.is_logged_in():
            return

        self.settings.validate_credentials()
        oauth = OAuth2Session(self.settings.client_id, redirect_uri=self.settings.redirect_uri)
        auth_url, state = oauth.authorization_url(self.settings.auth_url)

        parsed = urlparse(self.settings.redirect_uri)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        _OAuthCallbackHandler.code = None
        _OAuthCallbackHandler.error = None
        _OAuthCallbackHandler.state = None
        server = _CallbackServer((host, port), _OAuthCallbackHandler, self.settings.redirect_uri)

        # Start listening before opening the browser so a fast OAuth redirect
        # cannot beat the local callback server.
        logger.info("Waiting for AniList OAuth callback on %s", self.settings.redirect_uri)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        webbrowser.open(auth_url)
        thread.join(timeout=180)
        server.server_close()

        if _OAuthCallbackHandler.error:
            raise AuthenticationError(f"AniList authorization failed: {_OAuthCallbackHandler.error}")
        if _OAuthCallbackHandler.state != state:
            raise AuthenticationError("AniList OAuth state validation failed. Please try logging in again.")
        if not _OAuthCallbackHandler.code:
            raise AuthenticationError("Timed out waiting for the AniList OAuth callback.")

        try:
            token = oauth.fetch_token(
                self.settings.token_url,
                code=_OAuthCallbackHandler.code,
                client_secret=self.settings.client_secret,
            )
        except Exception as exc:
            raise AuthenticationError(f"Token exchange failed: {exc}") from exc

        self._save_token(token)
        self.token = token
        self._build_session()
