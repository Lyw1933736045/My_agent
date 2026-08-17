"""启动本地 RSSHub 容器的运行时辅助函数。"""

from __future__ import annotations

import os
import subprocess

from dotenv import load_dotenv

from .config import ENV_FILE


CONTAINER_NAME = "financial-single-agent-rsshub"
RSSHUB_IMAGE = "diygod/rsshub:latest"


def ensure_local_rsshub() -> bool:
    """Ensure the configured local RSSHub service is running.

    This is intentionally best-effort: a missing Docker installation must not
    prevent NewsNow or the API itself from starting.
    """
    load_dotenv(ENV_FILE, override=False)
    base = os.environ.get("RSSHUB_BASE", "").strip().rstrip("/")
    if base not in {"http://localhost:1200", "http://127.0.0.1:1200"}:
        return False

    try:
        subprocess.run(
            ["docker", "info"], check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=8,
        )
        status = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{CONTAINER_NAME}$",
             "--format", "{{.Status}}"],
            check=True, capture_output=True, text=True, timeout=8,
        ).stdout.strip()
        if status:
            if not status.lower().startswith("up "):
                subprocess.run(["docker", "start", CONTAINER_NAME], check=True,
                                stdout=subprocess.DEVNULL, timeout=15)
            return True
        subprocess.run(
            ["docker", "run", "-d", "--name", CONTAINER_NAME,
             "-p", "1200:1200", "--restart", "unless-stopped", RSSHUB_IMAGE],
            check=True, stdout=subprocess.DEVNULL, timeout=30,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"RSSHub 自动启动失败（请安装并启动 Docker Desktop）：{exc}")
        return False
