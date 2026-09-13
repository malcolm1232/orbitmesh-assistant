"""Public-link fetchers with the network mocked out."""
import io
import json

import pytest

from orbitmesh import fetchers
from orbitmesh.connectors import ConnectorError


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes, ctype: str = "text/markdown", disp: str = ""):
        super().__init__(data)
        self.headers = {"Content-Type": ctype, "Content-Disposition": disp}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock(monkeypatch, routes):
    """routes: {substring_of_url: FakeResp | Exception}"""
    calls = []

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        calls.append(url)
        for key, resp in routes.items():
            if key in url:
                if isinstance(resp, Exception):
                    raise resp
                resp.seek(0)
                return resp
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(fetchers.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_gdrive_file_link(monkeypatch):
    calls = _mock(monkeypatch, {"uc?export=download&id=1AbCdEfGhIjKlMnOp": FakeResp(b"# Doc\n\nhello", disp='attachment; filename="notes.md"')})
    out = fetchers.fetch_gdrive("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing")
    assert len(out) == 1 and out[0].filename == "notes.md" and out[0].data.startswith(b"# Doc")
    assert "id=1AbCdEfGhIjKlMnOp" in calls[0]


def test_gdrive_doc_link_exports_markdown(monkeypatch):
    _mock(monkeypatch, {"export?format=md": FakeResp(b"# Exported\n\nbody")})
    out = fetchers.fetch_gdrive("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit")
    assert out[0].data.startswith(b"# Exported") and out[0].filename.endswith(".md")


def test_gdrive_private_link_is_reported_as_not_public(monkeypatch):
    _mock(monkeypatch, {"uc?export=download": FakeResp(b"<!DOCTYPE html><html>Sign in</html>", ctype="text/html")})
    with pytest.raises(fetchers.LinkNotPublic):
        fetchers.fetch_gdrive("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view")


def test_gdrive_folder_requires_api_key_and_lists_with_it(monkeypatch):
    with pytest.raises(ConnectorError, match="GOOGLE_API_KEY"):
        fetchers.fetch_gdrive("https://drive.google.com/drive/folders/1FolderIdXyzAbc", api_key="")
    listing = json.dumps({"files": [{"id": "1FileAAAAAAAAAA", "name": "a.md", "mimeType": "text/markdown"},
                                    {"id": "1ImgBBBBBBBBBBB", "name": "pic.png", "mimeType": "image/png"}]}).encode()
    _mock(monkeypatch, {"googleapis.com/drive/v3/files?q=": FakeResp(listing, ctype="application/json"),
                        "id=1FileAAAAAAAAAA": FakeResp(b"# A\n\ntext")})
    out = fetchers.fetch_gdrive("https://drive.google.com/drive/folders/1FolderIdXyzAbc", api_key="k")
    assert [o.filename for o in out] == ["gdrive-1FileAAA.md"] or out[0].data == b"# A\n\ntext"


def test_sharepoint_file_link_uses_download_flag(monkeypatch):
    calls = _mock(monkeypatch, {"download=1": FakeResp(b"# SP\n\ntext", disp="attachment; filename=guide.md")})
    out = fetchers.fetch_sharepoint("https://contoso.sharepoint.com/:t:/s/site/EaBcDeF?e=abc")
    assert out[0].filename == "guide.md" and "download=1" in calls[0]


def test_foreign_links_are_refused():
    with pytest.raises(ConnectorError):
        fetchers.fetch_sharepoint("https://example.com/file.md")


def _mock_no_redirect(monkeypatch, routes):
    calls = []

    def fake(url, headers=None):
        calls.append((url, headers or {}))
        for key, resp in routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(fetchers, "_get_no_redirect", fake)
    return calls


def test_sharepoint_folder_link_is_crawled_anonymously(monkeypatch):
    link = "https://contoso.sharepoint.com/:f:/s/support/EaBcDeF?e=abc"
    redirect = (302, {"Set-Cookie": "FedAuth=BADGE123; Path=/; Secure", "Location":
                      "https://contoso.sharepoint.com/sites/support/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2Fsupport%2FShared%20Documents%2FOrbitMesh&p=true"}, b"")
    files = (200, {}, json.dumps({"value": [{"Name": "guide.md", "ServerRelativeUrl": "/sites/support/Shared Documents/OrbitMesh/guide.md"},
                                            {"Name": "photo.png", "ServerRelativeUrl": "/sites/support/Shared Documents/OrbitMesh/photo.png"}]}).encode())
    subfolders = (200, {}, json.dumps({"value": [{"Name": "Forms", "ServerRelativeUrl": "/x/Forms"}]}).encode())
    content = (200, {}, b"# Guide\n\ntext")
    calls = _mock_no_redirect(monkeypatch, {"EaBcDeF": redirect, ")/Files": files, ")/Folders": subfolders, "/$value": content})
    out = fetchers.fetch_sharepoint(link)
    assert [o.filename for o in out] == ["guide.md"] and out[0].data == b"# Guide\n\ntext"
    assert any(h.get("Cookie") == "FedAuth=BADGE123" for _, h in calls)
    assert any("GetFolderByServerRelativeUrl('/sites/support/Shared%20Documents/OrbitMesh')/Files" in u for u, _ in calls)


def test_sharepoint_folder_link_without_badge_is_not_public(monkeypatch):
    _mock_no_redirect(monkeypatch, {"EaBcDeF": (302, {"Location": "https://login.microsoftonline.com/..."}, b"")})
    with pytest.raises(fetchers.LinkNotPublic):
        fetchers.fetch_sharepoint("https://contoso.sharepoint.com/:f:/s/support/EaBcDeF")


def test_unrecognised_drive_link():
    with pytest.raises(ConnectorError):
        fetchers.fetch_gdrive("https://drive.google.com/something-else")
