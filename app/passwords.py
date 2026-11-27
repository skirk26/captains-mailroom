# Password and username rules, shared by the sign-in screens, the admin page
# and the command line, so every way of setting a password applies the same ones.
import re
import secrets
from functools import lru_cache
from pathlib import Path

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # roomy enough for a password manager's longest output

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,80}$")
USERNAME_RULE = "Usernames are 3 to 80 letters, numbers, dots, dashes or underscores."

COMMON_PASSWORDS_FILE = Path(__file__).parent / "common_passwords.txt"

# No look-alike characters (0/O, 1/l/I), since a supervisor may read one aloud
TEMP_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TEMP_PASSWORD_LENGTH = 16


@lru_cache(maxsize=1)
def common_passwords():
    lines = COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip() and not line.startswith("#"))


def password_problems(password, username=""):
    """Return a list of plain-language problems; empty means the password is fine.

    Only length, the common-password list, and "contains the username" are
    checked. There are no forced symbols or digits.
    """
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    elif len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    if password.strip().lower() in common_passwords():
        problems.append("That password is too common. Pick something less guessable.")
    if username and username.lower() in password.lower():
        problems.append("The password can't contain the username.")
    return problems


def username_problem(username):
    return None if USERNAME_PATTERN.match(username) else USERNAME_RULE


def generate_temp_password(username=""):
    while True:
        password = "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))
        if not password_problems(password, username):
            return password
