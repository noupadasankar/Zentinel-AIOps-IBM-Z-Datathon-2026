import hashlib
import hmac
import os
import threading
import time

from fastapi import HTTPException

ROLE_PERMISSIONS = {
    "admin": {"admin", "ingest", "read"},
    "ingest": {"ingest", "read"},
    "read": {"read"},
}


class TokenBucketRateLimiter:
    """Thread-safe in-memory token bucket rate limiter."""

    def __init__(self, rps: float = 100.0, burst: int = 200):
        self.rps = max(0.1, float(rps))
        self.burst = max(1, int(burst))
        self.buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_timestamp)
        self.lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(key, (float(self.burst), now))
            elapsed = now - last
            tokens = min(float(self.burst), tokens + elapsed * self.rps)

            if tokens < 1.0:
                self.buckets[key] = (tokens, now)
                return False

            self.buckets[key] = (tokens - 1.0, now)
            return True

    def reset(self):
        with self.lock:
            self.buckets.clear()


class SecurityManager:
    """Manages API Key authentication, role checks, and rate limiting."""

    def __init__(
        self,
        api_keys_config: str | None = None,
        rate_limit_rps: float = 100.0,
        rate_limit_burst: int = 200,
    ):
        self.api_keys_config = (
            api_keys_config if api_keys_config is not None else os.getenv("ZENTINEL_API_KEY_SHA256", "")
        )
        self.rate_limiter = TokenBucketRateLimiter(
            rps=rate_limit_rps,
            burst=rate_limit_burst,
        )
        self._key_map: dict[str, str] = {}  # sha256_hex -> role
        self._load_keys()

    def _load_keys(self):
        """Parse role:sha256 entries from configuration."""
        raw = self.api_keys_config.strip()
        if not raw:
            # When no keys configured, do not populate _key_map
            return

        entries = [item.strip() for item in raw.split(",") if item.strip()]
        for entry in entries:
            if ":" in entry:
                role, sha = entry.split(":", 1)
                role = role.strip().lower()
                sha = sha.strip().lower()
            else:
                role = "admin"
                sha = entry.strip().lower()

            if role not in ROLE_PERMISSIONS:
                role = "admin"
            self._key_map[sha] = role

    @property
    def auth_enabled(self) -> bool:
        return len(self._key_map) > 0

    def verify_api_key(
        self,
        api_key: str | None,
        required_role: str = "read",
    ) -> str:
        """
        Verify the provided API key against configured SHA-256 hashes using hmac.compare_digest.
        Returns the resolved role, or raises HTTPException on failure.
        """
        if not self.auth_enabled:
            # Auth not configured; open access in development
            return "admin"

        if not api_key:
            raise HTTPException(
                status_code=401,
                detail="Authentication required: Missing X-API-Key header",
            )

        provided_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest().lower()

        matched_role = None
        for stored_hash, role in self._key_map.items():
            if hmac.compare_digest(provided_hash, stored_hash):
                matched_role = role
                break

        if matched_role is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication failed: Invalid API key",
            )

        allowed_roles = ROLE_PERMISSIONS.get(matched_role, set())
        if required_role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Key role '{matched_role}' cannot perform '{required_role}' actions",
            )

        return matched_role

    def check_rate_limit(self, identifier: str) -> None:
        """Enforce rate limits per key or identifier."""
        if not self.rate_limiter.check(identifier):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded: Too many requests",
            )
