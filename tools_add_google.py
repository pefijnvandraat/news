"""Add Google sign-in to the App Service Easy Auth configuration.

Google cannot be set up without a Google Cloud project, which needs a Google
account - so the credentials have to come from the owner. This script takes
them and wires the provider in, leaving the Microsoft provider untouched.

    py -3.12 tools_add_google.py <client-id> <client-secret>

Recipe for the two values (about five minutes, free):

  1. https://console.cloud.google.com/  ->  new project, any name
  2. "APIs & Services" -> "OAuth consent screen"
       User type: External. Fill in app name and your own e-mail.
       Publishing status may stay "Testing"; add each reader as a test user.
  3. "Credentials" -> "Create credentials" -> "OAuth client ID"
       Application type: Web application
       Authorised redirect URI, exactly:
         https://nieuws-pf.azurewebsites.net/.auth/login/google/callback
  4. Copy the client ID and client secret, then run this script.
"""
import json
import subprocess
import sys

SITE = "nieuws-pf"
GROUP = "rg-newsfeed"
SUB = "4085d427-d6a9-4f39-a61a-c9fbf24d3a9c"
SECRET_SETTING = "GOOGLE_PROVIDER_AUTHENTICATION_SECRET"


def az(*args: str) -> str:
    r = subprocess.run(["az", *args], capture_output=True, text=True, shell=True)
    if r.returncode:
        raise SystemExit(f"az {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    client_id, client_secret = sys.argv[1].strip(), sys.argv[2].strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        print(f"waarschuwing: '{client_id}' ziet er niet uit als een Google client-id")

    print("1/3 geheim opslaan als app setting...")
    az("webapp", "config", "appsettings", "set", "-n", SITE, "-g", GROUP,
       "--subscription", SUB, "--settings", f"{SECRET_SETTING}={client_secret}",
       "-o", "none")

    print("2/3 huidige auth-configuratie ophalen...")
    url = (f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{GROUP}"
           f"/providers/Microsoft.Web/sites/{SITE}/config/authsettingsV2"
           "?api-version=2023-12-01")
    current = json.loads(az("rest", "--method", "get", "--uri", url))
    props = current["properties"]

    # Only touch the Google provider; the Microsoft one keeps working as it is.
    props.setdefault("identityProviders", {})["google"] = {
        "enabled": True,
        "registration": {
            "clientId": client_id,
            "clientSecretSettingName": SECRET_SETTING,
        },
        "login": {"scopes": ["openid", "profile", "email"]},
    }
    # With two providers there is no single obvious one to send people to, so
    # show the chooser instead of jumping straight to Microsoft.
    gv = props.setdefault("globalValidation", {})
    gv.pop("redirectToProvider", None)
    gv["unauthenticatedClientAction"] = "RedirectToLoginPage"

    with open("deploy/_google_auth.json", "w", encoding="utf-8") as fh:
        json.dump({"properties": props}, fh, indent=2)

    print("3/3 configuratie wegschrijven...")
    az("rest", "--method", "put", "--uri", url,
       "--body", "@deploy/_google_auth.json",
       "--headers", "Content-Type=application/json", "-o", "none")

    print("\nGoogle-aanmelding staat aan.")
    print("Vergeet niet de Google-adressen aan NIEUWS_ALLOWED_USERS toe te voegen:")
    print("  az webapp config appsettings set -n", SITE, "-g", GROUP,
          '--settings "NIEUWS_ALLOWED_USERS=...,iemand@gmail.com"')


if __name__ == "__main__":
    main()
