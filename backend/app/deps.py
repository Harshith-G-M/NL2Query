import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User
from .security import decode_token

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    uid = decode_token(token)
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


class _RateLimiter:
    """In-memory sliding window per user. Good enough for one process; use Redis for many."""

    def __init__(self) -> None:
        self._hits: dict[int, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, user_id: int, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[user_id]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                    f"Rate limit: {limit} requests per minute")
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = _RateLimiter()


def query_rate_limit(user: User = Depends(get_current_user)) -> User:
    rate_limiter.check(user.id, get_settings().rate_limit_per_min)
    return user
