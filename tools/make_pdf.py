"""
HTML -> PDF renderer for the phase documentation.

Every phase ends with a document in docs/ explaining what was built. This script
turns the HTML source into a PDF using the Chromium that Playwright already
installs for the agent, so there is no extra dependency to manage.

Usage:
    agent/venv/Scripts/python.exe tools/make_pdf.py docs/PHASE_0_EXPLAINED.html

The PDF is written next to the HTML with the same name.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def render(html_path: Path) -> Path:
    """Render one HTML file to a PDF beside it. Returns the PDF path."""
    if not html_path.exists():
        raise FileNotFoundError(html_path)

    pdf_path = html_path.with_suffix(".pdf")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # file:// so relative assets resolve. wait_until="load" makes sure fonts and
        # any images are in place before we snapshot the page.
        page.goto(html_path.resolve().as_uri(), wait_until="load")

        page.pdf(
            path=str(pdf_path),
            format="A4",
            # print_background keeps the code blocks and callout boxes from rendering
            # as plain white, which is most of what makes the document readable.
            print_background=True,
            margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                '<div style="width:100%;font-size:8px;color:#94a3b8;'
                'padding:0 12mm;display:flex;justify-content:space-between;">'
                "<span>Nexaris AI Outreach Agent</span>"
                '<span class="pageNumber"></span>'
                "</div>"
            ),
        )
        browser.close()

    return pdf_path


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    for arg in sys.argv[1:]:
        out = render(Path(arg))
        size_kb = out.stat().st_size / 1024
        print(f"wrote {out}  ({size_kb:.0f} KB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
