"""Wire encoding, request authentication and explicit local app configuration."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid


@dataclass(frozen=True)
class ClientProfile:
    endpoint: str
    client_id: str
    signing_secret: str
    api_version: str = "1.0"
    encryption_version: str = "0.0.1"
    user_agent: str = "xiaohai-web/2.0"

    @classmethod
    def load(cls, path=None):
        source = Path(path) if path else Path(__file__).resolve().parents[1] / ".local-data" / "cloud-client.json"
        try:
            settings = json.loads(source.read_text(encoding="utf-8"))
            profile = cls(**{key: value for key, value in settings.items() if key in cls.__dataclass_fields__})
            if any(not isinstance(value, str) or not value for value in vars(profile).values()):
                raise ValueError()
            if any(value.startswith("REPLACE_") for value in (profile.client_id, profile.signing_secret, profile.user_agent)):
                raise ValueError()
            address = urlsplit(profile.endpoint)
            if address.scheme != "https" and not (address.scheme == "http" and address.hostname in {"127.0.0.1", "localhost"}):
                raise ValueError()
            return profile
        except (OSError, ValueError, TypeError, AttributeError):
            raise ValueError("请先按 README 配置本机 cloud-client.json。") from None


def password_proof(password: str) -> str:
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def request_signature(fields, key):
    canonical = []
    for name, value in sorted(fields.items()):
        if name in {"sign", "gid"} or value == "":
            continue
        if name == "postData":
            digest = hashlib.md5(value.encode("utf-8")).hexdigest()
            blocks = [digest[start:start + 8] for start in range(0, 32, 8)]
            value = "".join(blocks[index] for index in (1, 0, 3, 2))
        canonical.append(f"{name}={value}")
    return hmac.digest(key.encode("utf-8"), "||".join(canonical).encode("utf-8"), "sha256").hex()


def envelope(profile, action, payload=None, *, version=None, group=None, session_id=None):
    fields = {"a":action, "clientId":profile.client_id, "v":version or profile.api_version,
              "et":profile.encryption_version, "time":str(int(time.time())), "requestId":str(uuid.uuid4())}
    if group is not None:
        fields["gid"] = str(group)
    if session_id:
        fields["sid"] = session_id
    if payload is not None:
        fields["postData"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fields["sign"] = request_signature(fields, profile.signing_secret)
    return fields
