import base64
import hashlib

import base58
from nacl.signing import VerifyKey, SigningKey
from nacl.exceptions import BadSignatureError
from nacl.encoding import Base64Encoder

from app.utils.exceptions import CryptoException

def verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    """
    Verify Ed25519 signature.
    """
    try:
        verify_key = VerifyKey(public_key_b64, encoder=Base64Encoder)
        verify_key.verify(message, base64.b64decode(signature_b64))
        return True
    except (BadSignatureError, ValueError) as e:
        raise CryptoException(f"Signature verification failed: {str(e)}")
    except Exception as e:
        raise CryptoException(f"Unexpected crypto error: {str(e)}")

def generate_keypair() -> tuple[str, str]:
    """
    Generate Ed25519 keypair.
    Returns: (public_key_b64, private_key_b64)
    """
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    
    private_key_b64 = signing_key.encode(encoder=Base64Encoder).decode('utf-8')
    public_key_b64 = verify_key.encode(encoder=Base64Encoder).decode('utf-8')
    
    return public_key_b64, private_key_b64

def get_pid_from_public_key(public_key_b64: str) -> str:
    """
    Derive PID from public key according to protocol spec.

    PID = base58(sha256(public_key_raw_bytes))
    """
    # Validate it's a valid key
    try:
        VerifyKey(public_key_b64, encoder=Base64Encoder)
        raw = base64.b64decode(public_key_b64)
        key_hash = hashlib.sha256(raw).digest()
        return base58.b58encode(key_hash).decode("utf-8")
    except Exception as e:
        raise CryptoException(f"Invalid public key: {str(e)}")