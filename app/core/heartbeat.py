"""Shared constants for the worker heartbeat.

The worker writes this key to Redis on startup and every ~10 seconds after.
The API reads it to answer one simple question for the UI: is anything
actually processing jobs right now? A job stuck on "queued" while this key is
missing means the worker process is not running - by far the most common
local-dev mistake (the API and the worker are two separate processes; starting
one does not start the other).
"""

from __future__ import annotations

HEARTBEAT_KEY = "scrappy:worker:heartbeat"
HEARTBEAT_TTL_SECONDS = 25
HEARTBEAT_INTERVAL_SECONDS = 10