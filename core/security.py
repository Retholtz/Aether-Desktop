"""
Aether Desktop - Core Security Proxy
Re-exports DPAPI encryption from the modular security package.
"""

from security.crypto import is_windows, protect_secret, unprotect_secret

__all__ = ["is_windows", "protect_secret", "unprotect_secret"]
