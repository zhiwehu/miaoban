"""Bounded HTTP reads and timestamps shared by the bundled tools."""
import json
from datetime import datetime, timezone


async def fetch(http, url, *, params=None, json_response=False, timeout=None):
    options={'timeout':timeout} if timeout is not None else {}
    async with http.get(url, params=params, allow_redirects=False, **options) as response:
        if response.status != 200:
            raise ValueError('upstream_http_'+str(response.status))
        raw = bytearray()
        async for block in response.content.iter_chunked(8192):
            raw.extend(block)
            if len(raw) > 1024 * 1024:
                raise ValueError('upstream_too_large')
        return json.loads(raw) if json_response else bytes(raw)



def fetched_at():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
