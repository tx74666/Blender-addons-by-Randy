"""Serial, callback-driven animation worklist batches, without Blender imports.

The Blender adapter owns timers, scene ownership and worker cancellation. A tick
starts at most one item; returning from ``start`` does not finish that item. The
adapter must call ``complete`` with the matching attempt token. It records the
launch fingerprint before starting a SYNC worker, so a later edit cannot replace
the proof of what was actually published.

Payloads, fingerprints and results must be deepcopy-able data, not Blender IDs.
They are copied at the queue boundary. Queue instances belong to one event loop;
this module does not start threads or workers.
"""

from copy import deepcopy
from dataclasses import dataclass
import uuid


ADD = 'ADD'
SYNC = 'SYNC'
QUEUED = 'QUEUED'
RUNNING = 'RUNNING'
COMPLETED = 'COMPLETED'
FAILED = 'FAILED'
CANCELLING = 'CANCELLING'
CANCELLED = 'CANCELLED'
_TERMINAL = frozenset((COMPLETED, FAILED, CANCELLED))
_UNRECORDED = object()


@dataclass(frozen=True)
class QueueItem:
    """A stable slot/item key and optional scan-time data."""

    key: str
    payload: object = None


@dataclass(frozen=True)
class QueueAttempt:
    """Passed to the start/cancel callback; token identifies this launch only."""

    key: str
    token: str
    payload: object = None


@dataclass(frozen=True)
class QueueSuccess:
    key: str
    token: str
    payload: object
    fingerprint: object
    fingerprint_recorded: bool
    result: object
    message: str


@dataclass(frozen=True)
class QueueFailure:
    key: str
    token: str
    message: str


class SerialQueue:
    """One ADD or SYNC batch whose successful prefix survives interruption.

    ``busy`` stays true between items, and while a cancelled worker is still
    running. ``remaining_keys`` includes the failed/cancelled current item.
    ``tick(start)`` calls ``start(attempt)`` once. Synchronous adapters may call
    ``complete`` inside ``start``; the next item still waits for another tick.

    SYNC requires an explicitly recorded launch fingerprint for success. The
    module does not decide whether a proof is complete or Unknown: that decision
    belongs to the adapter, which may record an explicit Unknown payload.
    """

    def __init__(self, mode, items):
        if mode not in (ADD, SYNC):
            raise ValueError('Animation queue mode must be ADD or SYNC.')
        copied = []
        seen = set()
        for item in items:
            if not isinstance(item, QueueItem):
                raise TypeError('Animation queue items must be QueueItem instances.')
            if not isinstance(item.key, str) or not item.key.strip():
                raise ValueError('Every animation queue item needs a non-empty stable key.')
            if item.key in seen:
                raise ValueError('Duplicate animation queue item key: ' + item.key)
            seen.add(item.key)
            copied.append(deepcopy(item))
        self.mode = mode
        self._items = tuple(copied)
        self._next = 0
        self._running = None
        self._fingerprint = _UNRECORDED
        self._successes = []
        self._starting = False
        self._cancel_requested = False
        self.state = QUEUED if self._items else COMPLETED
        self.failure = None
        self.cancel_error = ''
        self.cancel_message = ''

    @property
    def busy(self):
        return self.state not in _TERMINAL

    @property
    def running(self):
        return deepcopy(self._running)

    @property
    def total(self):
        return len(self._items)

    @property
    def successes(self):
        return tuple(deepcopy(self._successes))

    @property
    def succeeded_keys(self):
        return tuple(item.key for item in self._successes)

    @property
    def remaining_keys(self):
        return tuple(item.key for item in self._items[self._next:])

    def record_fingerprint(self, token, fingerprint):
        """Freeze launch proof once; return false for stale/cancelled tokens."""
        if (self._running is None or self._running.token != token or
                self._cancel_requested or self._fingerprint is not _UNRECORDED):
            return False
        self._fingerprint = deepcopy(fingerprint)
        return True

    def tick(self, start):
        """Start one item, or return false while waiting/stopped; never advance it."""
        if not self.busy or self._running is not None or self._starting:
            return False
        if self._cancel_requested:
            self.state = CANCELLED
            return False
        item = self._items[self._next]
        attempt = QueueAttempt(item.key, uuid.uuid4().hex, deepcopy(item.payload))
        self._running = attempt
        self._fingerprint = _UNRECORDED
        self.state = RUNNING
        self._starting = True
        try:
            start(deepcopy(attempt))
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            if self._running is not None and self._running.token == attempt.token:
                self.complete(attempt.token, success=False, message=message)
            elif not self._cancel_requested:
                # A synchronous callback may commit success and then raise.
                # Keep that success, but do not launch more work after the error.
                self.failure = QueueFailure(attempt.key, attempt.token, message)
                self.state = FAILED
        finally:
            self._starting = False
        return True

    def complete(self, token, *, success, message='', result=None, published=False):
        """Accept this launch's completion once; stale tokens have no effect.

        A late worker success during cancellation is unconfirmed unless the
        adapter explicitly supplies ``published=True`` after verifying a real
        ADD commit or published export/manifest. That success is retained, but
        the batch still stops. Returning from a worker alone is not such proof.
        """
        attempt = self._running
        if attempt is None or attempt.token != token:
            return False
        if self._cancel_requested and not (success and published):
            self.cancel_message = str(message)
            self._running = None
            self._fingerprint = _UNRECORDED
            self.state = CANCELLED
            return True
        recorded = self._fingerprint is not _UNRECORDED
        if success and self.mode == SYNC and not recorded:
            success = False
            message = 'SYNC finished without a recorded launch fingerprint.'
        if success:
            # Copy before mutating queue state, so invalid data cannot partially
            # commit a success or release the running item.
            saved = QueueSuccess(attempt.key, attempt.token,
                                 deepcopy(attempt.payload),
                                 deepcopy(self._fingerprint) if recorded else None,
                                 recorded, deepcopy(result), str(message))
            self._successes.append(saved)
            self._next += 1
            if self._cancel_requested:
                self.cancel_message = str(message)
                self.state = CANCELLED
            else:
                self.state = COMPLETED if self._next == len(self._items) else RUNNING
        else:
            self.failure = QueueFailure(attempt.key, attempt.token, str(message))
            self.state = FAILED
        self._running = None
        self._fingerprint = _UNRECORDED
        return True

    def cancel(self, cancel_running=None):
        """Stop further launches, asking the adapter to cancel an active worker.

        If a worker is active the queue remains CANCELLING/busy until its matching
        completion arrives. A cancellation callback error is reported and does
        not pretend the worker has stopped. Repeated requests do not call it twice.
        """
        if not self.busy or self._cancel_requested:
            return False
        self._cancel_requested = True
        if self._running is None:
            self.state = CANCELLED
            return True
        self.state = CANCELLING
        if cancel_running is not None:
            try:
                cancel_running(deepcopy(self._running))
            except Exception as exc:
                self.cancel_error = str(exc) or type(exc).__name__
        return True

    def acknowledge_cancel(self, token, *, message=''):
        """Confirm an active worker stopped, without treating failure as success."""
        if not self._cancel_requested:
            return False
        return self.complete(token, success=False, message=message)
