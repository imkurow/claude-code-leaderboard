"""Verify Cloudflare Access JWTs (RS256) with the standard library only.

Cloudflare Access adds `Cf-Access-Jwt-Assertion` to every request it lets through. We check the
signature against the team's public keys, the audience (the viewer app's AUD tag), issuer and expiry,
and only then trust the `email` claim.
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.request

# DER prefix of DigestInfo for SHA-256 (RFC 8017, EMSA-PKCS1-v1_5).
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64url(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class AccessVerifier:
    def __init__(self, team_domain, audience, ttl=3600):
        self.team = team_domain.strip().rstrip("/").replace("https://", "")
        self.aud = audience.strip()
        self.ttl = ttl
        self._keys = {}
        self._fetched = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.team and self.aud)

    def _load_keys(self, force=False):
        with self._lock:
            if not force and self._keys and time.time() - self._fetched < self.ttl:
                return self._keys
            if force and time.time() - self._fetched < 30:  # don't hammer the certs endpoint
                return self._keys
            url = "https://%s/cdn-cgi/access/certs" % self.team
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.load(resp)
            keys = {}
            for k in data.get("keys", []):
                if k.get("kty") == "RSA" and k.get("kid"):
                    keys[k["kid"]] = (int.from_bytes(_b64url(k["n"]), "big"), int.from_bytes(_b64url(k["e"]), "big"))
            self._keys, self._fetched = keys, time.time()
            return keys

    def verify_signature(self, token):
        """Return the claims if the RS256 signature checks out, else None (no claim checks)."""
        try:
            head_b64, body_b64, sig_b64 = token.split(".")
            header = json.loads(_b64url(head_b64))
            if header.get("alg") != "RS256":
                return None
            keys = self._load_keys()
            key = keys.get(header.get("kid"))
            if key is None:
                key = self._load_keys(force=True).get(header.get("kid"))
            if key is None:
                return None
            n, e = key
            k = (n.bit_length() + 7) // 8
            sig = int.from_bytes(_b64url(sig_b64), "big")
            if sig >= n:
                return None
            em = pow(sig, e, n).to_bytes(k, "big")
            digest = hashlib.sha256(("%s.%s" % (head_b64, body_b64)).encode("ascii")).digest()
            pad = k - 3 - len(_SHA256_PREFIX) - len(digest)
            if pad < 8:
                return None
            expected = b"\x00\x01" + b"\xff" * pad + b"\x00" + _SHA256_PREFIX + digest
            if not hmac.compare_digest(em, expected):
                return None
            return json.loads(_b64url(body_b64))
        except Exception:
            return None

    def email(self, token):
        """Email of the logged-in viewer, or None if the token is missing/invalid/for another app."""
        if not self.enabled or not token:
            return None
        claims = self.verify_signature(token)
        if not claims:
            return None
        now = time.time()
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if self.aud not in auds:
            return None
        if claims.get("iss") != "https://%s" % self.team:
            return None
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 30:
            return None
        if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now + 30:
            return None
        email = claims.get("email")
        if not isinstance(email, str) or "@" not in email:
            return None
        return email.strip().lower()
