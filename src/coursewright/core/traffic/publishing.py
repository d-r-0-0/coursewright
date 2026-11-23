"""Publish helpers a client calls from its single request choke point.

docs/HOUSE_STYLE.md section 3: every remote round trip publishes a
traffic event from the client's ``_make_request``, and publishing
never raises and never slows a request.  These helpers are that
contract in one place, so a client does not re-implement the
try/except and the path shortening the way discussorator's Canvas and
LLM clients each did (``_publish_traffic_event``,
``_publish_traffic_payload``).

Typical use inside a client's choke point::

    request_started_at = time.monotonic()
    publish_http_round_trip("canvas", method, url, 0, request_started_at)
    response = self._session.request(method, url, **kwargs)
    publish_http_round_trip(
        "canvas", method, url, response.status_code, request_started_at
    )

Every ``publish_*`` function here swallows every exception: a broken
bus, a broken subscriber or a surprising argument must never disturb
the request path for what is a cosmetic feature.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from coursewright.core.traffic import bus

# The Canvas REST prefix.  Everything up to and including it (scheme,
# host, prefix) is noise in a log line, so it is cut from the target.
DEFAULT_API_MARKER = "/api/v1/"

# Longest target shown.  Longer paths are cut and end in "...".
MAX_TARGET_LENGTH = 60


def shorten_request_target(
    url: str,
    api_marker: str = DEFAULT_API_MARKER,
    max_length: int = MAX_TARGET_LENGTH,
) -> str:
    """Reduce a request URL to the short path shown in traffic output.

    Same shortening discussorator's Canvas client applied (drop
    everything through the API marker, cap the length with "..."),
    plus one deliberate addition: the query string and fragment are
    dropped first.  Query strings are where an ``access_token`` or a
    search term (a student's name) can ride along, and house style
    says secrets and student data never appear in traffic output.
    """
    parsed_url = urlsplit(url)
    target_path = parsed_url.path if parsed_url.scheme else url.split("?", 1)[0].split("#", 1)[0]
    marker_index = target_path.find(api_marker) if api_marker else -1
    if marker_index >= 0:
        target_path = target_path[marker_index + len(api_marker) :]
    if len(target_path) > max_length:
        target_path = target_path[: max_length - 3] + "..."
    return target_path


def publish_round_trip(
    source: str,
    method: str,
    target: str,
    status: int,
    request_started_at: float,
) -> None:
    """Publish one round-trip event exactly as given.  Never raises.

    `request_started_at` is a ``time.monotonic()`` reading taken just
    before the request went out; the duration is measured from it.
    `status` follows `TrafficEvent`: 0 in flight, positive HTTP
    status, -1 errored.  Use this directly for services that are not
    plain HTTP paths (an LLM client passes its model as `method` and
    a label like "chat.completions" as `target`).
    """
    try:
        duration_ms = int((time.monotonic() - request_started_at) * 1000)
        bus.publish(
            bus.TrafficEvent(
                source=source,
                method=method,
                target=target,
                status=status,
                duration_ms=duration_ms,
            )
        )
    except Exception:
        return


def publish_payload_echo(
    source: str,
    method: str,
    target: str,
    direction: str,
    text: str,
) -> None:
    """Compatibility hook: private request and response bodies are never published."""
    return


def publish_http_round_trip(
    source: str,
    method: str,
    url: str,
    status_code: int,
    request_started_at: float,
    *,
    api_marker: str = DEFAULT_API_MARKER,
) -> None:
    """Publish a round trip for an HTTP call, given its full URL.

    The verb is upper-cased and the URL shortened with
    `shorten_request_target`.  Never raises.
    """
    try:
        publish_round_trip(
            source,
            method.upper(),
            shorten_request_target(url, api_marker),
            status_code,
            request_started_at,
        )
    except Exception:
        return


def publish_http_response_echo(
    source: str,
    method: str,
    url: str,
    response_text: str,
    *,
    api_marker: str = DEFAULT_API_MARKER,
) -> None:
    """Compatibility hook: HTTP response bodies are private and never published."""
    return
