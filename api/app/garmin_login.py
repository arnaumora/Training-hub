"""One-time Garmin Connect login for the sync. Run inside the garmin container, interactively:

    docker exec -it traininghub-garmin python -m app.garmin_login            (use saved tokens if they work)
    docker exec -it traininghub-garmin python -m app.garmin_login --fresh    (forget them and log in again)

Asks for the e-mail and password (the password is typed hidden), and for the 2FA code if Garmin asks for one.
The password is used for this one login and never written anywhere; only the tokens Garmin returns are
saved, in the token volume (GARMIN_TOKENS, mode 0600). On success it queues a sync, which starts within
30 seconds.

If Garmin answers "too many requests", wait 15 to 30 minutes before trying again: repeated attempts make
the block longer.
"""

import getpass
import sys
from pathlib import Path

import psycopg

from .common import conninfo
from .garmin_sync import TOKEN_FILE, token_dir


def main(argv: list[str]) -> int:
    from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

    folder = token_dir()
    token = Path(folder) / TOKEN_FILE
    if "--fresh" in argv and token.exists():
        token.unlink()
        print("Forgot the saved Garmin tokens.")

    if token.exists():
        print("Trying the saved tokens first...")
        client = Garmin()
        try:
            client.login(folder)
            print(f"The saved tokens work (account: {client.get_full_name() or 'unknown name'}). Nothing to do.")
            print("To log in again anyway: add --fresh.")
            return 0
        except Exception as e:
            print(f"The saved tokens do not work ({' '.join(str(e).split())[:120]}). Logging in again.")

    print("Garmin Connect login for Training Hub. The password is used for this login only and is never stored.")
    email = input("Garmin e-mail: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")
    client = Garmin(email, password, prompt_mfa=lambda: input("Code from Garmin (2FA): ").strip())
    del password
    try:
        client.login(folder)
    except GarminConnectTooManyRequestsError:
        print("Garmin says: too many login attempts. Wait 15 to 30 minutes before trying again; every try now extends the block.")
        return 1
    except GarminConnectAuthenticationError as e:
        print(f"Login failed: {' '.join(str(e).split())[:300]}")
        return 1
    if not token.exists():
        print(f"Logged in, but no token file appeared in {folder}. The sync will not work: check the volume permissions.")
        return 1
    print(f"Logged in as {client.get_full_name() or 'your account'}. Tokens saved in {folder}.")

    with psycopg.connect(conninfo()) as conn:
        conn.execute("INSERT INTO garmin_sync_run (trigger) VALUES ('request')")
    print("A sync is queued and starts within 30 seconds. Watch it with: docker logs -f traininghub-garmin")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
