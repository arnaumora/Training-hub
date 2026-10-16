"""Thin wrapper around pylibrelinkup: login and one raw graph request.

The library is used only for authentication and the HTTP call; parsing is ours (parse.py).
Pinned to pylibrelinkup 0.11.1 because fetch_graph uses its _get_graph_data_json helper,
which returns the raw response we archive. If the library changes, only this file is affected.
"""

import functools
import logging

import requests
from pylibrelinkup import APIUrl, PyLibreLinkUp
from pylibrelinkup.exceptions import RedirectError

log = logging.getLogger("collector.llu")

HTTP_TIMEOUT_S = 20

# pylibrelinkup calls requests.get/post without a timeout, so a server that accepts the
# connection and never answers would hang the collector forever. Enforce one. This process
# only uses requests through this library.
if not getattr(requests.get, "_th_timeout", False):
    requests.get = functools.partial(requests.get, timeout=HTTP_TIMEOUT_S)
    requests.post = functools.partial(requests.post, timeout=HTTP_TIMEOUT_S)
    requests.get._th_timeout = True  # type: ignore[attr-defined]


class Session:
    """Logged-in LibreLinkUp session for the first (only) connection on the account."""

    def __init__(self, email: str, password: str) -> None:
        region = APIUrl.EU
        for _ in range(2):
            client = PyLibreLinkUp(email=email, password=password, api_url=region)
            try:
                client.authenticate()
                break
            except RedirectError as e:
                region = e.region
                log.info("login redirected to region %s", region.name)
        else:
            raise RuntimeError("login failed after a region redirect")
        patients = client.get_patients()
        if not patients:
            raise RuntimeError("no LibreLinkUp connection on this account (invite not accepted?)")
        self.client = client
        self.patient_id = patients[0].patient_id
        log.info("logged in (region %s)", region.name)

    def fetch_graph(self) -> dict:
        """One request: current reading plus about 12 h of 15-minute history, as raw JSON."""
        return self.client._get_graph_data_json(self.patient_id)
