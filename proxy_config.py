# FILE: proxy_config.py
# Webshare proxy configuration for yfinance requests.
# Reads PROXY_HOST/PORT/USER/PASS from env; falls back to no proxy for local dev.

import os
import logging
import requests

log = logging.getLogger(__name__)

_host = os.environ.get("PROXY_HOST", "")
_port = os.environ.get("PROXY_PORT", "")
_user = os.environ.get("PROXY_USER", "")
_pass = os.environ.get("PROXY_PASS", "")

proxy_session = requests.Session()

if _host and _port and _user and _pass:
    _proxy_url = f"http://{_user}:{_pass}@{_host}:{_port}"
    proxy_session.proxies = {"http": _proxy_url, "https": _proxy_url}
    os.environ["HTTP_PROXY"]  = _proxy_url
    os.environ["HTTPS_PROXY"] = _proxy_url
    log.info(f"Proxy mode: ENABLED via {_host}:{_port}")
else:
    log.info("Proxy mode: DISABLED (no env vars set)")
