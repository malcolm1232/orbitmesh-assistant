"""Fetch markdown from public links - no OAuth, no tenant consent.

Google Drive
    * file links  (`/file/d/<id>`, `open?id=<id>`)      -> `uc?export=download&id=<id>`
    * Google Docs (`/document/d/<id>`)                   -> `export?format=md` (falls back to txt)
    * folder links (`/drive/folders/<id>`)               -> Drive API v3 `files.list` - needs a free
      API key (`GOOGLE_API_KEY`); listing a public folder anonymously is not possible.
SharePoint / OneDrive
    * "Anyone with the link" FILE links                  -> the same URL with `download=1`
    * "Anyone with the link" FOLDER links (`/:f:/`)      -> the mechanism DBSearch.AI's
      SharePoint-link connector measured on a live tenant: GET the link WITHOUT following the
      redirect, SharePoint answers 302 and sets a `FedAuth` cookie (an anonymous badge) whose
      Location names the folder; classic REST `GetFolderByServerRelativeUrl(...)/Files` and
      `/Folders` honour that cookie; `GetFileByServerRelativeUrl(...)/$value` returns bytes.
      No Microsoft identity of any kind. Only markdown/text files are taken.

Every fetch is size-capped and rejects HTML, because a login or "request access" page is the
usual failure mode of a link that is not actually public.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .connectors import MAX_DOC_BYTES, ConnectorError

_UA = "Mozilla/5.0 (compatible; OrbitMeshAssistant/0.1; +https://github.com/orbitmesh-assistant)"
TIMEOUT = 30

_GD_FILE = re.compile(r"drive\.google\.com/(?:file/d/|uc\?(?:.*&)?id=|open\?(?:.*&)?id=)([\w-]{10,})")
_GD_DOC = re.compile(r"docs\.google\.com/document/d/([\w-]{10,})")
_GD_FOLDER = re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([\w-]{10,})")
_SP_HOST = re.compile(r"https?://[\w.-]+\.(?:sharepoint\.com|sharepoint-df\.com|onedrive\.live\.com|1drv\.ms)/", re.I)
_SP_FOLDER = re.compile(r"/:f:/", re.I)
_SP_LAYOUTS = re.compile(r"^(?P<web>.*?)/_layouts/", re.I)
_SP_FORMS = re.compile(r"^(?P<web>.*?)/[^/]+/Forms/[^/]+\.aspx$", re.I)
_SP_TEXT_EXT = (".md", ".markdown", ".txt")


@dataclass
class Fetched:
    filename: str
    data: bytes
    origin: str


class LinkNotPublic(ConnectorError):
    pass


def _get(url: str, *, accept: str = "text/markdown, text/plain;q=0.9, */*;q=0.5") -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - https links only, user-supplied
            data = resp.read(MAX_DOC_BYTES + 1)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            disp = resp.headers.get("Content-Disposition") or ""
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise LinkNotPublic(f"the link is not publicly accessible (HTTP {exc.code})") from exc
        raise ConnectorError(f"fetch failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ConnectorError(f"fetch failed: {exc.reason}") from exc
    if len(data) > MAX_DOC_BYTES:
        raise ConnectorError("document larger than the 5 MB limit")
    return data, ctype, disp


def _filename_from(disp: str, fallback: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;]+)", disp) or re.search(r'filename="?([^";]+)"?', disp)
    name = urllib.parse.unquote(m.group(1)) if m else fallback
    return name if name.lower().endswith((".md", ".markdown", ".txt")) else f"{name}.md"


def _looks_like_html(data: bytes, ctype: str) -> bool:
    head = data[:400].lstrip().lower()
    return "text/html" in ctype or head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _ensure_text(data: bytes, ctype: str, what: str) -> None:
    if _looks_like_html(data, ctype):
        raise LinkNotPublic(f"{what} returned a web page instead of a document - the link is probably not "
                            f"shared publicly ('Anyone with the link'), or it is a folder")


# --- Google Drive ------------------------------------------------------------------------
def fetch_gdrive(url: str, api_key: str = "") -> list[Fetched]:
    if m := _GD_FOLDER.search(url):
        return _gdrive_folder(m.group(1), api_key)
    if m := _GD_DOC.search(url):
        return [_gdrive_doc(m.group(1))]
    if m := _GD_FILE.search(url):
        return [_gdrive_file(m.group(1))]
    raise ConnectorError("not a Google Drive link I recognise (expected /file/d/<id>, /document/d/<id> or /drive/folders/<id>)")


def _gdrive_file(file_id: str) -> Fetched:
    data, ctype, disp = _get(f"https://drive.google.com/uc?export=download&id={file_id}")
    if _looks_like_html(data, ctype) and b"confirm=" in data:
        # Large-file virus-scan interstitial: follow the confirm token.
        m = re.search(rb"confirm=([0-9A-Za-z_-]+)", data)
        if m:
            data, ctype, disp = _get(f"https://drive.google.com/uc?export=download&confirm={m.group(1).decode()}&id={file_id}")
    _ensure_text(data, ctype, "Google Drive")
    return Fetched(filename=_filename_from(disp, f"gdrive-{file_id[:8]}"), data=data,
                   origin=f"https://drive.google.com/file/d/{file_id}")


def _gdrive_doc(doc_id: str) -> Fetched:
    for fmt in ("md", "txt"):
        try:
            data, ctype, disp = _get(f"https://docs.google.com/document/d/{doc_id}/export?format={fmt}")
        except ConnectorError:
            continue
        if not _looks_like_html(data, ctype):
            return Fetched(filename=_filename_from(disp, f"gdoc-{doc_id[:8]}"), data=data,
                           origin=f"https://docs.google.com/document/d/{doc_id}")
    raise LinkNotPublic("Google Doc export returned a web page - share it as 'Anyone with the link' first")


def _gdrive_folder(folder_id: str, api_key: str) -> list[Fetched]:
    if not api_key:
        raise ConnectorError("listing a Google Drive folder needs GOOGLE_API_KEY (a free API key, no OAuth); "
                             "share individual file links instead, or set the key")
    q = urllib.parse.quote(f"'{folder_id}' in parents and trashed = false")
    url = (f"https://www.googleapis.com/drive/v3/files?q={q}&key={api_key}"
           f"&fields=files(id,name,mimeType)&pageSize=200")
    data, ctype, _ = _get(url, accept="application/json")
    try:
        files = json.loads(data).get("files", [])
    except json.JSONDecodeError as exc:
        raise ConnectorError("Drive API returned an unexpected response") from exc
    out: list[Fetched] = []
    for f in files:
        name, mime, fid = f.get("name", ""), f.get("mimeType", ""), f.get("id", "")
        if mime == "application/vnd.google-apps.document":
            out.append(_gdrive_doc(fid))
        elif name.lower().endswith((".md", ".markdown", ".txt")):
            out.append(_gdrive_file(fid))
    if not out:
        raise ConnectorError("the folder holds no markdown (.md) files or Google Docs")
    return out


# --- SharePoint / OneDrive ---------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - stdlib hook
        return None


def _get_no_redirect(url: str, headers: dict | None = None):
    """(status, headers, body) without following redirects - the 302 IS the payload."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            return resp.status, dict(resp.headers.items()), resp.read(MAX_DOC_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), b""
    except urllib.error.URLError as exc:
        raise ConnectorError(f"fetch failed: {exc.reason}") from exc


