"""python -m dailies_server  ->  uvicorn on 127.0.0.1:8765, logging to build/logs/dailies.log."""
import logging
import logging.handlers
import sys

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    s = Settings()
    s.log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(s.log_file, maxBytes=10 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    if sys.stderr.isatty():
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("refusing to bind a non-loopback address; expose Dailies through a tunnel such as Cloudflare Access")
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_config=None, access_log=True,
                proxy_headers=False, server_header=False, timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
