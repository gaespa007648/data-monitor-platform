"""Single-process fixed-window limiter. Do not trust proxy headers by default."""
from collections import OrderedDict
from time import monotonic

from fastapi import HTTPException, Request

windows = OrderedDict()


def limit(request: Request, action: str, maximum: int):
    now = monotonic()
    key = (action, request.client.host if request.client else "unknown")
    start, count = windows.get(key, (now, 0))
    if now - start >= 60:
        start, count = now, 0
    if count >= maximum:
        raise HTTPException(429, "Too many attempts; retry later", headers={"Retry-After": str(max(1, int(60 - (now - start))))})
    windows[key] = (start, count + 1)
    windows.move_to_end(key)
    while len(windows) > 10000:
        windows.popitem(last=False)
