"""
AES-256-GCM DECRYPTION for secrets encrypted by the Next.js app (see
src/lib/outreach/crypto.ts -- this module is that file's Python mirror, read
side only).

The agent never encrypts anything -- OutreachAccount.proxyPasswordEnc is
always written by the Next.js dashboard (src/lib/actions/outreach-accounts.ts)
when Hussein saves a proxy password, and only ever read+decrypted here, right
before handing it to Playwright's proxy config (see core/session.py's
build_proxy_config()).

Storage format, matching crypto.ts's encryptSecret() EXACTLY:

    "<iv_hex>:<authTag_hex>:<ciphertext_hex>"

  - IV: 12 bytes (GCM's recommended nonce length), hex-encoded.
  - Auth tag: 16 bytes, hex-encoded, produced/verified separately from the
    ciphertext (Node's createCipheriv/getAuthTag API keeps it out of the
    ciphertext bytes entirely -- there's no trailing-16-bytes-of-ciphertext
    convention here, it's a genuinely separate field). AESGCM from the
    `cryptography` package expects the tag appended to the ciphertext instead
    (that's its wire format for the combined "data" argument to decrypt()),
    so this module re-concatenates ciphertext + tag itself.
  - Ciphertext: hex-encoded.

Key: OUTREACH_ENCRYPTION_KEY, base64-encoded, must decode to exactly 32 bytes
(AES-256). Same env var name and same value as the Next.js app uses -- both
sides must share literally the same key or decryption fails outright (AEAD
authentication fails closed, never silently returns garbage).
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent import config

IV_LENGTH = 12  # bytes -- matches crypto.ts's IV_LENGTH


class DecryptionError(RuntimeError):
    """Raised when a proxy password can't be decrypted (bad key, malformed
    stored value, or tampered/corrupt ciphertext caught by GCM's auth tag)."""


def _load_key() -> bytes:
    raw = config.OUTREACH_ENCRYPTION_KEY
    if not raw:
        raise DecryptionError(
            "OUTREACH_ENCRYPTION_KEY is not set in agent/.env -- it must be the "
            "exact same value as the Next.js app's OUTREACH_ENCRYPTION_KEY."
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DecryptionError(f"OUTREACH_ENCRYPTION_KEY is not valid base64: {exc}") from exc
    if len(key) != 32:
        raise DecryptionError(
            f"OUTREACH_ENCRYPTION_KEY must decode to exactly 32 bytes for AES-256-GCM "
            f"(got {len(key)})."
        )
    return key


def decrypt_secret(stored: str) -> str | None:
    """
    Decrypt one value produced by crypto.ts's encryptSecret(). Mirrors that
    file's decryptSecret(): returns None instead of raising on ANY failure
    (missing key, malformed stored value, tampered ciphertext) -- callers
    (build_proxy_config()) treat "can't decrypt" the same as "nothing set"
    rather than crashing a whole account's session setup over it.
    """
    try:
        key = _load_key()
    except DecryptionError:
        return None

    parts = stored.split(":")
    if len(parts) != 3:
        return None
    iv_hex, auth_tag_hex, ciphertext_hex = parts

    try:
        iv = bytes.fromhex(iv_hex)
        auth_tag = bytes.fromhex(auth_tag_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
    except ValueError:
        return None

    if len(iv) != IV_LENGTH:
        return None

    try:
        aesgcm = AESGCM(key)
        # cryptography's AESGCM wants ciphertext + tag concatenated as one
        # buffer (its "data" argument) -- Node keeps them as two separate
        # values (cipher.update()/final() output, then a distinct
        # getAuthTag() call), so this re-joins them into the layout AESGCM
        # expects rather than mirroring Node's internal split.
        plaintext = aesgcm.decrypt(iv, ciphertext + auth_tag, None)
    except (InvalidTag, ValueError):
        return None

    return plaintext.decode("utf-8")
