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


def _folder_view(*entries):
    """The markup Drive's embeddedfolderview serves for a public folder: (href, title) per entry."""
    rows = "".join(
        f'<div class="flip-entry" id="entry-{href.rstrip("/").split("/")[-2 if href.endswith("/view") or href.endswith("/edit") else -1]}" tabindex="0">'
        f'<div class="flip-entry-info"><a href="{href}" target="_blank"><div class="flip-entry-title">{title}</div></a></div></div>'
        for href, title in entries)
    return FakeResp(f'<html><body><div class="flip-entries">{rows}</div></body></html>'.encode(), ctype="text/html")


def test_gdrive_public_folder_is_listed_without_a_key_and_subfolders_are_followed(monkeypatch):
    calls = _mock(monkeypatch, {
        "embeddedfolderview?id=1RootFolderAAAA": _folder_view(
            ("https://drive.google.com/file/d/1MdFileAAAAAAAA/view", "N1 field notes.md"),
            ("https://docs.google.com/document/d/1GoogleDocAAAAA/edit", "Pro FAQ"),
            ("https://drive.google.com/file/d/1PictureAAAAAAA/view", "diagram.png"),
            ("https://drive.google.com/drive/folders/1SubFolderAAAAA", "Archive &amp; old"),
        ),
        "embeddedfolderview?id=1SubFolderAAAAA": _folder_view(
            ("https://drive.google.com/file/d/1TxtFileAAAAAAA/view", "release-2023.txt"),
            ("https://drive.google.com/drive/folders/1RootFolderAAAA", "loop back to the root"),
        ),
        "id=1MdFileAAAAAAAA": FakeResp(b"# N1 field notes\n\nbody"),
        "document/d/1GoogleDocAAAAA/export?format=md": FakeResp(b"# Pro FAQ\n\nbody", disp='attachment; filename="Pro FAQ.md"'),
        "id=1TxtFileAAAAAAA": FakeResp(b"release notes", ctype="text/plain"),
    })
    out = fetchers.fetch_gdrive("https://drive.google.com/drive/folders/1RootFolderAAAA?usp=drive_link", api_key="")
    assert [(o.filename, o.data[:9]) for o in out] == [
        ("N1 field notes.md", b"# N1 fiel"), ("Pro FAQ.md", b"# Pro FAQ"), ("release-2023.txt", b"release n")]
    assert not any("googleapis.com" in c for c in calls)            # no key, no API
    assert not any("1PictureAAAAAAA" in c for c in calls)            # non-text files are never downloaded
    assert sum("embeddedfolderview?id=1RootFolderAAAA" in c for c in calls) == 1   # the loop back is not re-walked


def test_gdrive_private_folder_is_reported_as_not_shared_not_as_a_missing_key(monkeypatch):
    err = fetchers.urllib.error.HTTPError("https://drive.google.com/embeddedfolderview", 401, "Unauthorized", {}, None)
    _mock(monkeypatch, {"embeddedfolderview?id=1PrivateFolderA": err})
    with pytest.raises(fetchers.LinkNotPublic, match="Anyone with the link") as exc:
        fetchers.fetch_gdrive("https://drive.google.com/drive/folders/1PrivateFolderA?usp=drive_link")
    assert "GOOGLE_API_KEY" not in str(exc.value)


def test_gdrive_folder_with_no_text_files_says_so(monkeypatch):
    _mock(monkeypatch, {"embeddedfolderview?id=1ImagesOnlyAAAA": _folder_view(
        ("https://drive.google.com/file/d/1PictureAAAAAAA/view", "diagram.png"))})
    with pytest.raises(ConnectorError, match="no markdown"):
        fetchers.fetch_gdrive("https://drive.google.com/drive/folders/1ImagesOnlyAAAA")


def test_gdrive_folder_uses_the_drive_api_when_a_key_is_set(monkeypatch):
    listing = json.dumps({"files": [{"id": "1FileAAAAAAAAAA", "name": "a.md", "mimeType": "text/markdown"},
                                    {"id": "1ImgBBBBBBBBBBB", "name": "pic.png", "mimeType": "image/png"}]}).encode()
    calls = _mock(monkeypatch, {"googleapis.com/drive/v3/files?q=": FakeResp(listing, ctype="application/json"),
                                "id=1FileAAAAAAAAAA": FakeResp(b"# A\n\ntext", disp='attachment; filename="a.md"')})
    out = fetchers.fetch_gdrive("https://drive.google.com/drive/folders/1FolderIdXyzAbc", api_key="k")
    assert [(o.filename, o.data) for o in out] == [("a.md", b"# A\n\ntext")]
    assert not any("embeddedfolderview" in c for c in calls)


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
