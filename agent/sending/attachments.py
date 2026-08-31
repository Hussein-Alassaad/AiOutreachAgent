"""
Downloads a reply's Vercel Blob attachment (see
src/lib/outreach/reply-attachments.ts) to a local temp file so Playwright
can attach it to a real LinkedIn/Instagram message -- Playwright's file
chooser API (page.expect_file_chooser() + .set_files()) needs a real
filesystem path, it can't attach directly from a URL.

Shared by linkedin_send.py and instagram_send.py's send_reply() functions
so both channels download/clean-up identically rather than duplicating
this logic twice.
"""

from __future__ import annotations

import pathlib
import tempfile
from urllib.parse import urlparse

import httpx

_TIMEOUT_SECONDS = 30.0


def download_attachment(url: str, file_name: str | None) -> pathlib.Path:
    """
    Downloads message["attachment_url"] to a temp file, named after the
    original upload (attachment_name) where possible so the platform's own
    upload dialog shows a real filename, not a random blob key. Caller is
    responsible for deleting the returned path when done (see
    cleanup_attachment()).
    """
    suffix = pathlib.Path(file_name or urlparse(url).path).suffix or ""
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="nexaris-reply-attachment-"))
    dest = tmp_dir / (file_name or f"attachment{suffix}")

    with httpx.stream("GET", url, timeout=_TIMEOUT_SECONDS, follow_redirects=True) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)

    return dest


def cleanup_attachment(path: pathlib.Path) -> None:
    """Removes the downloaded temp file and its containing temp dir."""
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:
        pass  # best-effort cleanup, never worth failing a send over
