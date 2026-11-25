"""YouTube Data API v3: OAuth credentials, resumable upload, readback privacy. Có DemoYouTubeClient (không upload thật)."""
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from ...config import Settings, get_settings
from ...errors import BlockedByCredentials, PermanentError, RetryableError
from ...security import redact
from .privacy import PRIVATE, assert_private_body

log = logging.getLogger(__name__)
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"  # doanh thu (kênh đã bật kiếm tiền)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    ANALYTICS_SCOPE,  # Ban Tăng trưởng: chỉ ĐỌC số liệu, không sửa gì trên kênh
    MONETARY_SCOPE,
]
RETRIABLE_STATUS = {500, 502, 503, 504}


class YouTubeClient(Protocol):
    is_demo: bool

    def get_my_channel(self) -> dict: ...
    def find_existing_by_tag(self, tag: str) -> str | None: ...
    def insert_video(self, body: dict, file_path: Path, progress: Callable[[float], None] | None = None) -> str: ...
    def set_thumbnail(self, video_id: str, path: Path) -> None: ...
    def get_privacy(self, video_id: str) -> str | None: ...
    def force_private(self, video_id: str) -> None: ...


# ---------------------------------------------------------------- Google


def credentials_from_json(token_json: str):
    from google.oauth2.credentials import Credentials

    return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)


class GoogleYouTubeClient:
    is_demo = False

    def __init__(self, credentials: Any, on_refresh: Callable[[str], None] | None = None, service: Any = None,
                 chunk_size: int = 8 * 1024 * 1024, max_retries: int = 8):
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except Exception as e:
                raise BlockedByCredentials(f"Không làm mới được OAuth token, cần kết nối lại kênh: {redact(e)}") from e
            if on_refresh:
                on_refresh(credentials.to_json())
        self.yt = service or build("youtube", "v3", credentials=credentials, cache_discovery=False)
        self.chunk_size, self.max_retries = chunk_size, max_retries

    def _exec(self, req):
        from googleapiclient.errors import HttpError

        for attempt in range(self.max_retries):
            try:
                return req.execute()
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS and attempt + 1 < self.max_retries:
                    time.sleep(min(60, 2 ** attempt + random.random()))
                    continue
                if e.resp.status in (401, 403):
                    raise BlockedByCredentials(f"YouTube từ chối quyền ({e.resp.status}): {redact(e)}") from e
                raise PermanentError(f"YouTube API {e.resp.status}: {redact(e)}") from e
            except (OSError, ConnectionError) as e:
                if attempt + 1 < self.max_retries:
                    time.sleep(min(60, 2 ** attempt + random.random()))
                    continue
                raise RetryableError(f"Mạng lỗi khi gọi YouTube: {type(e).__name__}") from e

    def get_my_channel(self) -> dict:
        r = self._exec(self.yt.channels().list(part="id,snippet,contentDetails", mine=True))
        items = r.get("items", [])
        if not items:
            raise BlockedByCredentials("Tài khoản OAuth không có kênh YouTube")
        it = items[0]
        return {"id": it["id"], "title": it["snippet"]["title"],
                "uploads_playlist": it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")}

    def find_existing_by_tag(self, tag: str) -> str | None:
        """Đối soát trước khi upload lại: tìm video gần đây có tag idempotency (chống đăng trùng sau timeout)."""
        ch = self.get_my_channel()
        if not ch.get("uploads_playlist"):
            return None
        r = self._exec(self.yt.playlistItems().list(part="contentDetails", playlistId=ch["uploads_playlist"], maxResults=50))
        ids = [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        if not ids:
            return None
        v = self._exec(self.yt.videos().list(part="snippet", id=",".join(ids)))
        for it in v.get("items", []):
            if tag in (it.get("snippet", {}).get("tags") or []):
                return it["id"]
        return None

    def insert_video(self, body: dict, file_path: Path, progress=None) -> str:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        assert_private_body(body)  # lớp chặn thứ 2, ngay trước khi gọi API
        media = MediaFileUpload(str(file_path), mimetype="video/mp4", chunksize=self.chunk_size, resumable=True)
        req = self.yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=False)
        response, attempt = None, 0
        while response is None:
            try:
                status, response = req.next_chunk()
                if status and progress:
                    progress(status.progress())
                attempt = 0
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS and attempt < self.max_retries:
                    attempt += 1
                    time.sleep(min(60, 2 ** attempt + random.random()))
                    continue  # resumable: tiếp tục từ byte đã nhận
                raise PermanentError(f"Upload lỗi {e.resp.status}: {redact(e)}") from e
            except (OSError, ConnectionError) as e:
                if attempt < self.max_retries:
                    attempt += 1
                    time.sleep(min(60, 2 ** attempt + random.random()))
                    continue
                raise RetryableError(f"Mất mạng khi upload: {type(e).__name__}") from e
        if "id" not in response:
            raise PermanentError(f"Upload không trả video id: {response}")
        return response["id"]

    def set_thumbnail(self, video_id: str, path: Path) -> None:
        from googleapiclient.http import MediaFileUpload

        self._exec(self.yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(path), mimetype="image/jpeg")))

    def get_privacy(self, video_id: str) -> str | None:
        r = self._exec(self.yt.videos().list(part="status", id=video_id))
        items = r.get("items", [])
        return items[0]["status"]["privacyStatus"] if items else None

    def force_private(self, video_id: str) -> None:
        """Chỉ dùng theo hướng an toàn: kéo về private nếu readback khác private."""
        r = self._exec(self.yt.videos().list(part="status", id=video_id))
        st = r["items"][0]["status"]
        st["privacyStatus"] = PRIVATE
        st.pop("publishAt", None)
        body = {"id": video_id, "status": st}
        assert_private_body(body)
        self._exec(self.yt.videos().update(part="status", body=body))


