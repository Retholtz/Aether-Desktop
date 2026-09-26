"""
Aether Desktop - Security Subsystem
Provides AST code validation, DPAPI secret encryption, and application whitelisting.
"""

from security.crypto import protect_secret, unprotect_secret
from security.ast_gatekeeper import validate_python_script, validate_python_code, ASTSecurityGatekeeper
from security.whitelist import WhitelistValidator

__all__ = [
    "protect_secret",
    "unprotect_secret",
    "validate_python_script",
    "validate_python_code",
    "ASTSecurityGatekeeper",
    "WhitelistValidator",
]


