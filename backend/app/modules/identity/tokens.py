import hashlib
import secrets


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
