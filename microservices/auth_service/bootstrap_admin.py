from __future__ import annotations

import getpass
import sys

from microservices.auth_service.auth import (
    AdminAlreadyExistsError,
    bootstrap_admin_user,
    normalize_username,
)

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 80
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200


def main() -> int:
    username = normalize_username(input("Administrator username: "))
    if not USERNAME_MIN_LENGTH <= len(username) <= USERNAME_MAX_LENGTH:
        print("Username must contain between 3 and 80 characters.", file=sys.stderr)
        return 2

    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm administrator password: ")

    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        print("Password must contain between 8 and 200 characters.", file=sys.stderr)
        return 2
    if password != confirmation:
        print("Password confirmation does not match.", file=sys.stderr)
        return 2

    try:
        bootstrap_admin_user(username, password)
    except AdminAlreadyExistsError:
        print("An administrator account already exists.", file=sys.stderr)
        return 3
    except Exception:
        print("Administrator bootstrap failed.", file=sys.stderr)
        return 1

    print("Administrator account created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
