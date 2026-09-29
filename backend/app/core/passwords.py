"""Temporary passwords and the password policy. Hashing stays werkzeug's
generate_password_hash / check_password_hash (the existing mechanism)."""

import secrets
import string

MIN_LENGTH = 8
MAX_LENGTH = 128
# No look-alike characters (0/O, 1/l/I) so a password read off the screen is typed correctly.
_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


def generate_temporary_password():
    """12 random characters in three groups ("Xk7m-Q2pa-9Tdz"), always with letters and digits."""
    while True:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
        if any(c.isdigit() for c in raw) and any(c.isalpha() for c in raw):
            return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def password_problems(new_password, *, current_password=None):
    """Issues with a new password chosen by the user (empty list = acceptable)."""
    if not isinstance(new_password, str):
        return ["must be a string"]
    problems = []
    if not MIN_LENGTH <= len(new_password) <= MAX_LENGTH:
        problems.append(f"must be {MIN_LENGTH}–{MAX_LENGTH} characters")
    if not (any(c.isalpha() for c in new_password) and any(c.isdigit() for c in new_password)):
        problems.append("must contain both letters and digits")
    if current_password is not None and new_password == current_password:
        problems.append("must differ from the current password")
    return problems