def _sp_mint(link: str) -> tuple[str, str, str]:
    """Step 1: (badge, web, root). No badge = the link is not (or no longer) anonymous."""
    status, headers, _ = _get_no_redirect(link)
    cookie = headers.get("Set-Cookie", "") or ""
    m = re.search(r"FedAuth=([^;]+)", cookie)
    location = headers.get("Location", "") or ""
    if status not in (301, 302, 303, 307, 308) or not m:
        raise LinkNotPublic("this link is not shared as 'Anyone with the link' (or it expired), so SharePoint asks "
                            "for a sign-in - in SharePoint: Share -> Anyone with the link -> Copy link, on the folder")
    parsed = urllib.parse.urlparse(location)
    root = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
    path = urllib.parse.unquote(parsed.path)
    w = _SP_LAYOUTS.match(path) or _SP_FORMS.match(path)
    if not root or w is None:
        raise ConnectorError("the link opened, but SharePoint's redirect did not name a folder")
    return m.group(1), w.group("web").rstrip("/"), root


def _sp_rest_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/").replace("'", "''")


def _sp_rows(origin: str, web: str, badge: str, folder: str, tail: str) -> list[dict]:
    url = (f"{origin}{web}/_api/web/GetFolderByServerRelativeUrl('{_sp_rest_path(folder)}')/{tail}"
           f"?$select=Name,ServerRelativeUrl,Length,TimeLastModified")
    rows: list[dict] = []
    while url:
        status, _, body = _get_no_redirect(url, {"Cookie": f"FedAuth={badge}", "Accept": "application/json;odata=nometadata"})
        if status != 200:
            raise ConnectorError(f"SharePoint listing failed ({status}) for folder {folder!r}")
        data = json.loads(body or b"{}")
        rows.extend(data.get("value", []))
        url = data.get("odata.nextLink") or data.get("@odata.nextLink") or ""
    return rows


