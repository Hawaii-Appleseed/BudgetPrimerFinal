"""Read a Google Doc, with or without credentials.

Unauthenticated: the Markdown export endpoint serves any link-shared doc, which
is what the editor's Import text from the Doc uses — no secrets to hold.

Authenticated: the same endpoint with a service-account bearer token, which is
what CI uses. It also reaches docs that are shared only with the service
account, and is the only way to read modifiedTime for conflict detection.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

SCOPES = ("https://www.googleapis.com/auth/documents",
          "https://www.googleapis.com/auth/drive")


class FetchError(RuntimeError):
    pass


def export_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/export?format=markdown"


def _get(url: str, token: str | None = None, accept_html: bool = False) -> bytes:
    headers = {"User-Agent": "docsync"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if not accept_html and "text/html" in r.headers.get("Content-Type", ""):
                raise FetchError(
                    "Google returned a sign-in page, not Markdown. Either share "
                    "the doc as 'Anyone with the link -> Viewer', or share it with "
                    "the service account and run with credentials.")
            return r.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} fetching {url}") from e


def fetch_markdown(doc_id: str, token: str | None = None) -> str:
    return _get(export_url(doc_id), token).decode("utf-8")


def fetch_modified_time(doc_id: str, token: str) -> str:
    """Drive's modifiedTime (RFC-3339). Requires credentials — this is the
    signal that tells a push whether the doc changed since the last sync."""
    url = ("https://www.googleapis.com/drive/v3/files/"
           f"{doc_id}?fields=modifiedTime")
    return json.loads(_get(url, token))["modifiedTime"]


def replace_doc(doc_id: str, markdown: str, token: str) -> None:
    """Overwrite the doc body by importing Markdown — Drive converts headings,
    bold, links and lists into native Docs formatting, and its exporter turns
    them back, which is what makes the round-trip work at all."""
    url = ("https://www.googleapis.com/upload/drive/v3/files/"
           f"{doc_id}?uploadType=media")
    req = urllib.request.Request(
        url, data=markdown.encode("utf-8"), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "text/markdown"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} replacing doc {doc_id}: "
                         f"{e.read().decode('utf-8', 'replace')[:400]}") from e


def _post(url: str, body: dict, token: str, method: str = "POST") -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read() or b"{}")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} {method} {url}: "
                         f"{e.read().decode('utf-8', 'replace')[:400]}") from e


def create_doc(title: str, token: str, folder: str | None = None) -> str:
    """A new empty Doc, owned by the service account.

    Created inside `folder` when given: a folder the human shared with the
    service account once means every doc made here is reachable without any
    further per-doc sharing, which is the whole point.
    """
    body: dict = {"name": title,
                  "mimeType": "application/vnd.google-apps.document"}
    if folder:
        body["parents"] = [folder]
    url = "https://www.googleapis.com/drive/v3/files?fields=id&supportsAllDrives=true"
    return _post(url, body, token)["id"]


def share(doc_id: str, email: str, token: str, role: str = "writer") -> None:
    """Grant a human access to a doc the service account owns. Notification
    email is suppressed — this fires on every bind and is not news."""
    url = (f"https://www.googleapis.com/drive/v3/files/{doc_id}/permissions"
           "?sendNotificationEmail=false&supportsAllDrives=true")
    _post(url, {"type": "user", "role": role, "emailAddress": email}, token)


def can_access(doc_id: str, token: str) -> tuple[bool, str]:
    """Whether the service account can see a doc — the check that turns
    'permission denied' into 'share it with this address'."""
    try:
        fetch_modified_time(doc_id, token)
        return True, ""
    except FetchError as e:
        return False, str(e)


def service_account_email(service_account_json: str) -> str:
    """The key's identity, or "" if the key is unreadable — callers use this to
    tell the user who to share a doc with, and must not die doing it."""
    try:
        return json.loads(service_account_json).get("client_email", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def access_token(service_account_json: str) -> str:
    """Mint an access token from a service-account key (JWT bearer grant)."""
    try:
        from google.oauth2 import service_account            # noqa: PLC0415
        from google.auth.transport.requests import Request   # noqa: PLC0415
    except ImportError as e:
        # google-auth does not pull in requests, which its request transport
        # needs — installing only google-auth looks fine until it doesn't.
        raise FetchError(
            f"a dependency for authenticated access is missing ({e.name}):\n"
            "  pip install google-auth requests") from e

    # A bad key surfaces as ValueError from a PEM parser several layers down;
    # callers only know FetchError, and a stack trace is not a diagnosis.
    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError as e:
        raise FetchError(f"the key is not valid JSON: {e}") from e
    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=list(SCOPES))
        creds.refresh(Request())
    except Exception as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    return creds.token
