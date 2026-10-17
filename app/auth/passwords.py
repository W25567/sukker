from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Bruges ved login med ukendt e-mail, så svartiden ikke afslører, om kontoen findes.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password):
    return _hasher.hash(password)


def verify_password(password_hash, password):
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash):
    return _hasher.check_needs_rehash(password_hash)


def burn_verification_time(password):
    verify_password(_DUMMY_HASH, password)
