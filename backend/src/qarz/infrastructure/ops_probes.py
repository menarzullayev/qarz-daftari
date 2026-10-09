"""What the operations watch reads outside the database (DEC-078): the figures the backup jobs write,
how full a disk is, and the API from inside the Compose network.

Every reader answers with numbers and nothing else, and none of them raises for what it is there to
notice: a file that is missing is a figure that is absent, an API that does not answer is "not healthy".
"""

import shutil
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from qarz.domain.ops_alerts import parse_figures
from qarz.infrastructure.file_store import HttpTransport, Transport

HEALTH_PATH = "/healthz"
METRICS_PATH = "/metrics"
API_TIMEOUT_SECONDS = 5.0
MAX_METRICS_BYTES = 2 * 1024 * 1024
# A figure file is a few lines; anything larger is not one.
_MAX_FIGURE_FILE_BYTES = 64 * 1024


def read_figure_files(directory: str) -> dict[str, float]:
    """The figures in a directory the backup jobs write to, mounted read-only.

    `*.prom` files are in the Prometheus text format (deploy/backup/scripts/lib.sh, `write_metrics`);
    a `<job>.last-success` file holds one epoch and becomes the figure of that name. A directory that is
    missing or empty gives no figures, which the rules read as "never happened".
    """
    figures: dict[str, float] = {}
    try:
        paths = sorted(Path(directory).iterdir())
    except OSError:
        return figures
    for path in paths:
        if path.suffix not in (".prom", ".last-success") or path.name.startswith("."):
            continue
        try:
            if path.stat().st_size > _MAX_FIGURE_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix == ".prom":
            figures |= parse_figures(text)
        else:
            try:
                figures[path.name] = float(text.strip())
            except ValueError:
                continue
    return figures


def disk_shares(paths: Sequence[str]) -> dict[str, float]:
    """For each path, the share of its filesystem that is in use. A path that cannot be asked is left out."""
    shares: dict[str, float] = {}
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        if usage.total > 0:
            shares[path] = usage.used / usage.total
    return shares


class ApiProbe:
    """The API as a neighbour in the Compose network sees it: `/healthz`, and `/metrics` with the token."""

    def __init__(self, base_url: str, metrics_token: str = "", transport: Transport | None = None) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
            raise ValueError("QD_ALERT_API_URL must be scheme://host[:port] with no path")
        self._token = metrics_token
        self._transport = transport or HttpTransport(
            base_url, timeout=API_TIMEOUT_SECONDS, max_response_bytes=MAX_METRICS_BYTES
        )

    async def healthy(self) -> bool:
        try:
            status, _ = await self._transport("GET", HEALTH_PATH, {}, None)
        except Exception:
            return False
        return status == 200

    async def counters(self) -> dict[str, float] | None:
        try:
            status, body = await self._transport("GET", METRICS_PATH, {"Authorization": f"Bearer {self._token}"}, None)
        except Exception:
            return None
        if status != 200 or len(body) > MAX_METRICS_BYTES:
            return None
        return parse_figures(body.decode("utf-8", errors="replace"))