# ---------------------------------------------------------------- DEMO


class DemoYouTubeClient:
    """KHÔNG upload thật. Ghi 'video' vào artifacts/demo_youtube/. Có cờ giả lập lỗi cho test."""
    is_demo = True

    def __init__(self, channel_id: str = "UC_DEMO_CHANNEL_000000", channel_title: str = "[DEMO] Kênh thử nghiệm",
                 fail_network_times: int = 0, report_privacy: str | None = None, state_dir: Path | None = None,
                 fail_after_insert_times: int = 0):
        self.channel_id, self.channel_title = channel_id, channel_title
        self.fail_network_times = fail_network_times
        self.report_privacy = report_privacy
        self.dir = state_dir or (get_settings().artifacts_dir / "demo_youtube")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fail_after_insert_times = fail_after_insert_times  # video đã tạo nhưng client timeout
        self.insert_calls = 0
        self.force_private_calls = 0

    def _db(self) -> dict:
        p = self.dir / "videos.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _save(self, db: dict) -> None:
        (self.dir / "videos.json").write_text(json.dumps(db, ensure_ascii=False, indent=1))

    def get_my_channel(self) -> dict:
        return {"id": self.channel_id, "title": self.channel_title, "uploads_playlist": "UU_DEMO"}

    def find_existing_by_tag(self, tag: str) -> str | None:
        for vid, v in self._db().items():
            if tag in v["body"]["snippet"].get("tags", []) and v["channel_id"] == self.channel_id:
                return vid
        return None

    def insert_video(self, body: dict, file_path: Path, progress=None) -> str:
        assert_private_body(body)
        self.insert_calls += 1
        if self.fail_network_times > 0:
            self.fail_network_times -= 1
            if progress:
                progress(0.4)
            raise RetryableError("DEMO: mất mạng giữa chừng khi upload (giả lập)")
        vid = "DEMO-" + uuid.uuid4().hex[:11]
        db = self._db()
        db[vid] = {"body": body, "file": str(file_path), "size": Path(file_path).stat().st_size,
                   "channel_id": self.channel_id, "privacyStatus": body["status"]["privacyStatus"]}
        self._save(db)
        if self.fail_after_insert_times > 0:
            self.fail_after_insert_times -= 1
            raise RetryableError("DEMO: timeout sau khi YouTube đã nhận video (giả lập)")
        if progress:
            progress(1.0)
        return vid

    def set_thumbnail(self, video_id: str, path: Path) -> None:
        db = self._db()
        db[video_id]["thumbnail"] = str(path)
        self._save(db)

    def get_privacy(self, video_id: str) -> str | None:
        if self.report_privacy:
            return self.report_privacy
        v = self._db().get(video_id)
        return v["privacyStatus"] if v else None

    def force_private(self, video_id: str) -> None:
        self.force_private_calls += 1
        db = self._db()
        db[video_id]["privacyStatus"] = PRIVATE
        self._save(db)
        self.report_privacy = None

    def simulate_owner_published(self, video_id: str) -> None:
        """Chỉ dùng trong demo/test: mô phỏng CHỦ KÊNH tự công khai trên Studio."""
        db = self._db()
        db[video_id]["privacyStatus"] = "public"
        self._save(db)


def make_oauth_flow(settings: Settings, state: str):
    from google_auth_oauthlib.flow import Flow

    redirect = settings.public_base_url.rstrip("/") + "/api/oauth/google/callback"
    if settings.google_client_secrets_json:
        cfg = json.loads(settings.google_client_secrets_json.get_secret_value())
        return Flow.from_client_config(cfg, scopes=SCOPES, state=state, redirect_uri=redirect)
    if settings.google_client_secrets_file and Path(settings.google_client_secrets_file).exists():
        return Flow.from_client_secrets_file(str(settings.google_client_secrets_file), scopes=SCOPES, state=state, redirect_uri=redirect)
    raise BlockedByCredentials("Chưa có OAuth client — dán client_secret.json trong Thiết lập → Cấu hình API → YouTube")
