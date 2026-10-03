"""Encrypts (gzip + Fernet/AES) the lead database so it can be stored in git safely, even in a public repo.

data/leads.db  <->  data/leads.db.enc   (Fernet / AES, key from LEADMACHINE_DB_KEY passphrase)
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet

from .config import env

SALT = b"corygroup-leadmachine-v1"


def _fernet(passphrase: str | None = None) -> Fernet:
    passphrase = passphrase or env("LEADMACHINE_DB_KEY")
    if not passphrase:
        raise SystemExit("LEADMACHINE_DB_KEY is not set")
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), SALT, 390_000)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(src: Path, dst: Path, passphrase: str | None = None) -> None:
    dst.write_bytes(_fernet(passphrase).encrypt(gzip.compress(src.read_bytes())))


def decrypt(src: Path, dst: Path, passphrase: str | None = None) -> None:
    dst.write_bytes(gzip.decompress(_fernet(passphrase).decrypt(src.read_bytes())))
