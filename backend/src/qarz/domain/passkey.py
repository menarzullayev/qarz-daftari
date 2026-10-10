"""An administrator's passkey (WebAuthn): what the server issues, and what it reads of a browser's answer.

A passkey is one more way to the session the Telegram sign-in gives. The server keeps a credential's
identifier and public key; the device keeps the private key and signs a challenge only after it has
verified its holder (a fingerprint, a face, a PIN) and only for the site the credential was made for.

Nothing here verifies a signature: that needs a cryptography library and lives in the infrastructure.
Here are the parts that are plain bytes: the challenge, the client data, the authenticator data.

Challenges are not stored. A challenge carries its own expiry and purpose and a MAC under the server's
key, so the server recognises one it issued; that an accepted answer is not accepted twice is the
caller's to see to.
"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

CHALLENGE_LIFE = timedelta(minutes=5)
SIGN_IN = b"g"
REGISTER = b"c"
# ES256, RS256 and EdDSA, by their COSE numbers: what a browser is asked to choose from.
ALGORITHMS = (-7, -257, -8)
MAX_CREDENTIAL_ID = 1023
_NONCE, _EXPIRY, _MAC = 16, 8, 16
_FLAG_PRESENT, _FLAG_VERIFIED, _FLAG_ATTESTED = 0x01, 0x04, 0x40


class Refused(ValueError):
    """The answer is not one this server accepts. The reason is for the log, never for the caller."""


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    if not text.isascii() or len(text) > 8192:
        raise Refused("not base64url")
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise Refused("not base64url") from error


def _mac(key: bytes, body: bytes, bound_to: bytes) -> bytes:
    return hmac.new(key, body + b"|" + bound_to, hashlib.sha256).digest()[:_MAC]


def new_challenge(key: bytes, purpose: bytes, now: datetime, bound_to: bytes = b"") -> bytes:
    """A challenge for one purpose, good for five minutes. `bound_to` ties it to a person, when there is one."""
    expires = int((now + CHALLENGE_LIFE).timestamp())
    body = purpose + secrets.token_bytes(_NONCE) + expires.to_bytes(_EXPIRY, "big")
    return body + _mac(key, body, bound_to)


def challenge_expiry(key: bytes, purpose: bytes, challenge: bytes, now: datetime, bound_to: bytes = b"") -> datetime:
    """When this challenge runs out, having checked it is ours, for this purpose and this person, and not over."""
    if len(challenge) != 1 + _NONCE + _EXPIRY + _MAC or challenge[:1] != purpose:
        raise Refused("not a challenge of this kind")
    body, mac = challenge[:-_MAC], challenge[-_MAC:]
    if not hmac.compare_digest(mac, _mac(key, body, bound_to)):
        raise Refused("not a challenge this server issued")
    expires = datetime.fromtimestamp(int.from_bytes(body[-_EXPIRY:], "big"), UTC)
    if now >= expires:
        raise Refused("the challenge is over")
    return expires


def check_client_data(raw: bytes, kind: str, origin: str) -> bytes:
    """The challenge the browser says it answered, when the client data is of this kind and from our page."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise Refused("client data is not JSON") from error
    if not isinstance(data, dict) or data.get("type") != kind:
        raise Refused("client data of another kind")
    if data.get("origin") != origin:
        raise Refused("client data from another origin")
    if data.get("crossOrigin") is True:
        raise Refused("client data from a frame of another origin")
    challenge = data.get("challenge")
    if not isinstance(challenge, str):
        raise Refused("client data without a challenge")
    return unb64(challenge)


@dataclass(frozen=True)
class AuthenticatorData:
    sign_count: int
    credential_id: bytes | None


def read_authenticator_data(raw: bytes, host: str) -> AuthenticatorData:
    """The counter, and the credential's identifier when one is attested, of data made for our site by
    an authenticator that saw its holder and verified them."""
    if len(raw) < 37:
        raise Refused("authenticator data too short")
    if not hmac.compare_digest(raw[:32], hashlib.sha256(host.encode("ascii")).digest()):
        raise Refused("authenticator data for another site")
    flags = raw[32]
    if not flags & _FLAG_PRESENT or not flags & _FLAG_VERIFIED:
        raise Refused("the holder was not verified")
    count = int.from_bytes(raw[33:37], "big")
    credential_id: bytes | None = None
    if flags & _FLAG_ATTESTED:
        if len(raw) < 55:
            raise Refused("attested data too short")
        length = int.from_bytes(raw[53:55], "big")
        if not 1 <= length <= MAX_CREDENTIAL_ID or len(raw) < 55 + length:
            raise Refused("credential identifier of a wrong length")
        credential_id = raw[55 : 55 + length]
    return AuthenticatorData(count, credential_id)


def counter_moved_on(stored: int, seen: int) -> bool:
    """A device that counts must count upwards: the same or a lower number is a copy of the key. A
    device that does not count (most synced passkeys) says zero every time."""
    return (stored == 0 and seen == 0) or seen > stored


def signed_message(authenticator_data: bytes, client_data: bytes) -> bytes:
    return authenticator_data + hashlib.sha256(client_data).digest()
