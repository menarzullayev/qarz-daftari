"""Verifying a passkey's signature: the one part of WebAuthn that needs a cryptography library.

A public key is kept as the browser gives it (`getPublicKey()`: DER, SubjectPublicKeyInfo) with the COSE
number of its algorithm. Three algorithms are taken: ES256, RS256 and EdDSA.
"""

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key

ES256, RS256, EDDSA = -7, -257, -8
MAX_KEY_BYTES = 1024


def usable(public_key: bytes, algorithm: int) -> bool:
    """Whether these bytes are a public key of the kind this algorithm signs with."""
    if not 32 <= len(public_key) <= MAX_KEY_BYTES:
        return False
    try:
        key = load_der_public_key(public_key)
    except (ValueError, TypeError):
        return False
    if algorithm == ES256:
        return isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP256R1)
    if algorithm == RS256:
        return isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048
    if algorithm == EDDSA:
        return isinstance(key, ed25519.Ed25519PublicKey)
    return False


def verified(public_key: bytes, algorithm: int, signature: bytes, message: bytes) -> bool:
    """Whether `signature` is this key's over `message`. False for anything else, a broken key included."""
    if not usable(public_key, algorithm) or not signature or len(signature) > 1024:
        return False
    key = load_der_public_key(public_key)
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(signature, message)
        else:
            return False
    except InvalidSignature:
        return False
    return True
