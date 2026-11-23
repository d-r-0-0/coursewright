"""Traffic events: every remote round trip, published for ui/ to render.

`bus` is the pub/sub bus (ported from discussorator); `publishing`
holds the never-raising helpers a client calls from its request choke
point.  The names most callers need are re-exported here.
"""

from __future__ import annotations

from coursewright.core.traffic.bus import (
    TrafficEvent,
    publish,
    publish_chunks,
    reset_for_tests,
    subscribe,
    unsubscribe,
)
from coursewright.core.traffic.publishing import (
    publish_http_response_echo,
    publish_http_round_trip,
    publish_payload_echo,
    publish_round_trip,
    shorten_request_target,
)

# Named values of `TrafficEvent.status` that are not HTTP status codes
# (the meanings are documented on `TrafficEvent`).
STATUS_IN_FLIGHT = 0
STATUS_ERRORED = -1
STATUS_OUTBOUND_CHUNK = -2
STATUS_INBOUND_CHUNK = -3

__all__ = [
    "STATUS_ERRORED",
    "STATUS_INBOUND_CHUNK",
    "STATUS_IN_FLIGHT",
    "STATUS_OUTBOUND_CHUNK",
    "TrafficEvent",
    "publish",
    "publish_chunks",
    "publish_http_response_echo",
    "publish_http_round_trip",
    "publish_payload_echo",
    "publish_round_trip",
    "reset_for_tests",
    "subscribe",
    "unsubscribe",
    "shorten_request_target",
]
