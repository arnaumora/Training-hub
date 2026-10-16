"""Create the password hash file.

Usage (prompts twice, never echoes the password):
    python -m app.set_password /path/to/app_password_hash
"""

import getpass
import sys

from .auth import hash_password

MIN_LENGTH = 12


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m app.set_password <output file>", file=sys.stderr)
        return 2
    out = sys.argv[1]
    password = getpass.getpass("New Training Hub password: ")
    if len(password) < MIN_LENGTH:
        print(f"Too short: use at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat it: ") != password:
        print("The two entries do not match. Nothing written.", file=sys.stderr)
        return 1
    with open(out, "w", encoding="ascii") as f:
        f.write(hash_password(password) + "\n")
    print(f"Hash written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
