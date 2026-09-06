import base64
import os
import sys

def is_windows() -> bool:
    return sys.platform == "win32"

def protect_secret(plaintext: str) -> str:
    """
    Encrypts a plaintext secret using Windows Data Protection API (DPAPI).
    The encrypted blob is user- and machine-bound, requiring no hardcoded keys.
    Returns a Base64-encoded ciphertext string.
    """
    if not plaintext:
        return ""
    
    if is_windows():
        try:
            import win32crypt
            raw_bytes = plaintext.encode("utf-8")
            encrypted = win32crypt.CryptProtectData(
                raw_bytes,
                "AetherDesktopSecret",
                None,
                None,
                None,
                0
            )
            return base64.b64encode(encrypted).decode("ascii")
        except Exception as e:
            print(f"[SECURITY WARNING] DPAPI encryption failed: {e}. Falling back to env.")
    
    # Fallback for non-windows or failure: base64
    return base64.b64encode(plaintext.encode("utf-8")).decode("ascii")

def unprotect_secret(ciphertext_b64: str) -> str:
    """
    Decrypts a Base64-encoded DPAPI ciphertext string.
    Returns the original plaintext.
    """
    if not ciphertext_b64:
        return os.environ.get("GEMINI_API_KEY", "")
    
    if is_windows():
        try:
            import win32crypt
            raw_bytes = base64.b64decode(ciphertext_b64.encode("ascii"))
            decrypted = win32crypt.CryptUnprotectData(
                raw_bytes,
                None,
                None,
                None,
                0
            )[1]
            return decrypted.decode("utf-8")
        except Exception as e:
            print(f"[SECURITY WARNING] DPAPI decryption failed: {e}")
            return os.environ.get("GEMINI_API_KEY", "")
            
    try:
        return base64.b64decode(ciphertext_b64.encode("ascii")).decode("utf-8")
    except Exception:
        return os.environ.get("GEMINI_API_KEY", "")

