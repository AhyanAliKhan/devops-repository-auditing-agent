from __future__ import annotations

import pytest

from repo_manager import normalize_github_url


def test_normalize_https_url() -> None:
    url, owner, repo = normalize_github_url("https://github.com/psf/requests")
    assert url == "https://github.com/psf/requests"
    assert owner == "psf"
    assert repo == "requests"


def test_normalize_with_git_suffix_and_slash() -> None:
    url, owner, repo = normalize_github_url("https://github.com/psf/requests.git/")
    assert url == "https://github.com/psf/requests"
    assert owner == "psf"
    assert repo == "requests"


def test_normalize_shorthand() -> None:
    url, owner, repo = normalize_github_url("pallets/flask")
    assert url == "https://github.com/pallets/flask"
    assert owner == "pallets"
    assert repo == "flask"


def test_reject_non_github() -> None:
    with pytest.raises(ValueError, match="github.com"):
        normalize_github_url("https://gitlab.com/foo/bar")
