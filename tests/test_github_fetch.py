"""Tests for issue pagination, which must not return pull requests."""
import pytest

from app import github


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Serves canned pages and records how many were requested."""

    def __init__(self, pages):
        self._pages = pages
        self.pages_fetched = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.pages_fetched += 1
        index = (params or {}).get("page", 1) - 1
        if index < len(self._pages):
            return _FakeResponse(self._pages[index])
        return _FakeResponse([])


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


def _issue(number):
    return {"number": number, "title": f"Issue {number}"}


def _pr(number):
    return {"number": number, "title": f"PR {number}", "pull_request": {}}


def _install(monkeypatch, pages):
    client = _FakeClient(pages)
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kw: client)
    return client


@pytest.mark.asyncio
async def test_pull_requests_are_excluded(monkeypatch, token):
    _install(monkeypatch, [[_issue(1), _pr(2), _issue(3)]])

    result = await github.fetch_issues("o/r", per_page=100)

    assert [i["number"] for i in result] == [1, 3]


@pytest.mark.asyncio
async def test_limit_is_honoured_exactly(monkeypatch, token):
    page = [_issue(n) for n in range(1, 101)]
    _install(monkeypatch, [page, [_issue(n) for n in range(101, 201)]])

    result = await github.fetch_issues("o/r", per_page=100, limit=150)

    assert len(result) == 150


@pytest.mark.asyncio
async def test_limit_stops_paging_early(monkeypatch, token):
    """The point of the limit: don't walk pages you no longer need."""
    pages = [[_issue(n) for n in range(p * 100, p * 100 + 100)]
             for p in range(10)]
    client = _install(monkeypatch, pages)

    await github.fetch_issues("o/r", per_page=100, limit=10)

    assert client.pages_fetched == 1


@pytest.mark.asyncio
async def test_pr_heavy_repo_keeps_paging_for_real_issues(monkeypatch, token):
    """
    A page of 90 PRs and 10 issues yields only 10 towards the limit, so
    paging must continue rather than stopping at the first page.
    """
    def mixed(base):
        return ([_pr(base + n) for n in range(90)]
                + [_issue(base + 90 + n) for n in range(10)])

    _install(monkeypatch, [mixed(0), mixed(100), mixed(200)])

    result = await github.fetch_issues("o/r", per_page=100, limit=25)

    assert len(result) == 25
    assert all("pull_request" not in i for i in result)


@pytest.mark.asyncio
async def test_no_limit_fetches_everything(monkeypatch, token):
    _install(monkeypatch, [[_issue(n) for n in range(100)], [_issue(100)]])

    result = await github.fetch_issues("o/r", per_page=100)

    assert len(result) == 101


@pytest.mark.asyncio
async def test_empty_repo(monkeypatch, token):
    _install(monkeypatch, [[]])

    assert await github.fetch_issues("o/r") == []
