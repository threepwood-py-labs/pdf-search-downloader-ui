"""HTML parsing helpers used by provider adapters and PDF resolution."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


@dataclass(frozen=True, slots=True)
class HtmlLink:
    """Describe one extracted HTML link-like element."""

    url: str
    text: str
    tag_name: str


class _LinkCollector(HTMLParser):
    """Collect anchor-like elements from HTML without external dependencies."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._anchor_stack: list[str] = []
        self.links: list[HtmlLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track supported start tags."""

        attr_map = {name: value or "" for name, value in attrs}
        if tag == "a":
            href = attr_map.get("href", "").strip()
            if href:
                self._anchor_stack.append(urljoin(self._base_url, href))
            return
        if tag in {"embed", "iframe"}:
            src = attr_map.get("src", "").strip()
            if src:
                self.links.append(
                    HtmlLink(url=urljoin(self._base_url, src), text="", tag_name=tag)
                )
            return
        if tag == "object":
            data_url = attr_map.get("data", "").strip()
            if data_url:
                self.links.append(
                    HtmlLink(
                        url=urljoin(self._base_url, data_url),
                        text="",
                        tag_name=tag,
                    )
                )

    def handle_data(self, data: str) -> None:
        """Capture text inside anchors."""

        if not self._anchor_stack:
            return
        stripped = data.strip()
        if not stripped:
            return
        self.links.append(
            HtmlLink(url=self._anchor_stack[-1], text=stripped, tag_name="a")
        )

    def handle_endtag(self, tag: str) -> None:
        """Discard closed anchors."""

        if tag == "a" and self._anchor_stack:
            self._anchor_stack.pop()


def extract_links(html: str, base_url: str) -> list[HtmlLink]:
    """Extract anchor-like links from one HTML document."""

    collector = _LinkCollector(base_url)
    collector.feed(html)
    unique_links: list[HtmlLink] = []
    seen: set[tuple[str, str, str]] = set()
    for link in collector.links:
        key = (link.url, link.text, link.tag_name)
        if key in seen:
            continue
        seen.add(key)
        unique_links.append(link)
    return unique_links


def looks_like_pdf_url(url: str) -> bool:
    """Return whether one URL looks like it points to a PDF file."""

    parsed = urlparse(url)
    path = parsed.path.lower()
    return path.endswith(".pdf") or ".pdf/" in path


def extract_pdf_links(html: str, base_url: str) -> list[str]:
    """Extract likely PDF URLs from one landing page."""

    pdf_links: list[str] = []
    seen: set[str] = set()
    for link in extract_links(html, base_url):
        if not looks_like_pdf_url(link.url):
            continue
        normalized = link.url.strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        pdf_links.append(normalized)
    return pdf_links
