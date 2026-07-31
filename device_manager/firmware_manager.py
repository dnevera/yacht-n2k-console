"""Firmware update operations for YDNU-02.

Handles OTA firmware flashing and version checking via yachtd.com scraping.
"""

import re
import urllib.request
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from device_manager.operation_runner import OperationRunner


class FirmwareManager:
    """OTA firmware flash and version tracking.

    Flash uses FIRMWARE_START passthrough (via OperationRunner).
    Version check scrapes yachtd.com/downloads/ for latest release.
    """

    def __init__(self, ops: OperationRunner, invalidate_cache: Optional[Callable[[], None]] = None):
        self._ops = ops
        self._invalidate_cache = invalidate_cache or (lambda: None)
        self.fw_progress: Dict[str, Any] = {"stage": "idle", "percent": 0}

    def flash_firmware(self, bin_path: str) -> Dict[str, str]:
        """Flash firmware via proxy passthrough (chunked binary write)."""
        def _progress(stage: str, pct: int) -> None:
            self.fw_progress = {"stage": stage, "percent": pct}

        def _do(ctrl):
            self.fw_progress = {"stage": "starting", "percent": 0}
            result = ctrl.update_firmware(bin_path, skip_backup=False, progress_cb=_progress)
            self._invalidate_cache()
            self.fw_progress = {"stage": "done", "percent": 100}
            written = result.get("written", 0) if result else 0
            return {"status": "ok", "message": f"Firmware uploaded ({written} bytes). Device rebooting."}

        return self._ops.raw_locked_operation(_do)

    @staticmethod
    def check_latest_firmware() -> Dict[str, Any]:
        """Fetch and parse yachtd.com/downloads/ to find the latest YDNU-02 firmware."""
        url = "https://www.yachtd.com/downloads/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "YDNU02-Console/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='ignore')

            fw_match = re.search(
                r'name="ydnufw"\s+href="([^"]+)".*?'
                r'Firmware\s+Update.*?'
                r'text-dark-400">\s*([\w\s,]+?\d{4})\s*<.*?'
                r'text-dark-400[^>]*>\s*([\d.]+)\s*<',
                html, re.DOTALL | re.IGNORECASE
            )
            if not fw_match:
                return {"status": "ok", "latest_version": None,
                        "message": "Could not parse firmware block", "url": url}

            download_file = fw_match.group(1).strip()
            date_raw      = fw_match.group(2).strip()
            version       = fw_match.group(3).strip()
            download_url  = f"https://www.yachtd.com/downloads/{download_file}"

            try:
                dt   = datetime.strptime(date_raw, "%B %d, %Y")
                date = dt.strftime("%d/%m/%Y")
            except ValueError:
                date = date_raw

            changelog = ""
            cl_match = re.search(
                r'name="ydnufw".*?border-t\s+border-slate-200">\s*(.*?)\s*</div>',
                html, re.DOTALL | re.IGNORECASE
            )
            if cl_match:
                changelog = cl_match.group(1).strip()

            return {
                "status": "ok",
                "latest_version": version,
                "release_date": date,
                "download_url": download_url,
                "changelog": changelog,
                "url": url,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "url": url}
