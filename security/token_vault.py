"""
Aether Desktop - Hardware-Backed Encrypted OAuth Token Vault
Secures OAuth 2.0 access/refresh tokens, client secrets, and IMAP/SMTP app passwords
using Windows DPAPI (CryptProtectData with custom entropy and CRYPTPROTECT_UI_FORBIDDEN)
with Fernet fallback, persisted atomically in data/oauth_tokens.json.
"""

import base64
import json
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

from security.crypto import protect_secret as encrypt_secret, unprotect_secret as decrypt_secret

CRYPTPROTECT_UI_FORBIDDEN = 0x01
VAULT_ENTROPY = b"AetherDesktop_OAuthTokenVault_Entropy_2026_v1"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_VAULT_PATH = os.path.join(BASE_DIR, "data", "oauth_tokens.json")


def _parse_dotenv_fallback(file_path: str) -> None:
    """Lightweight fallback parser for .env files when python-dotenv is not installed."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = val
    except Exception:
        pass


def load_app_env() -> None:
    """
    Loads environment variables from .env or .env.local with support for
    local development, CI/CD, and PyInstaller bundled environments.
    """
    candidates = []
    # 1. PyInstaller frozen / bundle awareness
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, ".env"))
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, ".env.local"))
        candidates.append(os.path.join(exe_dir, ".env"))

    # 2. Local repository / working directory
    candidates.append(os.path.join(BASE_DIR, ".env.local"))
    candidates.append(os.path.join(BASE_DIR, ".env"))
    candidates.append(os.path.join(os.getcwd(), ".env.local"))
    candidates.append(os.path.join(os.getcwd(), ".env"))

    loaded = False
    try:
        from dotenv import load_dotenv
        for path in candidates:
            if os.path.isfile(path):
                load_dotenv(dotenv_path=path, override=False)
                loaded = True
    except ImportError:
        pass

    if not loaded:
        for path in candidates:
            if os.path.isfile(path):
                _parse_dotenv_fallback(path)


# Pre-seed environment from .env / .env.local before reading credentials
load_app_env()

# Default Application OAuth 2.0 Credentials for Aether Desktop (loaded from environment or .env).
# In production, secrets are injected via .env, environment variables, or CI/CD build scripts.
DEFAULT_GOOGLE_CLIENT_ID = os.getenv("AETHER_GOOGLE_CLIENT_ID", "").strip()
DEFAULT_GOOGLE_CLIENT_SECRET = os.getenv("AETHER_GOOGLE_CLIENT_SECRET", "").strip()
DEFAULT_MICROSOFT_CLIENT_ID = os.getenv(
    "AETHER_MICROSOFT_CLIENT_ID",
    "5242daeb-e095-43c7-9979-57694e6983aa",
).strip()
DEFAULT_MICROSOFT_TENANT_ID = os.getenv("AETHER_MICROSOFT_TENANT_ID", "consumers").strip() or "consumers"

DEFAULT_CLIENT_IDS = {
    "google": DEFAULT_GOOGLE_CLIENT_ID,
    "microsoft": DEFAULT_MICROSOFT_CLIENT_ID,
}
DEFAULT_CLIENT_SECRETS = {
    "google": DEFAULT_GOOGLE_CLIENT_SECRET,
    "microsoft": "",
}


def _dpapi_encrypt(plaintext: str) -> str:
    """Encrypts a string using Windows DPAPI tied to the current Windows user session."""
    if not plaintext:
        return ""
    if sys.platform == "win32":
        try:
            import win32crypt
            raw_bytes = plaintext.encode("utf-8")
            encrypted_blob = win32crypt.CryptProtectData(
                raw_bytes,
                "AetherOAuthVault",
                VAULT_ENTROPY,
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
            )
            return "dpapi:" + base64.b64encode(encrypted_blob).decode("ascii")
        except Exception:
            pass
    return "fernet:" + encrypt_secret(plaintext)


def _dpapi_decrypt(ciphertext: str) -> str:
    """Decrypts a string encrypted by _dpapi_encrypt (supports DPAPI and Fernet fallback)."""
    if not ciphertext:
        return ""
    if ciphertext.startswith("dpapi:"):
        if sys.platform == "win32":
            try:
                import win32crypt
                blob = base64.b64decode(ciphertext[len("dpapi:"):].encode("ascii"))
                _, decrypted_bytes = win32crypt.CryptUnprotectData(
                    blob,
                    VAULT_ENTROPY,
                    None,
                    None,
                    CRYPTPROTECT_UI_FORBIDDEN,
                )
                return decrypted_bytes.decode("utf-8")
            except Exception as e:
                print(f"[ERROR] [TOKEN_VAULT] DPAPI decryption failed: {e}")
                return ""
        return ""
    if ciphertext.startswith("fernet:"):
        return decrypt_secret(ciphertext[len("fernet:"):])
    return decrypt_secret(ciphertext)


class OAuthTokenVault:
    """
    Thread-safe, DPAPI-encrypted storage for OAuth 2.0 tokens and provider client configs.
    Isolated in data/oauth_tokens.json and blocked from child script sandboxes by ASTSecurityGatekeeper.
    """

    SUPPORTED_PROVIDERS = ("google", "microsoft", "imap")

    def __init__(self, vault_path: str = DEFAULT_VAULT_PATH):
        self.vault_path = vault_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.vault_path), exist_ok=True)

    def _read_raw_vault(self) -> Dict[str, Any]:
        with self._lock:
            if not os.path.exists(self.vault_path):
                return {"providers": {}, "client_configs": {}}
            try:
                with open(self.vault_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return {"providers": {}, "client_configs": {}}
                data.setdefault("providers", {})
                data.setdefault("client_configs", {})
                return data
            except Exception as e:
                print(f"[WARN] [TOKEN_VAULT] Failed reading vault {self.vault_path}: {e}")
                return {"providers": {}, "client_configs": {}}

    def _write_raw_vault(self, data: Dict[str, Any]) -> bool:
        with self._lock:
            os.makedirs(os.path.dirname(self.vault_path), exist_ok=True)
            tmp_path = f"{self.vault_path}.{os.getpid()}.tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, self.vault_path)
                return True
            except Exception as e:
                print(f"[ERROR] [TOKEN_VAULT] Failed saving vault {self.vault_path}: {e}")
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                return False

    def save_client_config(self, provider: str, client_config: Dict[str, Any]) -> bool:
        """
        Saves OAuth client configuration (e.g., client_id, optional client_secret, tenant_id,
        or IMAP/SMTP host/port settings) with sensitive secrets encrypted via DPAPI.
        """
        provider_key = provider.lower().strip()
        with self._lock:
            vault = self._read_raw_vault()
            existing = vault["client_configs"].get(provider_key, {})

            client_id = str(client_config.get("client_id", existing.get("client_id", ""))).strip()
            tenant_id = str(client_config.get("tenant_id", existing.get("tenant_id", DEFAULT_MICROSOFT_TENANT_ID))).strip() or DEFAULT_MICROSOFT_TENANT_ID

            record: Dict[str, Any] = {
                "client_id": client_id,
                "tenant_id": tenant_id,
                "updated_at": time.time(),
            }

            if "client_secret" in client_config:
                raw_secret = str(client_config.get("client_secret") or "").strip()
                if raw_secret:
                    record["client_secret_enc"] = _dpapi_encrypt(raw_secret)
                elif client_config.get("clear_secret"):
                    record["client_secret_enc"] = ""
                else:
                    record["client_secret_enc"] = existing.get("client_secret_enc", "")
            else:
                record["client_secret_enc"] = existing.get("client_secret_enc", "")

            # Optional IMAP / SMTP settings if provider == "imap"
            if provider_key == "imap":
                record["imap_host"] = str(client_config.get("imap_host", existing.get("imap_host", ""))).strip()
                record["imap_port"] = int(client_config.get("imap_port", existing.get("imap_port", 993)) or 993)
                record["smtp_host"] = str(client_config.get("smtp_host", existing.get("smtp_host", ""))).strip()
                record["smtp_port"] = int(client_config.get("smtp_port", existing.get("smtp_port", 587)) or 587)
                record["email_address"] = str(client_config.get("email_address", existing.get("email_address", ""))).strip()

            vault["client_configs"][provider_key] = record
            return self._write_raw_vault(vault)

    def get_client_config(self, provider: str) -> Dict[str, Any]:
        """Retrieves decrypted client configuration for the given provider, falling back to built-in app defaults."""
        provider_key = provider.lower().strip()
        with self._lock:
            vault = self._read_raw_vault()
            raw = dict(vault.get("client_configs", {}).get(provider_key, {}))
            secret_enc = raw.pop("client_secret_enc", "")
            raw["client_secret"] = _dpapi_decrypt(secret_enc) if secret_enc else ""
            if not raw.get("client_id"):
                raw["client_id"] = (
                    os.getenv(f"AETHER_{provider_key.upper()}_CLIENT_ID", "").strip()
                    or DEFAULT_CLIENT_IDS.get(provider_key, "")
                )
            if not raw.get("client_secret"):
                raw["client_secret"] = (
                    os.getenv(f"AETHER_{provider_key.upper()}_CLIENT_SECRET", "").strip()
                    or DEFAULT_CLIENT_SECRETS.get(provider_key, "")
                )
            raw.setdefault("tenant_id", DEFAULT_MICROSOFT_TENANT_ID if provider_key == "microsoft" else "common")
            return raw

    def save_provider_tokens(self, provider: str, token_data: Dict[str, Any]) -> bool:
        """
        Encrypts and saves OAuth 2.0 tokens (or IMAP app password) for the given provider.
        Preserves existing refresh_token if a token refresh response omits it.
        """
        provider_key = provider.lower().strip()
        with self._lock:
            vault = self._read_raw_vault()
            existing = vault["providers"].get(provider_key, {})

            access_token = str(token_data.get("access_token") or "").strip()
            refresh_token = str(token_data.get("refresh_token") or "").strip()
            app_password = str(token_data.get("app_password") or "").strip()

            # Preserve existing refresh token if not rotated
            if not refresh_token and existing.get("refresh_token_enc"):
                refresh_token = _dpapi_decrypt(existing["refresh_token_enc"])

            expires_in = token_data.get("expires_in")
            expires_at = token_data.get("expires_at")
            if expires_at is None and expires_in is not None:
                try:
                    expires_at = time.time() + float(expires_in)
                except Exception:
                    expires_at = time.time() + 3600.0

            scopes = token_data.get("scopes") or existing.get("scopes") or []
            if isinstance(scopes, str):
                scopes = [s for s in scopes.split() if s]

            record = {
                "provider": provider_key,
                "account_email": str(token_data.get("account_email") or existing.get("account_email") or "").strip(),
                "account_name": str(token_data.get("account_name") or existing.get("account_name") or "").strip(),
                "access_token_enc": _dpapi_encrypt(access_token) if access_token else existing.get("access_token_enc", ""),
                "refresh_token_enc": _dpapi_encrypt(refresh_token) if refresh_token else existing.get("refresh_token_enc", ""),
                "app_password_enc": _dpapi_encrypt(app_password) if app_password else existing.get("app_password_enc", ""),
                "expires_at": float(expires_at) if expires_at is not None else existing.get("expires_at", 0.0),
                "scopes": list(scopes),
                "imap_host": str(token_data.get("imap_host") or existing.get("imap_host") or "").strip(),
                "imap_port": int(token_data.get("imap_port") or existing.get("imap_port") or 993),
                "smtp_host": str(token_data.get("smtp_host") or existing.get("smtp_host") or "").strip(),
                "smtp_port": int(token_data.get("smtp_port") or existing.get("smtp_port") or 587),
                "updated_at": time.time(),
            }

            vault["providers"][provider_key] = record
            return self._write_raw_vault(vault)

    def get_provider_tokens(self, provider: str) -> Optional[Dict[str, Any]]:
        """Returns decrypted token record for a provider, or None if not connected."""
        provider_key = provider.lower().strip()
        with self._lock:
            vault = self._read_raw_vault()
            raw = vault.get("providers", {}).get(provider_key)
            if not raw:
                return None

            access_token = _dpapi_decrypt(raw.get("access_token_enc", ""))
            refresh_token = _dpapi_decrypt(raw.get("refresh_token_enc", ""))
            app_password = _dpapi_decrypt(raw.get("app_password_enc", ""))

            if not access_token and not refresh_token and not app_password:
                return None

            return {
                "provider": provider_key,
                "account_email": raw.get("account_email", ""),
                "account_name": raw.get("account_name", ""),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "app_password": app_password,
                "expires_at": float(raw.get("expires_at", 0.0) or 0.0),
                "scopes": list(raw.get("scopes", [])),
                "imap_host": raw.get("imap_host", ""),
                "imap_port": int(raw.get("imap_port", 993) or 993),
                "smtp_host": raw.get("smtp_host", ""),
                "smtp_port": int(raw.get("smtp_port", 587) or 587),
                "updated_at": raw.get("updated_at", 0.0),
            }

    def delete_provider_tokens(self, provider: str) -> bool:
        """Removes stored tokens for the specified provider."""
        provider_key = provider.lower().strip()
        with self._lock:
            vault = self._read_raw_vault()
            if provider_key in vault.get("providers", {}):
                del vault["providers"][provider_key]
                return self._write_raw_vault(vault)
            return True

    def get_all_provider_statuses(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns safe, non-sensitive connection metadata for all supported providers
        suitable for broadcasting to the Settings UI.
        """
        with self._lock:
            vault = self._read_raw_vault()
            providers_raw = vault.get("providers", {})
            configs_raw = vault.get("client_configs", {})

            statuses: Dict[str, Dict[str, Any]] = {}
            for prov in self.SUPPORTED_PROVIDERS:
                p_rec = providers_raw.get(prov, {})
                c_rec = configs_raw.get(prov, {})

                has_tokens = bool(
                    p_rec.get("access_token_enc")
                    or p_rec.get("refresh_token_enc")
                    or p_rec.get("app_password_enc")
                )
                custom_client_id = c_rec.get("client_id", "")
                effective_client_id = custom_client_id or DEFAULT_CLIENT_IDS.get(prov, "")
                client_id_preview = (
                    f"{effective_client_id[:12]}...{effective_client_id[-6:]}"
                    if len(effective_client_id) > 20
                    else effective_client_id
                )

                statuses[prov] = {
                    "provider": prov,
                    "connected": has_tokens,
                    "account_email": p_rec.get("account_email", "") if has_tokens else "",
                    "account_name": p_rec.get("account_name", "") if has_tokens else "",
                    "scopes": p_rec.get("scopes", []) if has_tokens else [],
                    "expires_at": p_rec.get("expires_at", 0.0) if has_tokens else 0.0,
                    "updated_at": p_rec.get("updated_at", 0.0) if has_tokens else 0.0,
                    "has_client_id": bool(effective_client_id),
                    "using_builtin_client_id": bool(not custom_client_id and DEFAULT_CLIENT_IDS.get(prov)),
                    "client_id": custom_client_id,
                    "client_id_preview": client_id_preview,
                    "has_client_secret": bool(c_rec.get("client_secret_enc") or DEFAULT_CLIENT_SECRETS.get(prov)),
                    "tenant_id": c_rec.get("tenant_id", DEFAULT_MICROSOFT_TENANT_ID),
                    "imap_host": p_rec.get("imap_host") or c_rec.get("imap_host", ""),
                    "imap_port": p_rec.get("imap_port") or c_rec.get("imap_port", 993),
                    "smtp_host": p_rec.get("smtp_host") or c_rec.get("smtp_host", ""),
                    "smtp_port": p_rec.get("smtp_port") or c_rec.get("smtp_port", 587),
                }
            return statuses
