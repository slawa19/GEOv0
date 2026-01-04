import base64
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
    Derive PID from public key.
    For MVP, PID is derived from the public key but made URL-safe.
    We use base64url without padding so it can safely appear in paths/query params.
    """
    # Validate it's a valid key
    try:
        VerifyKey(public_key_b64, encoder=Base64Encoder)
        raw = base64.b64decode(public_key_b64)
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    except Exception as e:
        raise CryptoException(f"Invalid public key: {str(e)}")