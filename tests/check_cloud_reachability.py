"""Opt-in request probe; uses no account password and sends no device command."""
import asyncio
import json
from pathlib import Path
import sys
import time

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloudio.wire import ClientProfile, envelope


async def main():
    profile = ClientProfile.load()
    request = envelope(profile, "tuya.m.location.list")
    started = time.monotonic()
    async with aiohttp.ClientSession(trust_env=False) as http:
        async with http.post(profile.endpoint, data=request, headers={"User-Agent":profile.user_agent},
                             timeout=aiohttp.ClientTimeout(total=20)) as response:
            reply = await response.json(content_type=None)
            print(json.dumps({"http":response.status, "seconds":round(time.monotonic()-started, 3),
                "structured_response":isinstance(reply, dict), "account_credentials_sent":False,
                "error_code":reply.get("errorCode") if isinstance(reply, dict) else None,
                "device_commands_sent":False}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
