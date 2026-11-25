"""Dailies settings. Everything derives from the project directory so tests can point the app at a fixture tree."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The project directory (the data root holding renders/, notes/, build/ and music/) is DAILIES_PROJECT_DIR, or the
# current working directory when that is unset.


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass
class Settings:
    root: Path = field(default_factory=lambda: Path(os.environ.get("DAILIES_PROJECT_DIR") or Path.cwd()).resolve())
    host: str = os.environ.get("DAILIES_HOST", "127.0.0.1")
    port: int = int(os.environ.get("DAILIES_PORT", "8765"))
    fps: int = 30
    # Hosts the server answers for. Anything else is refused (DNS-rebinding guard).
    local_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    # Public hostnames that arrive through a Cloudflare tunnel. Requests for these require a valid Cloudflare Access
    # JWT, independently of the edge. Empty (the default): only loopback hosts are answered.
    public_hosts: tuple[str, ...] = tuple(
        h.strip() for h in os.environ.get("DAILIES_PUBLIC_HOSTS", "").split(",") if h.strip())
    access_team_domain: str = os.environ.get("DAILIES_ACCESS_TEAM_DOMAIN", "")
    access_aud: str = os.environ.get("DAILIES_ACCESS_AUD", "")
    access_emails: tuple[str, ...] = tuple(
        e.strip().lower() for e in os.environ.get("DAILIES_ACCESS_EMAILS", "").split(",") if e.strip())
    warm: bool = _env_bool("DAILIES_WARM", True)
    jobs_enabled: bool = True
    nice_proxy: int = 15
    nice_build: int = 10
    ffmpeg_threads: int = 2
    # Skip background warm-up while the machine is this busy (1-min load / cores); on-demand work still runs.
    warm_max_load: float = float(os.environ.get("DAILIES_WARM_MAX_LOAD", "0.9"))
    web_dist: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if self.web_dist is None:
            self.web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"

    # derived locations ---------------------------------------------------------------------------------------------
    @property
    def studio_dir(self) -> Path:          # generated media (gitignored with the rest of renders/)
        return self.root / "renders" / "studio"

    @property
    def proxies_dir(self) -> Path:
        return self.studio_dir / "proxies"

    @property
    def cuts_dir(self) -> Path:
        return self.studio_dir / "cuts"

    @property
    def jobs_dir(self) -> Path:
        return self.studio_dir / "jobs"

    @property
    def tmp_dir(self) -> Path:
        return self.studio_dir / "tmp"

    @property
    def frozen_dir(self) -> Path:
        return self.studio_dir / "frozen"

    @property
    def notes_dir(self) -> Path:           # git-tracked, coordinator-readable
        return self.root / "notes" / "studio"

    @property
    def annotations_file(self) -> Path:
        return self.notes_dir / "annotations.json"

    @property
    def timelines_dir(self) -> Path:
        return self.notes_dir / "timelines"

    @property
    def frames_dir(self) -> Path:
        return self.notes_dir / "frames"

    @property
    def python(self) -> Path:
        env = os.environ.get("DAILIES_PYTHON")
        if env:
            return Path(env)
        p = self.root / ".venv" / "bin" / "python"
        return p if p.exists() else Path(sys.executable)

    @property
    def log_file(self) -> Path:
        return self.root / "build" / "logs" / "dailies.log"
