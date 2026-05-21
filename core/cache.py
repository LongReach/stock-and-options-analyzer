import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, Hashable, Iterator, Optional, TypeVar

V = TypeVar("V")


@dataclass
class _Entry(Generic[V]):
    value: V
    created_at: float


class TTLCache(Generic[V]):
    """A size-bounded cache with per-entry time-to-live (TTL) expiry.

    - Generic over the stored value type V.
    - Bounded to `maxsize` entries; the least-recently-used entry is
      evicted when the cache is full and a new key is inserted.
    - Entries older than `ttl` seconds are treated as stale and dropped
      lazily on access (and can be purged eagerly via `purge()`).
    """

    def __init__(self, maxsize: int = 128, ttl: Optional[float] = None) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be a positive integer")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be a positive number or None")

        self._maxsize = maxsize
        self._ttl = ttl
        self._data: OrderedDict[Hashable, _Entry[V]] = OrderedDict()

    # --- core API --------------------------------------------------------

    def get(self, key: Hashable, default: Optional[V] = None) -> Optional[V]:
        """Return the value for `key` if present and fresh, else `default`."""
        entry = self._data.get(key)
        if entry is None:
            return default

        now = time.monotonic()
        if not self._is_fresh(entry, now):
            del self._data[key]
            return default

        # Mark as most-recently-used.
        self._data.move_to_end(key)
        return entry.value

    def put(self, key: Hashable, value: V) -> None:
        """Insert or update `key` with `value`, evicting LRU if needed."""
        now = time.monotonic()

        if key in self._data:
            self._data[key] = _Entry(value, now)
            self._data.move_to_end(key)
            return

        if len(self._data) >= self._maxsize:
            # Evict least-recently-used (the first item).
            self._data.popitem(last=False)

        self._data[key] = _Entry(value, now)

    def purge(self) -> int:
        """Eagerly remove all stale entries. Returns the number dropped."""
        if self._ttl is None:
            return 0
        now = time.monotonic()
        stale = [k for k, e in self._data.items() if not self._is_fresh(e, now)]
        for k in stale:
            del self._data[k]
        return len(stale)

    # --- internal helpers ------------------------------------------------

    def _is_fresh(self, entry: _Entry[V], now: float) -> bool:
        if self._ttl is None:
            return True
        return (now - entry.created_at) < self._ttl

    # --- dunder / convenience -------------------------------------------

    def __contains__(self, key: Hashable) -> bool:
        return self.get(key, _MISSING) is not _MISSING  # type: ignore[arg-type]

    def __len__(self) -> int:
        """Number of entries currently stored (may include stale ones)."""
        return len(self._data)

    def __getitem__(self, key: Hashable) -> V:
        result = self.get(key, _MISSING)  # type: ignore[arg-type]
        if result is _MISSING:
            raise KeyError(key)
        return result  # type: ignore[return-value]

    def __setitem__(self, key: Hashable, value: V) -> None:
        self.put(key, value)

    def __delitem__(self, key: Hashable) -> None:
        del self._data[key]

    def clear(self) -> None:
        self._data.clear()


# Sentinel for distinguishing "missing" from a stored None value.
_MISSING = object()