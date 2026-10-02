import json
import re
from pathlib import Path

from core.models import CondenseRequest

ROOT = Path(__file__).resolve().parents[1]


def test_documented_sample_is_a_valid_api_request() -> None:
    sample = json.loads((ROOT / "examples" / "session.json").read_text(encoding="utf-8"))
    request = CondenseRequest.model_validate(sample)
    assert len(request.messages) == 3
    assert request.summary_token_budget == 128


def test_markdown_local_links_resolve() -> None:
    markdown_files = [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "skills").rglob("*.md"),
    ]
    for source in markdown_files:
        text = source.read_text(encoding="utf-8")
        for destination in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
            if destination.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = destination.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
            if target:
                assert (source.parent / target).exists(), (
                    f"{source.relative_to(ROOT)} links to missing {destination}"
                )


def test_release_docs_are_explicit_about_current_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    use_cases = (ROOT / "docs" / "use-cases.md").read_text(encoding="utf-8")
    assert "pre-release" in readme
    assert "not a public multi-tenant" in readme
    assert "all sessions" in security
    assert "Before a public launch" in use_cases
    assert "S3 lifecycle expiry" in use_cases
