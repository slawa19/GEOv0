import base64
import hashlib

import base58

from app.core.auth.crypto import generate_keypair, get_pid_from_public_key


def test_pid_generation_matches_spec_sha256_base58():
    public_key_b64, _private_key_b64 = generate_keypair()

    pid = get_pid_from_public_key(public_key_b64)

    raw = base64.b64decode(public_key_b64)
    expected_hash = hashlib.sha256(raw).digest()
    decoded = base58.b58decode(pid)

    assert decoded == expected_hash


def test_pid_generation_is_deterministic():
    public_key_b64, _private_key_b64 = generate_keypair()
    assert get_pid_from_public_key(public_key_b64) == get_pid_from_public_key(public_key_b64)
