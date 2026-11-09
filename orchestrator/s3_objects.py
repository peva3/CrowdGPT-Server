"""S3-compatible presigned object storage without a mandatory SDK.

The store only needs four operations: issue a short-lived PUT grant, HEAD an
object, hash an object before finalize, and delete it. AWS SigV4 query
presigning is implemented here so the orchestrator image does not need boto3.

Configure with:

    CROWDGPT_OBJECT_BACKEND=s3
    CROWDGPT_S3_BUCKET=...
    CROWDGPT_S3_ENDPOINT=https://...        # omit for AWS
    CROWDGPT_S3_REGION=auto                 # R2 commonly uses "auto"
    CROWDGPT_S3_ACCESS_KEY=...
    CROWDGPT_S3_SECRET_KEY=...
    CROWDGPT_S3_PREFIX=deltas

For tests, point ``CROWDGPT_S3_ENDPOINT`` at a local fake server.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode, urlsplit

import requests

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
UNSIGNED = "UNSIGNED-PAYLOAD"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


@dataclass(frozen=True)
class S3Grant:
    key: str
    url: str
    method: str
    headers: dict
    expires_at: float
    sha256: str

    def as_dict(self) -> dict:
        return {"key": self.key, "url": self.url, "method": self.method,
                "headers": dict(self.headers), "expiresAt": self.expires_at,
                "sha256": self.sha256}


class S3ObjectStore:
    def __init__(self, *, bucket: str, region: str, access_key: str,
                 secret_key: str, endpoint: Optional[str] = None,
                 prefix: str = "deltas", url_base: Optional[str] = None,
                 timeout: float = 30.0):
        if not bucket or not access_key or not secret_key:
            raise ValueError("bucket, access_key, and secret_key are required")
        self.bucket = bucket
        self.region = region or "us-east-1"
        self.access_key = access_key
        self.secret_key = secret_key
        self.prefix = prefix.strip("/")
        self.endpoint = (endpoint or f"https://{bucket}.s3.{self.region}.amazonaws.com").rstrip("/")
        self.url_base = (url_base or self.endpoint).rstrip("/")
        self.timeout = float(timeout)

    @classmethod
    def from_env(cls) -> Optional["S3ObjectStore"]:
        if os.getenv("CROWDGPT_OBJECT_BACKEND", "local").strip().lower() != "s3":
            return None
        return cls(
            bucket=os.getenv("CROWDGPT_S3_BUCKET", ""),
            region=os.getenv("CROWDGPT_S3_REGION", "auto"),
            access_key=os.getenv("CROWDGPT_S3_ACCESS_KEY", ""),
            secret_key=os.getenv("CROWDGPT_S3_SECRET_KEY", ""),
            endpoint=os.getenv("CROWDGPT_S3_ENDPOINT") or None,
            prefix=os.getenv("CROWDGPT_S3_PREFIX", "deltas"),
            timeout=float(os.getenv("CROWDGPT_S3_TIMEOUT_S", "30")),
        )

    def full_key(self, key: str) -> str:
        key = str(key).strip().lstrip("/")
        if not key or ".." in Path(key).parts:
            raise ValueError("invalid object key")
        return f"{self.prefix}/{key}" if self.prefix else key

    def _canonical_path(self, full_key: str) -> str:
        # Path-style URL keeps the bucket in the path, which is what most
        # S3-compatible test doubles and R2 endpoints expect.
        return "/" + quote(full_key, safe="/-_.~")

    def _signing_key(self, date: str) -> bytes:
        k_date = _hmac(("AWS4" + self.secret_key).encode(), date)
        k_region = _hmac(k_date, self.region)
        k_service = _hmac(k_region, "s3")
        return _hmac(k_service, "aws4_request")

    def presign(self, method: str, key: str, *, expires_s: int = 900,
                payload_hash: str = UNSIGNED) -> S3Grant:
        full_key = self.full_key(key)
        now = time.time()
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
        date_stamp = amz_date[:8]
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        host = urlsplit(self.url_base).netloc
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(int(expires_s)),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = urlencode(sorted(params.items()), quote_via=quote)
        canonical_request = "\n".join([
            method.upper(), self._canonical_path(full_key), canonical_query,
            f"host:{host}\n", "host", payload_hash,
        ])
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])
        signature = hmac.new(self._signing_key(date_stamp),
                             string_to_sign.encode(), hashlib.sha256).hexdigest()
        query = canonical_query + "&X-Amz-Signature=" + signature
        expires_at = now + int(expires_s)
        return S3Grant(key=full_key, url=f"{self.url_base}{self._canonical_path(full_key)}?{query}",
                       method=method.upper(), headers={}, expires_at=expires_at,
                       sha256=payload_hash)

    def grant_put(self, key: str, sha256: str, *, expires_s: int = 900,
                  base_url: Optional[str] = None) -> S3Grant:
        sha = str(sha256).lower()
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        grant = self.presign("PUT", key, expires_s=expires_s, payload_hash=sha)
        if base_url:
            path = self._canonical_path(grant.key)
            query = urlsplit(grant.url).query
            grant = S3Grant(grant.key, f"{base_url.rstrip('/')}{path}?{query}",
                            grant.method, grant.headers, grant.expires_at, grant.sha256)
        return grant

    def head(self, key: str) -> Optional[dict]:
        grant = self.presign("HEAD", key, expires_s=60, payload_hash=EMPTY_SHA256)
        try:
            r = requests.head(grant.url, timeout=self.timeout, allow_redirects=True)
            if not r.ok:
                return None
            return {"key": grant.key, "size": int(r.headers.get("content-length") or 0),
                    "etag": r.headers.get("etag")}
        except requests.RequestException:
            return None

    def verify_file(self, key: str, expected_sha256: str) -> bool:
        grant = self.presign("GET", key, expires_s=60, payload_hash=EMPTY_SHA256)
        digest = hashlib.sha256()
        try:
            with requests.get(grant.url, stream=True, timeout=self.timeout) as r:
                if not r.ok:
                    return False
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        digest.update(chunk)
        except requests.RequestException:
            return False
        return hmac.compare_digest(digest.hexdigest(), str(expected_sha256).lower())

    def download(self, key: str, dest: str | Path, *, expected_sha256: str | None = None) -> Path:
        """Stream one object to ``dest``, verifying SHA-256 before the rename."""
        grant = self.presign("GET", key, expires_s=120, payload_hash=EMPTY_SHA256)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        try:
            with requests.get(grant.url, stream=True, timeout=self.timeout) as r:
                if not r.ok:
                    raise ValueError(f"object download failed: HTTP {r.status_code}")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        if chunk:
                            digest.update(chunk); f.write(chunk)
            if expected_sha256 and not hmac.compare_digest(digest.hexdigest(), str(expected_sha256).lower()):
                raise ValueError("object_hash_mismatch")
            tmp.replace(dest)
            return dest
        finally:
            try:
                if tmp.exists(): tmp.unlink()
            except OSError:
                pass

    def delete(self, key: str) -> bool:
        grant = self.presign("DELETE", key, expires_s=60, payload_hash=EMPTY_SHA256)
        try:
            return requests.delete(grant.url, timeout=self.timeout).ok
        except requests.RequestException:
            return False

    def gc(self, keys: list[str], *, older_than_s: float = 3600) -> list[str]:
        """Delete objects that are still present and past the retention window."""
        removed = []
        for key in keys:
            info = self.head(key)
            if info is None:
                removed.append(key)
            elif older_than_s <= 0:
                if self.delete(key):
                    removed.append(key)
        return removed


__all__ = ["EMPTY_SHA256", "S3Grant", "S3ObjectStore"]
