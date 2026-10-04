"""Manage who may sign in.

    py -3.12 tools_users.py                      # show the guest list
    py -3.12 tools_users.py add iemand@gmail.com
    py -3.12 tools_users.py remove iemand@gmail.com

Changing an app setting restarts the app, so a change takes about a minute to
take effect.
"""
import subprocess
import sys

SITE = "nieuws-pf"
GROUP = "rg-newsfeed"
SUB = "4085d427-d6a9-4f39-a61a-c9fbf24d3a9c"
KEY = "NIEUWS_ALLOWED_USERS"


def az(*args: str) -> str:
    r = subprocess.run(["az", *args], capture_output=True, text=True, shell=True)
    if r.returncode:
        raise SystemExit(f"az {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def current() -> list[str]:
    raw = az("webapp", "config", "appsettings", "list", "-n", SITE, "-g", GROUP,
             "--subscription", SUB, "--query",
             f"[?name=='{KEY}'].value", "-o", "tsv")
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def write(users: list[str]) -> None:
    az("webapp", "config", "appsettings", "set", "-n", SITE, "-g", GROUP,
       "--subscription", SUB, "--settings", f"{KEY}={','.join(users)}", "-o", "none")


def main() -> None:
    users = current()
    if len(sys.argv) == 1:
        print(f"{len(users)} toegelaten:")
        for u in users:
            print("  ", u)
        print("\n(een regel die met @ begint laat een heel domein toe)")
        return

    cmd = sys.argv[1].lower()
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    who = sys.argv[2].strip().lower()

    if cmd == "add":
        if who in users:
            print(f"{who} staat er al op")
            return
        write(users + [who])
        print(f"{who} toegevoegd ({len(users) + 1} toegelaten)")
    elif cmd == "remove":
        if who not in users:
            print(f"{who} staat er niet op")
            return
        left = [u for u in users if u != who]
        if not left:
            # An empty list locks everyone out, including the owner. Refuse
            # rather than leave the app unreachable.
            raise SystemExit("weigering: dit zou de laatste gebruiker verwijderen "
                             "en iedereen buitensluiten")
        write(left)
        print(f"{who} verwijderd ({len(left)} over)")
    else:
        raise SystemExit(__doc__)

    print("de app herstart; na ongeveer een minuut is het actief")


if __name__ == "__main__":
    main()
