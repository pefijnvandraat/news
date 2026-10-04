"""Security checks on the identity layer.

The dangerous failure here is not "nobody can sign in" but "anybody can be
anybody". These tests cover the spoofing paths explicitly.
"""
import base64
import importlib
import json
import os
import sys

sys.path.insert(0, ".")

ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"{label:<60} -> {str(got):<26} (verwacht {want}) {'OK' if good else 'FOUT'}")


def reload_identity(**env):
    """Re-import identity with a given environment (it reads env at import)."""
    for k in ("WEBSITE_SITE_NAME", "WEBSITE_AUTH_ENABLED", "NIEUWS_AUTH_ACTIVE",
              "NIEUWS_ALLOWED_USERS", "NIEUWS_LEGACY_OWNER"):
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    import app.identity as I
    return importlib.reload(I)


def principal(email=None, name=None, provider="aad", subject=None):
    claims = []
    if email:
        claims.append({"typ": "preferred_username", "val": email})
    if name:
        claims.append({"typ": "name", "val": name})
    blob = base64.b64encode(
        json.dumps({"auth_typ": provider, "claims": claims}).encode()).decode()
    h = {"x-ms-client-principal": blob}
    if subject:
        h["x-ms-client-principal-id"] = subject
    return h


# --- local development ----------------------------------------------------
I = reload_identity()
check("lokaal zonder header -> ontwikkelaar",
      I.current_user({})["id"], "local")

# --- hosted: no principal means no access ---------------------------------
I = reload_identity(WEBSITE_SITE_NAME="nieuws-pf", NIEUWS_AUTH_ACTIVE="true",
                    NIEUWS_ALLOWED_USERS="anna@example.com")
try:
    I.current_user({})
    check("gehost zonder header geweigerd", "toegelaten", "NotSignedIn")
except I.NotSignedIn:
    check("gehost zonder header geweigerd", "NotSignedIn", "NotSignedIn")

# --- hosted with Easy Auth OFF: headers must not be trusted ---------------
I = reload_identity(WEBSITE_SITE_NAME="nieuws-pf", NIEUWS_AUTH_ACTIVE="false",
                    NIEUWS_ALLOWED_USERS="anna@example.com")
try:
    I.current_user(principal("anna@example.com"))
    check("vervalste header zonder Easy Auth geweigerd", "TOEGELATEN", "NotSignedIn")
except I.NotSignedIn:
    check("vervalste header zonder Easy Auth geweigerd", "NotSignedIn", "NotSignedIn")

# --- allow-list -----------------------------------------------------------
I = reload_identity(WEBSITE_SITE_NAME="nieuws-pf", NIEUWS_AUTH_ACTIVE="true",
                    NIEUWS_ALLOWED_USERS="anna@example.com, @werk.nl")
check("uitgenodigd adres mag erin",
      I.current_user(principal("anna@example.com"))["id"], "u:anna@example.com")
check("domein op de lijst mag erin",
      I.current_user(principal("piet@werk.nl"))["id"], "u:piet@werk.nl")
try:
    I.current_user(principal("vreemde@elders.com"))
    check("onbekend adres geweigerd", "TOEGELATEN", "AccessDenied")
except I.AccessDenied as exc:
    check("onbekend adres geweigerd", f"AccessDenied({exc.email})",
          "AccessDenied(vreemde@elders.com)")

# --- empty allow-list closes the door, not opens it -----------------------
I = reload_identity(WEBSITE_SITE_NAME="nieuws-pf", NIEUWS_AUTH_ACTIVE="true")
try:
    I.current_user(principal("anna@example.com"))
    check("lege gastenlijst laat niemand toe", "TOEGELATEN", "AccessDenied")
except I.AccessDenied:
    check("lege gastenlijst laat niemand toe", "AccessDenied", "AccessDenied")

# --- same person, two providers, one account ------------------------------
I = reload_identity(WEBSITE_SITE_NAME="nieuws-pf", NIEUWS_AUTH_ACTIVE="true",
                    NIEUWS_ALLOWED_USERS="anna@example.com")
via_ms = I.current_user(principal("anna@example.com", provider="aad"))["id"]
via_google = I.current_user(principal("anna@example.com", provider="google"))["id"]
check("Microsoft en Google geven hetzelfde account", via_ms == via_google, True)

check("hoofdletters maken niet uit",
      I.current_user(principal("ANNA@Example.com"))["id"], "u:anna@example.com")

# --- legacy owner ---------------------------------------------------------
I = reload_identity(WEBSITE_SITE_NAME="nieuws-pf", NIEUWS_AUTH_ACTIVE="true",
                    NIEUWS_ALLOWED_USERS="anna@example.com,bob@example.com",
                    NIEUWS_LEGACY_OWNER="anna@example.com")
check("eigenaar herkend",
      I.is_legacy_owner(I.current_user(principal("anna@example.com"))), True)
check("andere gebruiker is geen eigenaar",
      I.is_legacy_owner(I.current_user(principal("bob@example.com"))), False)

# --- malformed input must not crash ---------------------------------------
check("onzin-header levert geen principal",
      I.principal_from_headers({"x-ms-client-principal": "!!!niet-base64!!!"}), None)
check("lege headers leveren geen principal", I.principal_from_headers({}), None)

print(f"\n{ok}/{ok + fail} geslaagd")
sys.exit(1 if fail else 0)

