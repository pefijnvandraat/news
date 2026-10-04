"""Who is making this request.

App Service Authentication ("Easy Auth") handles the sign-in dance on the
platform, before a request ever reaches Python, and injects the result as
headers. This module turns those headers into a user id and decides whether
that user is allowed in.

Three decisions worth stating:

* **Keyed on e-mail, not on the provider's subject id.** Signing in with
  Microsoft one day and Google the next should land you on your own
  favourites, not a second empty account. E-mail is the only identifier both
  providers agree on.

* **Fails closed when hosted.** If the platform headers are absent, or Easy
  Auth is not actually switched on, every request is refused. Without that
  check a client could simply send `X-MS-CLIENT-PRINCIPAL-ID` itself and
  become anyone - Easy Auth strips those headers from inbound requests, but
  only while it is enabled.

* **Allow-list by default.** Microsoft and Google accounts are free and
  worldwide, so "signed in" is not the same as "invited". An empty allow-list
  is treated as a closed door, not an open one.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading

log = logging.getLogger("nieuws.identity")

HOSTED = bool(os.environ.get("WEBSITE_SITE_NAME"))

# App Service sets WEBSITE_AUTH_ENABLED for Easy Auth v1 but NOT for v2, and
# refuses to let you set it yourself (it is a reserved name). So authentication
# is confirmed through our own flag, written as part of enabling Easy Auth.
#
# The check matters: Easy Auth strips inbound X-MS-CLIENT-PRINCIPAL* headers
# while it is on. With it off, anyone could send those headers and become any
# user. Trusting them unconditionally would be the whole vulnerability.
AUTH_ENABLED = (os.environ.get("NIEUWS_AUTH_ACTIVE", "").lower() in ("1", "true", "yes")
                or os.environ.get("WEBSITE_AUTH_ENABLED", "").lower() == "true")

# Local development has no platform in front of it.
DEV_USER = os.environ.get("NIEUWS_DEV_USER", "local")

# The user that inherits the single-user data from before sign-in existed.
LEGACY_USER = "local"
LEGACY_OWNER = (os.environ.get("NIEUWS_LEGACY_OWNER") or "").strip().lower()

_claims_lock = threading.Lock()


class AccessDenied(Exception):
    """Signed in, but not on the guest list."""

    def __init__(self, email: str):
        super().__init__(email)
        self.email = email


class NotSignedIn(Exception):
    """No usable principal on a request that requires one."""


def allow_list() -> set[str]:
    """Addresses permitted to sign in, lower-cased.

    Comma- or semicolon-separated in NIEUWS_ALLOWED_USERS. A bare domain like
    "@example.com" admits everyone on that domain.
    """
    raw = os.environ.get("NIEUWS_ALLOWED_USERS", "")
    return {p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()}


def is_allowed(email: str) -> bool:
    allowed = allow_list()
    if not allowed:
        # No list configured: refuse everyone rather than admit everyone. A
        # misconfiguration should lock the owner out, not open the door.
        return False
    email = (email or "").strip().lower()
    if not email:
        return False
    if email in allowed:
        return True
    domain = "@" + email.partition("@")[2]
    return domain in allowed and len(domain) > 1


def _decode_principal(header: str) -> dict:
    """Parse the base64 JSON blob App Service sends."""
    try:
        padded = header + "=" * (-len(header) % 4)
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}


# Claim names used for e-mail, most specific first. Providers disagree, and
# Entra in particular often puts the address in `preferred_username`.
EMAIL_CLAIMS = (
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "emails", "email", "preferred_username", "upn", "unique_name", "name",
)
NAME_CLAIMS = (
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "name", "given_name",
)


def _claims_map(principal: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for claim in principal.get("claims") or []:
        key = claim.get("typ") or claim.get("type")
        val = claim.get("val") or claim.get("value")
        if key and val and key not in out:
            out[key] = val
    return out


def _pick(claims: dict[str, str], names) -> str:
    for n in names:
        val = claims.get(n)
        if val:
            return val
    return ""


def principal_from_headers(headers) -> dict | None:
    """The signed-in user, or None when there is no principal at all.

    Returns {id, email, name, provider}. `id` is what the rest of the app
    stores in user_id.
    """
    blob = headers.get("x-ms-client-principal")
    principal = _decode_principal(blob) if blob else {}
    claims = _claims_map(principal) if principal else {}

    email = (_pick(claims, EMAIL_CLAIMS)
             or headers.get("x-ms-client-principal-name", "")).strip()
    provider = (principal.get("auth_typ")
                or headers.get("x-ms-client-principal-idp", "")).strip()
    subject = (headers.get("x-ms-client-principal-id", "")).strip()

    if not (email or subject):
        return None

    name = _pick(claims, NAME_CLAIMS) or email or subject

    # Prefer the address: it survives switching provider. Only when a provider
    # hides it do we fall back to something provider-specific, which means that
    # person gets a separate account per provider - rare, and visible to them.
    if email and "@" in email:
        user_id = "u:" + email.lower()
    else:
        user_id = f"p:{provider or 'unknown'}:{subject}"

    return {"id": user_id, "email": email.lower(), "name": name,
            "provider": provider or "onbekend"}


def current_user(headers) -> dict:
    """The user for this request, or raise.

    Local development has no platform in front of it, so it falls back to a
    fixed developer identity. That fallback is deliberately unavailable when
    hosted: there it would be an authentication bypass.
    """
    principal = principal_from_headers(headers)

    if principal is None:
        if HOSTED:
            raise NotSignedIn("geen aanmeldgegevens op deze aanvraag")
        return {"id": DEV_USER, "email": "", "name": "Lokale gebruiker",
                "provider": "lokaal", "dev": True}

    if HOSTED and not AUTH_ENABLED:
        # Headers present but the platform is not enforcing anything: they
        # could have come from the client. Trusting them here would be the
        # whole vulnerability.
        log.error("principal headers present while Easy Auth is off - refusing")
        raise NotSignedIn("aanmelding is niet actief op deze omgeving")

    if not is_allowed(principal["email"]):
        raise AccessDenied(principal["email"])

    return principal


def is_legacy_owner(user: dict) -> bool:
    """Whether this user should inherit the pre-sign-in data."""
    return bool(LEGACY_OWNER) and user.get("email", "") == LEGACY_OWNER
