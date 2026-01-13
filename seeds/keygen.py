"""
Deterministic key generation for test seeds.

Usage:
    python seeds/keygen.py

Generates Ed25519 keypairs deterministically from a seed string.
Same seed + index always produces the same keypair.
"""
import hashlib
import base64
import json


def deterministic_keypair(seed: str, index: int) -> tuple[bytes, bytes, str]:
    """
    Generate Ed25519-like key material deterministically.
    
    For real Ed25519, use pynacl:
        from nacl.signing import SigningKey
        seed_bytes = hashlib.sha256(f"{seed}:{index}".encode()).digest()
        sk = SigningKey(seed_bytes)
        pk = sk.verify_key.encode()
    
    This simplified version creates reproducible test data.
    
    Returns:
        (private_key_bytes, public_key_bytes, pid_string)
    """
    material = f"{seed}:{index}".encode()
    
    # Derive 32-byte "private key" (seed for Ed25519)
    private_key = hashlib.sha256(material).digest()
    
    # Derive 32-byte "public key" (in real impl: Ed25519 point multiplication)
    # For testing, we just hash again
    public_key = hashlib.sha256(private_key).digest()
    
    # Generate PID from public key (base58 in real impl, simplified here)
    pid_hash = hashlib.sha256(public_key).hexdigest()[:16]
    
    return private_key, public_key, pid_hash


def generate_test_participants(seed: str = "2025-geo-test", count: int = 5):
    """Generate test participant data with deterministic keys."""
    names = ["Alice", "Bob", "Carol", "Dave", "Hub Admin"]
    types = ["person", "person", "person", "person", "business"]
    
    participants = []
    for i in range(count):
        private_key, public_key, pid_suffix = deterministic_keypair(seed, i)
        
        name = names[i] if i < len(names) else f"User_{i}"
        ptype = types[i] if i < len(types) else "person"
        
        # Create predictable PID
        pid_prefix = name.upper().replace(" ", "_")[:10]
        pid = f"PID_{pid_prefix}_{pid_suffix}"
        
        participants.append({
            "pid": pid,
            "display_name": f"{name} (Test)",
            "type": ptype,
            "status": "active",
            "public_key": base64.b64encode(public_key).decode(),
            "_private_key": base64.b64encode(private_key).decode(),
            "_seed_info": f"seed='{seed}' index={i}"
        })
    
    return participants


if __name__ == "__main__":
    seed = "2025-geo-test"
    participants = generate_test_participants(seed)
    
    print("=" * 60)
    print(f"Deterministic Test Keys (seed: '{seed}')")
    print("=" * 60)
    
    for p in participants:
        print(f"\n{p['display_name']}:")
        print(f"  PID: {p['pid']}")
        print(f"  Public Key: {p['public_key'][:32]}...")
        print(f"  Private Key: {p['_private_key'][:32]}... (KEEP SECRET)")
    
    print("\n" + "=" * 60)
    print("For pytest conftest.py:")
    print("=" * 60)
    print("""
# conftest.py
import hashlib
from nacl.signing import SigningKey

TEST_SEED = "2025-geo-test"

def get_test_keypair(index: int):
    material = f"{TEST_SEED}:{index}".encode()
    seed_bytes = hashlib.sha256(material).digest()
    sk = SigningKey(seed_bytes)
    return sk, sk.verify_key

# Usage:
# alice_sk, alice_vk = get_test_keypair(0)
# bob_sk, bob_vk = get_test_keypair(1)
""")
