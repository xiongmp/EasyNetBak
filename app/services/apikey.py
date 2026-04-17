import hashlib
import secrets
from typing import Tuple

def generate_api_key(prefix_length: int = 8, key_length: int = 32) -> Tuple[str, str, str]:
    """
    Generate a new API key.
    Returns a tuple of (plaintext_key, key_hash, prefix).
    """
    # Generate random key
    raw_key = secrets.token_urlsafe(key_length)
    plaintext_key = f"nb_{raw_key}"
    
    # Generate prefix for display
    prefix = plaintext_key[:prefix_length]
    
    # Generate hash for storage
    key_hash = hash_api_key(plaintext_key)
    
    return plaintext_key, key_hash, prefix

def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using SHA-256 for secure storage.
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

def verify_api_key(api_key: str, stored_hash: str) -> bool:
    """
    Verify an API key against a stored hash.
    """
    return secrets.compare_digest(hash_api_key(api_key), stored_hash)