def _sp_folder(link: str) -> list[Fetched]:
    origin = _SP_HOST.match(link).group(0).rstrip("/")
    badge, web, root = _sp_mint(link)
    out: list[Fetched] = []
    queue = [root]
    seen = 0
    while queue and seen < 500:
        folder = queue.pop(0)
        seen += 1
        for f in _sp_rows(origin, web, badge, folder, "Files"):
            name = f.get("Name", "")
            if not name.lower().endswith(_SP_TEXT_EXT):
                continue
            url = f"{origin}{web}/_api/web/GetFileByServerRelativeUrl('{_sp_rest_path(f['ServerRelativeUrl'])}')/$value"
            status, _, data = _get_no_redirect(url, {"Cookie": f"FedAuth={badge}"})
            if status == 403:
                badge, web, root = _sp_mint(link)     # badge expired mid-crawl: re-mint once
                status, _, data = _get_no_redirect(url, {"Cookie": f"FedAuth={badge}"})
            if status != 200:
                raise ConnectorError(f"SharePoint download failed ({status}) for {name!r}")
            if len(data) > MAX_DOC_BYTES:
                raise ConnectorError(f"{name!r} is larger than the 5 MB limit")
            out.append(Fetched(filename=name, data=data, origin=f"{origin}{f['ServerRelativeUrl']}"))
        for sub in _sp_rows(origin, web, badge, folder, "Folders"):
            if sub.get("Name") != "Forms":
                queue.append(sub["ServerRelativeUrl"])
    if not out:
        raise ConnectorError("the shared folder holds no markdown (.md) or text files")
    return out


def fetch_sharepoint(url: str) -> list[Fetched]:
    if not _SP_HOST.match(url):
        raise ConnectorError("not a SharePoint / OneDrive link")
    if _SP_FOLDER.search(url):
        return _sp_folder(url)
    sep = "&" if "?" in url else "?"
    dl = url if "download=1" in url else f"{url}{sep}download=1"
    data, ctype, disp = _get(dl)
    _ensure_text(data, ctype, "SharePoint")
    tail = urllib.parse.unquote(urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]) or "sharepoint"
    return [Fetched(filename=_filename_from(disp, tail), data=data, origin=url)]


def fetch(kind: str, url: str, *, google_api_key: str = "") -> list[Fetched]:
    if kind == "gdrive":
        return fetch_gdrive(url, google_api_key)
    if kind == "sharepoint":
        return fetch_sharepoint(url)
    raise ConnectorError(f"connector kind {kind!r} has nothing to fetch")
