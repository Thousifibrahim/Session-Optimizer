from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.scripts = 0
        self.steps: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        href = attributes.get("href")
        step = attributes.get("data-release-step")
        if identifier:
            self.ids.append(identifier)
        if href:
            self.hrefs.append(href)
        if step:
            self.steps.append(int(step))
        if tag == "script":
            self.scripts += 1


def test_page_navigation_and_release_sections() -> None:
    parser = PageParser()
    parser.feed((ROOT / "index.html").read_text(encoding="utf-8"))
    assert len(parser.ids) == len(set(parser.ids))
    assert parser.steps == list(range(1, 19))
    for href in parser.hrefs:
        if href.startswith("#"):
            assert href[1:] in parser.ids
    assert {"main", "start", "architecture", "contribute", "safety"} <= set(parser.ids)
    assert parser.scripts == 0


def test_page_is_static_and_uses_consistent_project_branding() -> None:
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'name="viewport"' in page
    assert 'lang="en"' in page
    assert "prefers-reduced-motion" in page
    assert "This is a static project guide" in page
    assert "Session Optimizer" in page
    assert "#10151d" in page and "#b8f772" in page
    assert "Session Context Condenser" not in page
    assert "HISTORY.md" not in page
    assert "github.com/Thousifibrahim/Session-Optimizer" in page
    assert 'href="favicon.svg"' in page
    assert (ROOT / "favicon.svg").is_file()
    assert (ROOT / "robots.txt").is_file()
    assert (ROOT / "sitemap.xml").is_file()
    assert "<form" not in page
