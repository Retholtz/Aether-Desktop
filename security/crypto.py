"""
Aether Desktop - Security Cryptography & DPAPI Secret Management
Provides DPAPI encryption and decryption for user API keys on Windows.
"""

import base64
import os
import sys

def is_windows() -> bool:
    return sys.platform == "win32"

def protect_secret(plaintext: str) -> str:
    """
    Encrypts a plaintext secret using Windows DPAPI.
    Raises RuntimeError if DPAPI is unavailable or fails.
    """
    if not plaintext:
        return ""

    if sys.platform != "win32":
        raise RuntimeError("DPAPI encryption is only supported on Windows environments.")

    try:
        import win32crypt
        encrypted_bytes = win32crypt.CryptProtectData(
            plaintext.encode("utf-8"),
            "AetherSecureCredential",
            None,
            None,
            None,
            0
        )
        return "dpapi:" + base64.b64encode(encrypted_bytes).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"CryptProtectData failed: {e}") from e

def unprotect_secret(ciphertext: str) -> str:
    """
    Decrypts a DPAPI-encrypted secret.
    Raises RuntimeError if decryption fails.
    """
    if not ciphertext:
        return ""

    if not ciphertext.startswith("dpapi:"):
        raise ValueError("Invalid secret format: missing 'dpapi:' prefix.")

    if sys.platform != "win32":
        raise RuntimeError("DPAPI decryption is only supported on Windows environments.")

    try:
        import win32crypt
        raw_b64 = ciphertext[len("dpapi:"):]
        encrypted_bytes = base64.b64decode(raw_b64.encode("utf-8"))
        _, decrypted_bytes = win32crypt.CryptUnprotectData(
            encrypted_bytes,
            None,
            None,
            None,
            0
        )
        return decrypted_bytes.decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"CryptUnprotectData failed: {e}") from e


