"""둘의 하루 · 오프라인/LAN용 커플 다이어리 (SQLite).

소스 파일은 app.py와 requirements.txt, 두 개입니다. Python 3.11/3.12 권장.
외부 DB, API 키, Streamlit Secrets가 필요하지 않습니다.

[설치 / 실행]
1. 같은 폴더에 두 파일을 두고, 최초 설치 시 실행합니다. 설치에는 인터넷이 필요합니다.
   python -m pip install -r requirements.txt
2. 다음 명령으로 실행합니다. 설치 후 앱 사용에는 인터넷이 필요하지 않습니다.
   python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --browser.gatherUsageStats=false
3. PC 브라우저에서 http://localhost:8501 을 엽니다.
4. 첫 화면에서 두 사람의 이름과 서로 다른 8~128자 비밀번호를 한 번 정합니다.
   공개 공유 전에 PC에서 먼저 초기 설정을 마치세요. 첫 사용자는 A로 입장합니다.
5. 휴대폰과 PC를 같은 Wi-Fi 또는 로컬 네트워크에 연결합니다.
6. 앱 상단 QR -> 'PC의 접속 주소'에 PC의 실제 Wi-Fi IPv4 주소를 입력합니다.
   예: http://192.168.0.23:8501 (예시이므로 실제 PC 주소로 바꾸세요.)
   Windows는 네트워크 속성의 IPv4 주소, macOS는 Wi-Fi 세부사항의 IP 주소를 봅니다.
   localhost / 127.0.0.1 / 0.0.0.0 은 다른 휴대폰에서 사용할 주소가 아닙니다.
7. 'QR 만들기'를 누르고 상대 휴대폰으로 스캔합니다. 각자 자기 이름으로 입장합니다.

[연결이 안 될 때]
- PC에서 앱이 실행 중이어야 합니다. PC 종료/절전/앱 중지 시 접속할 수 없습니다.
- PC 방화벽에서 신뢰하는 사설 네트워크의 Python/TCP 8501 접속을 허용하세요.
- 게스트 Wi-Fi의 기기 간 통신 차단, 서로 다른 네트워크, 잘못된 PC IP를 확인하세요.
- PC의 Wi-Fi 주소가 바뀌면 QR 주소도 다시 저장합니다. 인터넷 포트 개방은 불필요합니다.

[로컬 저장 / 백업]
실행하면 app.py 옆에 couple_diary.sqlite3 데이터 파일이 자동 생성됩니다.
두 휴대폰이 이 PC의 한 데이터 파일을 공유합니다. 휴대폰별 독립 저장은 아닙니다.
앱을 다시 실행해도 같은 폴더/DB를 사용하면 계정과 일기, 읽음이 유지됩니다.
백업하려면 앱을 완전히 종료한 뒤 이 DB 파일을 안전한 곳에 복사하세요.
실행 중에는 SQLite의 -wal/-shm 보조 파일이 생길 수 있습니다. 임의 삭제하지 마세요.
GitHub에 올릴 것은 소스 두 파일뿐입니다. DB/보조 파일은 개인정보이므로 올리지 마세요.
기존 외부 저장소 데이터는 자동 이전하지 않습니다.

[기능 / 제한]
- 한 사람당 날짜별 일기 1개, 기분 5종, 최대 240자, 저장한 일기 수정 가능.
- 상대의 명시적인 읽음 체크/확인 시각. 내용을 수정하면 읽음 초기화.
- 동시 수정의 버전 충돌 방지. 20초마다 보이는 화면 동기화, ↻로 즉시 확인.
- 입력 중 초안은 탭 메모리에 유지되지만 새로고침/로그아웃 시 사라집니다.
- 주 화면은 모바일 한 화면으로 구성. 긴 글/QR은 같은 페이지의 팝업으로 표시합니다.
- 작은 화면, 글자 확대, 가상 키보드, 팝업 안에서는 접근성을 위해 스크롤을 허용합니다.
- 인터넷 없이 쓰되, 두 기기 간 Wi-Fi/LAN 연결과 실행 중인 PC는 필요합니다.
  각 휴대폰이 네트워크까지 완전히 끊긴 상태의 동기화/PWA 앱은 아닙니다.
- Streamlit Cloud에서도 실행은 가능하지만 그 경우 인터넷이 필요합니다.
  Cloud의 로컬 파일은 재배포/교체 시 유실될 수 있으므로 영구 저장용으로 쓰지 마세요.

[보안]
비밀번호는 무작위 salt와 PBKDF2-SHA256(600,000회)으로 해시해 로컬 DB에 보관합니다.
QR에는 주소만 들어갑니다. 평문 비밀번호를 코드/QR/DB에 저장하지 않습니다.
일기 DB 자체는 암호화되지 않으며 PC 관리자는 내용을 볼 수 있습니다.
로컬 HTTP 연결은 전송 구간이 암호화되지 않습니다. 신뢰하는 사설 Wi-Fi에서만 쓰세요.
인터넷에 포트를 개방하거나 민감한 기록을 공개 네트워크에서 쓰지 마세요.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import ipaddress
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

import qrcode
import streamlit as st

DB_PATH = Path(__file__).resolve().with_name("couple_diary.sqlite3")
KST = timezone(timedelta(hours=9))
MAX_CHARS = 240
SESSION_SECONDS = 12 * 60 * 60
SYNC_SECONDS = 20
PASSWORD_ITERATIONS = 600_000
COMPONENT_KEY = "couple_ui_offline"
MOODS = [
    {"id": "happy", "emoji": "😍", "label": "행복"},
    {"id": "calm", "emoji": "🙂", "label": "평온"},
    {"id": "tired", "emoji": "😴", "label": "피곤"},
    {"id": "sad", "emoji": "😢", "label": "우울"},
    {"id": "upset", "emoji": "😤", "label": "속상"},
]
MOOD_IDS = {m["id"] for m in MOODS}


class StorageError(Exception):
    pass


class VersionConflict(Exception):
    pass


class UserMessage(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    initialized: bool = False
    name_a: str = "별이"
    name_b: str = "달이"
    auth_stamp: str = ""
    app_url: str = ""
    sync_seconds: int = SYNC_SECONDS

    @property
    def names(self) -> dict[str, str]:
        return {"A": self.name_a, "B": self.name_b}


def today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def parse_day(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise UserMessage("날짜를 다시 선택해 주세요.")
    try:
        selected = date.fromisoformat(value)
    except ValueError:
        raise UserMessage("올바른 날짜를 선택해 주세요.") from None
    if not date(2000, 1, 1) <= selected <= datetime.now(KST).date():
        raise UserMessage("2000년 이후, 오늘까지의 날짜만 선택할 수 있어요.")
    return value


def validate_role(role: Any) -> str:
    if role not in ("A", "B"):
        raise UserMessage("내 이름을 먼저 선택해 주세요.")
    return role


def validate_text(text: Any) -> str:
    if not isinstance(text, str):
        raise UserMessage("일기 내용을 입력해 주세요.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if (not text or len(text) > MAX_CHARS
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text)):
        raise UserMessage(f"빈 글은 보낼 수 없어요. {MAX_CHARS}자 안으로 적어 주세요.")
    return text


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                                 PASSWORD_ITERATIONS).hex()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt, expected = encoded.split("$")
        count = int(iterations)
        if scheme != "pbkdf2_sha256" or not 100_000 <= count <= 2_000_000:
            return False
        if not re.fullmatch(r"[a-f0-9]{32}", salt) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), count).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return False


def normalize_url(value: Any) -> str:
    """Validate an address only. Never request it, resolve DNS, or scan a network."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not 1 <= len(value) <= 500 or re.search(r"[\s\x00-\x1f\x7f<>\"\\]", value):
        return ""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            return ""
        try:
            address = ipaddress.ip_address(host)
            if address.is_unspecified or address.is_multicast:
                return ""
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
                return ""
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    except ValueError:
        return ""


def loopback_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return True
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    role TEXT PRIMARY KEY CHECK (role IN ('A','B')),
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    day TEXT NOT NULL,
    author TEXT NOT NULL REFERENCES members(role),
    mood TEXT NOT NULL CHECK (mood IN ('happy','calm','tired','sad','upset')),
    text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 240),
    version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    read_by TEXT NOT NULL DEFAULT '' CHECK (read_by IN ('','A','B') AND read_by <> author),
    read_at TEXT NOT NULL DEFAULT '',
    last_request TEXT NOT NULL,
    PRIMARY KEY (day, author)
);
CREATE TABLE IF NOT EXISTS login_attempts (
    role TEXT PRIMARY KEY CHECK (role IN ('A','B')),
    attempts INTEGER NOT NULL,
    window_start REAL NOT NULL
);
"""


class LocalStore:
    """Short-lived connections + transactions: safe across two phones/threads."""
    def __init__(self, path: Path | str):
        self.path = Path(path)
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StorageError("이 데이터 파일은 다른 버전의 앱에서 만들어졌어요. 파일을 지우지 말고 확인해 주세요.")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            if version == 0:
                conn.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = None
        try:
            conn = sqlite3.connect(str(self.path), timeout=8, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=8000")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except sqlite3.Error:
            if conn is not None:
                conn.rollback()
            raise StorageError("로컬 저장 파일을 열거나 저장하지 못했어요. 앱 폴더의 쓰기 권한과 여유 공간을 확인해 주세요.") from None
        finally:
            if conn is not None:
                conn.close()

    def settings(self) -> Settings:
        with self.connection() as conn:
            meta = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
            names = dict(conn.execute("SELECT role, name FROM members").fetchall())
        stamp = meta.get("setup_id", "")
        if not stamp and not names:
            return Settings()
        if not stamp or set(names) != {"A", "B"}:
            raise StorageError("계정 설정을 확인하지 못했어요. 데이터 파일을 삭제하지 말고 백업부터 해 주세요.")
        return Settings(True, names["A"], names["B"], stamp, normalize_url(meta.get("share_url", "")))

    def initialize(self, name_a: Any, name_b: Any, pass_a: Any, pass_b: Any) -> Settings:
        if not all(isinstance(v, str) for v in (name_a, name_b, pass_a, pass_b)):
            raise UserMessage("두 사람의 이름과 비밀번호를 입력해 주세요.")
        names = [name_a.strip(), name_b.strip()]
        if (any(not 1 <= len(n) <= 12 or re.search(r"[\x00-\x1f\x7f]", n) for n in names)
                or names[0] == names[1]):
            raise UserMessage("이름은 서로 다른 1~12자로 정해 주세요.")
        if any(not 8 <= len(p) <= 128 or re.search(r"[\x00-\x1f\x7f]", p) for p in (pass_a, pass_b)):
            raise UserMessage("각 비밀번호는 8~128자로 정해 주세요. 줄바꿈은 넣지 않아요.")
        if hmac.compare_digest(hashlib.sha256(pass_a.encode()).digest(), hashlib.sha256(pass_b.encode()).digest()):
            raise UserMessage("서로의 계정이 구분되도록 비밀번호를 다르게 정해 주세요.")
        hashes = [password_hash(pass_a), password_hash(pass_b)]
        with self.connection(write=True) as conn:
            if (conn.execute("SELECT 1 FROM metadata WHERE key='setup_id'").fetchone()
                    or conn.execute("SELECT 1 FROM members LIMIT 1").fetchone()):
                raise UserMessage("이미 다이어리가 만들어졌어요. 새로고침한 뒤 입장해 주세요.")
            conn.executemany("INSERT INTO members(role, name, password_hash) VALUES (?, ?, ?)",
                             [("A", names[0], hashes[0]), ("B", names[1], hashes[1])])
            conn.execute("INSERT INTO metadata(key, value) VALUES ('setup_id', ?)", (uuid.uuid4().hex,))
        return self.settings()

    def authenticate(self, role: Any, password: Any) -> bool:
        role = validate_role(role)
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise UserMessage("이름과 비밀번호를 확인해 주세요.")
        current_time = time.time()
        with self.connection(write=True) as conn:
            member = conn.execute("SELECT password_hash FROM members WHERE role=?", (role,)).fetchone()
            if member is None:
                raise UserMessage("먼저 이 PC에 다이어리를 만들어 주세요.")
            row = conn.execute("SELECT attempts, window_start FROM login_attempts WHERE role=?", (role,)).fetchone()
            attempts, started = (row[0], row[1]) if row else (0, current_time)
            if current_time - started >= 300 or current_time < started:
                attempts, started = 0, current_time
            if attempts >= 10:
                raise UserMessage("로그인 시도가 많아요. 최대 5분 뒤 다시 시도해 주세요.")
            conn.execute("INSERT INTO login_attempts(role, attempts, window_start) VALUES (?, ?, ?) "
                         "ON CONFLICT(role) DO UPDATE SET attempts=excluded.attempts, window_start=excluded.window_start",
                         (role, attempts + 1, started))
        matches = password_matches(password, member[0])
        if matches:
            with self.connection(write=True) as conn:
                conn.execute("DELETE FROM login_attempts WHERE role=?", (role,))
        return matches

    def get_day(self, day: str) -> dict[str, dict[str, Any] | None]:
        day = parse_day(day)
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM entries WHERE day=?", (day,)).fetchall()
        result: dict[str, Any] = {"A": None, "B": None}
        for row in rows:
            result[row["author"]] = dict(row)
        return result

    def save(self, day: str, role: str, mood: Any, text: Any,
             expected_version: Any, request_id: Any) -> dict[str, Any]:
        day, role, text = parse_day(day), validate_role(role), validate_text(text)
        if not isinstance(mood, str) or mood not in MOOD_IDS:
            raise UserMessage("오늘의 기분을 먼저 골라 주세요.")
        if not isinstance(expected_version, str) or not re.fullmatch(r"(?:[a-f0-9]{32})?", expected_version):
            raise UserMessage("저장본을 다시 불러온 뒤 보내 주세요.")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
            raise UserMessage("보내기를 다시 눌러 주세요.")
        with self.connection(write=True) as conn:
            row = conn.execute("SELECT * FROM entries WHERE day=? AND author=?", (day, role)).fetchone()
            current = dict(row) if row else None
            if current and current["last_request"] == request_id:
                return current
            if (current["version"] if current else "") != expected_version:
                raise VersionConflict("다른 기기에서 글이 바뀌었어요. 초안을 복사해 두고 '저장본'을 불러온 뒤 다시 수정해 주세요.")
            if current and current["text"] == text and current["mood"] == mood:
                return current
            stamp = now_iso()
            doc = {"day": day, "author": role, "mood": mood, "text": text, "version": uuid.uuid4().hex,
                   "created_at": current["created_at"] if current else stamp, "updated_at": stamp,
                   "read_by": "", "read_at": "", "last_request": request_id}
            conn.execute("INSERT INTO entries(day, author, mood, text, version, created_at, updated_at, read_by, read_at, last_request) "
                         "VALUES (:day, :author, :mood, :text, :version, :created_at, :updated_at, :read_by, :read_at, :last_request) "
                         "ON CONFLICT(day, author) DO UPDATE SET mood=excluded.mood, text=excluded.text, version=excluded.version, "
                         "updated_at=excluded.updated_at, read_by='', read_at='', last_request=excluded.last_request", doc)
        return doc

    def mark_read(self, day: str, viewer: str, version: Any) -> dict[str, Any]:
        day, viewer = parse_day(day), validate_role(viewer)
        if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{32}", version):
            raise UserMessage("새 일기를 불러온 뒤 다시 체크해 주세요.")
        author = "B" if viewer == "A" else "A"
        with self.connection(write=True) as conn:
            row = conn.execute("SELECT * FROM entries WHERE day=? AND author=?", (day, author)).fetchone()
            if row is None:
                raise UserMessage("확인할 일기가 없어요. ↻로 다시 불러와 주세요.")
            doc = dict(row)
            if doc["version"] != version:
                raise VersionConflict("상대가 방금 글을 수정했어요. 새 내용을 읽고 다시 체크해 주세요.")
            if not doc["read_by"]:
                doc.update(read_by=viewer, read_at=now_iso())
                conn.execute("UPDATE entries SET read_by=?, read_at=? WHERE day=? AND author=?",
                             (doc["read_by"], doc["read_at"], day, author))
        return doc

    def set_share_url(self, value: Any) -> str:
        url = normalize_url(value)
        if not url or loopback_url(url):
            raise UserMessage("http://실제-PC-IP:8501 형식으로 넣어 주세요. localhost나 0.0.0.0은 휴대폰 접속용이 아니에요.")
        with self.connection(write=True) as conn:
            conn.execute("INSERT INTO metadata(key, value) VALUES ('share_url', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (url,))
        return url


@st.cache_resource(show_spinner=False)
def get_store(path: str) -> LocalStore:
    return LocalStore(path)


def authenticated_member(settings: Settings) -> str | None:
    auth = st.session_state.get("_auth", {})
    if (settings.initialized and isinstance(auth, dict) and auth.get("role") in ("A", "B")
            and auth.get("stamp") == settings.auth_stamp
            and isinstance(auth.get("issued_at"), (int, float))
            and 0 <= time.time() - auth["issued_at"] < SESSION_SECONDS):
        return auth["role"]
    st.session_state.pop("_auth", None)
    return None


def notice(text: str, kind: str = "success") -> None:
    st.session_state["_notice"] = {"text": text, "kind": kind, "at": time.time()}


def remember_document(day: str, role: str, doc: dict[str, Any]) -> None:
    cached = st.session_state.get("_last_snapshot", {})
    known = dict(cached["docs"]) if cached.get("day") == day else {"A": None, "B": None}
    known[role] = doc
    st.session_state["_last_snapshot"] = {"day": day, "docs": known}


def handle_event() -> None:
    event = st.session_state.get(COMPONENT_KEY, {}).get("event")
    if not isinstance(event, dict):
        return
    eid, kind, payload = event.get("id"), event.get("kind"), event.get("payload", {})
    if not isinstance(eid, str) or not 1 <= len(eid) <= 80 or not isinstance(kind, str):
        return
    if eid == st.session_state.get("_last_event"):
        return
    st.session_state["_last_event"] = eid
    if not isinstance(payload, dict):
        payload = {}
    ack: dict[str, Any] = {"id": eid, "kind": kind, "ok": False}
    try:
        store = get_store(str(DB_PATH))
        settings = store.settings()
        if kind == "setup":
            settings = store.initialize(payload.get("name_a"), payload.get("name_b"),
                                        payload.get("password_a"), payload.get("password_b"))
            st.session_state["_auth"] = {"role": "A", "issued_at": time.time(), "stamp": settings.auth_stamp}
            st.session_state["_follow_today"] = True
            st.session_state.pop("_last_snapshot", None)
            notice("이 PC에 다이어리를 만들었어요. QR에서 휴대폰 접속 주소를 정해 주세요.")
        elif kind == "login":
            role = validate_role(payload.get("role"))
            if not store.authenticate(role, payload.get("password")):
                raise UserMessage("비밀번호가 맞지 않아요. 내 이름을 선택했는지도 확인해 주세요.")
            st.session_state["_auth"] = {"role": role, "issued_at": time.time(), "stamp": settings.auth_stamp}
            st.session_state["_follow_today"] = True
            st.session_state.pop("_last_snapshot", None)
            notice(f"{settings.names[role]}님, 오늘의 마음을 남겨 주세요.")
        elif kind == "logout":
            st.session_state.pop("_auth", None)
            st.session_state.pop("_last_snapshot", None)
            notice("안전하게 나왔어요.")
        else:
            role = authenticated_member(settings)
            if not role:
                raise UserMessage("내 이름으로 다시 입장해 주세요. 로그인은 열린 연결에서 최대 12시간 유지돼요.")
            if kind == "load":
                selected = parse_day(payload.get("day"))
                st.session_state["_selected_day"] = selected
                st.session_state["_follow_today"] = selected == today_kst()
            elif kind == "refresh":
                pass
            elif kind == "share_url":
                store.set_share_url(payload.get("url"))
                notice("QR 주소를 저장했어요. 같은 Wi-Fi의 휴대폰으로 찍어 주세요.")
            elif kind == "save":
                selected = parse_day(payload.get("day"))
                doc = store.save(selected, role, payload.get("mood"), payload.get("text"), payload.get("version", ""), eid)
                remember_document(selected, role, doc)
                st.session_state["_selected_day"] = selected
                st.session_state["_follow_today"] = selected == today_kst()
                ack.update(day=selected, version=doc["version"])
                notice("마음을 이 PC에 저장했어요. 상대가 체크하면 확인 시각이 보여요.")
            elif kind == "read":
                selected = parse_day(payload.get("day"))
                doc = store.mark_read(selected, role, payload.get("version"))
                remember_document(selected, "B" if role == "A" else "A", doc)
                notice("읽었다는 마음을 전했어요. ✓")
            else:
                raise UserMessage("지원하지 않는 동작이에요.")
        ack["ok"] = True
    except (UserMessage, VersionConflict, StorageError) as exc:
        notice(str(exc), "error")
    finally:
        for key in ("password", "password_a", "password_b"):
            payload.pop(key, None)
        st.session_state["_ack"] = ack


def public_url(settings: Settings) -> str:
    if settings.app_url:
        return normalize_url(settings.app_url)
    try:
        return normalize_url(str(st.context.url))
    except Exception:
        return ""


@st.cache_data(show_spinner=False, max_entries=32)
def qr_data_uri(url: str) -> str:
    qr = qrcode.QRCode(box_size=8, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def frontend_data(store: LocalStore, settings: Settings) -> dict[str, Any]:
    me = authenticated_member(settings)
    today = today_kst()
    selected = today if st.session_state.get("_follow_today", True) else st.session_state.get("_selected_day", today)
    docs: dict[str, Any] = {"A": None, "B": None}
    storage_error = False
    if me:
        try:
            docs = store.get_day(selected)
            st.session_state["_last_snapshot"] = {"day": selected, "docs": docs}
        except StorageError as exc:
            storage_error = True
            previous = st.session_state.get("_last_snapshot", {})
            if previous.get("day") == selected:
                docs = previous["docs"]
            notice(str(exc), "error")
    app_url = public_url(settings)
    qr_ok = bool(app_url and not loopback_url(app_url))
    current_notice = st.session_state.get("_notice")
    if current_notice and time.time() - current_notice["at"] > 8:
        current_notice = None
    return {
        "client_id": st.session_state.setdefault("_client_id", uuid.uuid4().hex),
        "needs_setup": not settings.initialized,
        "me": me or "", "names": settings.names, "today": today, "day": selected,
        "own": docs[me] if me else None,
        "partner": docs["B" if me == "A" else "A"] if me else None,
        "moods": MOODS, "max_chars": MAX_CHARS, "sync_seconds": settings.sync_seconds,
        "offline": storage_error, "server_time": now_iso(),
        "app_url": app_url if qr_ok else "", "qr": qr_data_uri(app_url) if qr_ok else "",
        "local_url": not qr_ok,
        "ack": st.session_state.get("_ack"), "notice": current_notice,
    }


HTML = r'''
<div class="shell" lang="ko">
  <header class="app-header">
    <div class="brand"><span class="brand-heart" aria-hidden="true">♥</span><div><h1>둘의 하루</h1><p>오늘도, 너에게</p></div></div>
    <nav class="header-tools" aria-label="앱 도구">
      <button id="qr-open" class="tool" type="button" aria-label="접속 QR 코드">QR</button>
      <button id="refresh" class="tool" type="button" aria-label="새로 동기화">↻</button>
      <button id="logout" class="tool exit" type="button">나가기</button>
    </nav>
  </header>

  <section id="login-view" class="login-view">
    <div class="login-card">
      <span class="envelope" aria-hidden="true">💌</span>
      <h2 id="welcome-title">둘만의 작은 기록</h2>
      <p id="welcome-intro" class="intro">기분 하나, 짧은 글 하나.<br>오늘의 나를 너에게 남겨요.</p>
      <form id="setup-form" hidden>
        <div class="setup-grid">
          <div><label for="setup-name-a">나의 이름</label><input id="setup-name-a" type="text" maxlength="12" value="별이" autocomplete="off" required></div>
          <div><label for="setup-name-b">상대의 이름</label><input id="setup-name-b" type="text" maxlength="12" value="달이" autocomplete="off" required></div>
          <div><label for="setup-pass-a">나의 비밀번호</label><input id="setup-pass-a" type="password" minlength="8" maxlength="128" autocomplete="new-password" placeholder="8자 이상" required></div>
          <div><label for="setup-pass-b">상대의 비밀번호</label><input id="setup-pass-b" type="password" minlength="8" maxlength="128" autocomplete="new-password" placeholder="서로 다르게" required></div>
        </div>
        <p class="setup-note">각자 다른 비밀번호로 정해 주세요.<br>계정과 일기는 이 PC에만 저장돼요.</p>
        <button id="setup-submit" class="primary" type="submit">이 PC에 다이어리 만들기</button>
      </form>
      <form id="login-form">
        <fieldset><legend>내 이름을 선택해 주세요</legend><div id="identities" class="identity-grid"></div></fieldset>
        <label class="password-label" for="password">내 비밀번호</label>
        <input id="password" type="password" autocomplete="current-password" maxlength="128" required placeholder="우리끼리 정한 비밀번호">
        <button id="login-submit" class="primary" type="submit">우리 다이어리 열기</button>
      </form>
      <p id="login-hint" class="privacy-hint">QR에는 접속 주소만 담겨요.<br>서로의 비밀번호는 따로 사용해 주세요.</p>
    </div>
  </section>

  <main id="home-view" class="home-view" hidden>
    <div class="day-row">
      <button id="prev-day" class="date-arrow" type="button" aria-label="하루 전">‹</button>
      <input id="day" type="date" aria-label="일기 날짜" min="2000-01-01">
      <button id="next-day" class="date-arrow" type="button" aria-label="하루 뒤">›</button>
      <span id="day-label" class="day-label">오늘의 기록</span>
      <button id="today" class="text-button" type="button">오늘</button>
    </div>

    <section class="card compose-card" aria-labelledby="own-title">
      <div class="section-top"><h2 id="own-title">나의 마음</h2><button id="reset-draft" class="text-button" type="button" title="저장된 일기로 다시 편집">저장본</button></div>
      <div id="moods" class="mood-grid" role="group" aria-label="오늘의 기분 선택"></div>
      <label class="sr-only" for="diary">일기 내용</label>
      <textarea id="diary" placeholder="먼저 기분을 골라 주세요" spellcheck="false" disabled></textarea>
      <div class="write-bottom"><span id="count" class="counter">0 / 240</span><button id="save" class="primary send" type="button">마음 보내기</button></div>
      <p id="own-receipt" class="own-receipt">보낸 뒤 상대의 확인 여부가 여기에 보여요.</p>
    </section>

    <section class="card partner-card" aria-labelledby="partner-title">
      <div class="section-top"><h2 id="partner-title">상대의 마음</h2><span id="partner-mood" class="mood-badge">기다리는 중</span></div>
      <p id="partner-copy" class="partner-copy"></p>
      <div class="partner-bottom">
        <label id="read-label" class="read-label"><input id="read-check" type="checkbox"><span id="read-text">읽었어요</span></label>
        <button id="detail-open" class="text-button" type="button">전체 읽기 ↗</button>
      </div>
    </section>
  </main>

  <footer id="sync-label" class="sync-label">둘만의 마음을 천천히 쌓아가요.</footer>
  <div id="toast" class="toast" role="status" aria-live="polite" hidden></div>

  <dialog id="qr-dialog" aria-labelledby="qr-title">
    <div class="dialog-heading"><h2 id="qr-title">우리의 다이어리 QR</h2><button id="qr-close" class="tool" type="button" aria-label="QR 닫기">✕</button></div>
    <p class="dialog-intro">PC 앱을 켜 두고, 같은 Wi-Fi에서 접속해요.<br>기본 HTTP는 암호화되지 않으니 신뢰하는 Wi-Fi에서만 써 주세요.</p>
    <img id="qr-image" class="qr-image" alt="커플 다이어리 접속 QR 코드">
    <p id="qr-help" class="dialog-intro"></p>
    <form id="share-form" class="share-form">
      <label for="share-address">PC의 접속 주소</label>
      <div class="share-row"><input id="share-address" type="url" inputmode="url" autocomplete="off" placeholder="http://192.168.0.23:8501" maxlength="500" required><button id="make-qr" class="primary" type="submit">QR 만들기</button></div>
    </form>
    <p id="url-access-hint" class="privacy-hint">다이어리를 만들거나 입장한 뒤 주소를 설정할 수 있어요.</p>
    <p id="qr-url" class="qr-url"></p>
    <button id="copy-url" class="primary" type="button">접속 링크 복사</button>
  </dialog>

  <dialog id="detail-dialog" aria-labelledby="detail-title">
    <div class="dialog-heading"><h2 id="detail-title">너의 오늘</h2><button id="detail-close" class="tool" type="button" aria-label="일기 팝업 닫기">✕</button></div>
    <p id="detail-meta" class="dialog-intro"></p>
    <p id="detail-body" class="detail-body"></p>
    <button id="detail-read" class="primary" type="button">✓ 읽었어요 표시하기</button>
    <p class="privacy-hint">체크해야 상대에게 확인 표시가 전달돼요.</p>
  </dialog>
</div>
'''

CSS = r'''
:host { display:block; color:#342C31; color-scheme:light; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; }
* { box-sizing:border-box; }
[hidden] { display:none !important; }
h1,h2,p { margin:0; }
button,input,textarea { font:inherit; }
button { cursor:pointer; touch-action:manipulation; -webkit-tap-highlight-color:transparent; border:0; }
button:disabled { cursor:default; opacity:.48; }
button:focus-visible,input:focus-visible,textarea:focus-visible { outline:3px solid #A73553; outline-offset:3px; }
.shell { position:relative; display:flex; flex-direction:column; gap:9px; width:100%; max-width:430px; height:min(744px,calc(100svh - 18px)); min-height:490px; margin:0 auto; }
.app-header { display:flex; align-items:center; justify-content:space-between; min-height:43px; flex-shrink:0; }
.brand { display:flex; gap:9px; align-items:center; }
.brand-heart { background:#F7E3E7; color:#A73553; border-radius:14px; width:39px; height:39px; display:grid; place-items:center; font-size:23px; }
h1 { font-size:20px; letter-spacing:-1px; font-weight:800; line-height:1.1; }
.brand p { font-size:10px; color:#75636C; margin-top:3px; letter-spacing:.5px; }
.header-tools { display:flex; gap:4px; align-items:center; }
.tool { display:grid; place-items:center; min-width:34px; min-height:36px; border-radius:10px; background:#FFFDF9; border:1px solid #E9DFD7; color:#6B4E5B; font-size:13px; font-weight:700; }
#refresh { font-size:23px; font-weight:400; }
.exit { font-size:10px; padding:0 8px; }
.home-view { flex:1; min-height:0; display:flex; flex-direction:column; gap:9px; }
.day-row { display:flex; align-items:center; gap:4px; min-height:34px; flex-shrink:0; }
.date-arrow { background:transparent; color:#806570; min-width:30px; height:32px; font-size:24px; }
#day { border:0; border-radius:9px; background:#FFFDF9; color:#493B43; padding:5px 6px; min-width:0; width:139px; height:33px; font-size:14px; font-weight:650; }
.day-label { color:#75636C; font-size:10px; margin-left:auto; white-space:nowrap; }
.text-button { background:transparent; color:#923B55; min-height:30px; padding:0 5px; font-size:11px; font-weight:700; white-space:nowrap; }
.card { background:#FFFDF9; border:1px solid #EBE0D9; border-radius:19px; padding:13px; box-shadow:0 3px 13px #603E4310; }
.compose-card { display:flex; flex-direction:column; gap:7px; flex-shrink:0; }
.section-top { display:flex; align-items:center; justify-content:space-between; min-height:23px; gap:8px; }
h2 { font-size:14px; font-weight:750; letter-spacing:-.4px; }
.section-top h2 { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.section-top .text-button { min-height:23px; }
.mood-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:5px; }
.mood { display:flex; align-items:center; justify-content:center; flex-direction:column; gap:1px; height:47px; background:#F7F1EC; border:1px solid transparent; border-radius:12px; color:#69535D; font-size:10px; }
.mood .emoji { font-size:20px; line-height:1.15; }
.mood[aria-pressed="true"] { background:#FCE9EE; border-color:#B84463; color:#8D2542; font-weight:750; }
#diary { width:100%; height:clamp(65px,12svh,96px); min-height:65px; resize:none; border:1px solid #E7DBD3; background:#FFF; border-radius:12px; padding:10px 11px; color:#342C31; font-size:16px; line-height:1.5; }
#diary::placeholder { color:#89747D; font-size:13px; }
#diary:disabled { background:#F6F1ED; color:#83717A; }
.write-bottom { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.counter { font-size:10px; color:#75636C; font-variant-numeric:tabular-nums; }
.counter.over { color:#A32643; font-weight:750; }
.primary { display:block; background:#A73553; color:#FFF; border-radius:12px; min-height:39px; padding:9px 13px; font-size:13px; font-weight:750; text-align:center; }
.primary:not(:disabled):hover { background:#8D2843; }
.send { min-width:146px; min-height:35px; padding:8px 14px; }
.own-receipt { font-size:10px; color:#75636C; min-height:14px; line-height:1.4; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.own-receipt.seen { color:#2C6852; }
.partner-card { flex:1; min-height:134px; display:flex; flex-direction:column; gap:8px; background:linear-gradient(135deg,#FFFDF9,#FFF5F3); }
.mood-badge { flex-shrink:0; color:#88516A; background:#F6E6EC; border-radius:20px; padding:4px 8px; font-size:10px; }
.partner-copy { min-height:35px; flex:1; font-size:14px; line-height:1.65; white-space:pre-wrap; overflow-wrap:anywhere; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:4; overflow:hidden; }
.partner-copy.empty { color:#88727D; font-size:12px; line-height:1.8; }
.partner-bottom { display:flex; justify-content:space-between; align-items:center; gap:5px; min-height:31px; }
.read-label { display:flex; align-items:center; gap:7px; color:#775260; font-size:12px; cursor:pointer; min-height:31px; }
.read-label.done { color:#2C6852; }
#read-check { width:18px; height:18px; margin:0; accent-color:#A73553; }
.sync-label { font-size:9px; line-height:1.5; color:#75636C; text-align:center; flex-shrink:0; }
.login-view { flex:1; min-height:0; display:flex; align-items:center; justify-content:center; }
.login-card { width:100%; background:#FFFDF9; border:1px solid #EBE0D9; border-radius:25px; padding:26px 23px 21px; text-align:center; box-shadow:0 9px 35px #6841490B; }
.envelope { display:block; font-size:38px; margin-bottom:9px; }
.login-card h2 { font-size:22px; }
.intro { color:#7A6470; font-size:13px; line-height:1.8; margin-top:9px; margin-bottom:21px; }
fieldset { border:0; padding:0; margin:0 0 14px; text-align:left; }
legend,.password-label { font-size:11px; font-weight:650; color:#705563; display:block; margin-bottom:7px; text-align:left; }
.identity-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.identity { background:#F5EFEA; color:#715462; border:1px solid #E5D7D0; border-radius:12px; padding:11px; font-size:14px; }
.identity[aria-pressed="true"] { background:#FCE9EE; border-color:#B84463; color:#8D2542; font-weight:750; }
#password { background:#FFF; border:1px solid #E5D7D0; color:#342C31; border-radius:12px; padding:11px; font-size:16px; width:100%; min-width:0; margin-bottom:12px; }
#password::placeholder { font-size:12px; color:#89747D; }
#login-submit,#setup-submit { width:100%; }
.setup-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:12px 9px; text-align:left; }
.setup-grid label,.share-form label { display:block; color:#705563; font-size:11px; font-weight:650; margin-bottom:6px; text-align:left; }
.setup-grid input,#share-address { width:100%; min-width:0; border:1px solid #E5D7D0; border-radius:10px; padding:10px 8px; background:#FFF; color:#342C31; font-size:16px; }
.setup-grid input::placeholder,#share-address::placeholder { font-size:11px; color:#89747D; }
.setup-note { font-size:10px; line-height:1.7; color:#7D6772; margin:12px 0; }
.share-form { margin:13px 0 6px; }
.share-row { display:flex; gap:6px; }
.share-row .primary { width:auto; flex-shrink:0; min-height:40px; padding:8px 10px; font-size:11px; }
.privacy-hint { color:#7D6772; font-size:10px; line-height:1.7; margin-top:12px; text-align:center; }
.toast { position:absolute; bottom:27px; left:0; right:0; z-index:30; border-radius:13px; background:#E9F3ED; border:1px solid #A5C6B2; color:#28563E; padding:11px 13px; font-size:12px; line-height:1.6; box-shadow:0 6px 24px #342C311A; }
.toast.error { background:#FFF0EF; color:#8A2F3D; border-color:#DEA8AC; }
dialog { width:min(91vw,410px); max-height:calc(100svh - 38px); padding:20px; border:1px solid #E5D7D0; border-radius:23px; background:#FFFDF9; color:#342C31; box-shadow:0 22px 75px #33203335; }
dialog::backdrop { background:#35243266; backdrop-filter:blur(3px); }
.dialog-heading { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }
.dialog-heading h2 { font-size:17px; }
.dialog-intro { font-size:12px; color:#7A6470; line-height:1.7; }
.qr-image { display:block; width:min(184px,52vw); height:auto; margin:10px auto; border-radius:10px; background:#FFF; }
.qr-url { font-size:10px; line-height:1.5; overflow-wrap:anywhere; color:#6D5361; text-align:center; margin:10px 0 14px; user-select:all; }
dialog .primary { width:100%; }
.detail-body { font-size:16px; line-height:1.85; white-space:pre-wrap; overflow-wrap:anywhere; max-height:50svh; overflow:auto; margin:15px 0 20px; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@media (max-height:660px) {
  .shell { gap:6px; min-height:465px; }
  .app-header { min-height:36px; }
  .brand-heart { width:34px; height:34px; font-size:20px; }
  h1 { font-size:18px; }
  .home-view { gap:6px; }
  .card { padding:10px 11px; border-radius:16px; }
  .compose-card { gap:5px; }
  .mood { height:39px; }
  .mood .emoji { font-size:18px; }
  #diary { height:65px; padding:7px 9px; }
  .partner-card { min-height:116px; gap:5px; }
  .partner-copy { -webkit-line-clamp:3; font-size:13px; }
  .login-card { padding:18px 22px; }
  .envelope { font-size:29px; margin-bottom:4px; }
  .intro { margin-top:6px; margin-bottom:14px; }
}
@media (max-width:350px) {
  .day-label { display:none; }
  #today { margin-left:auto; }
  .tool { min-width:30px; }
  .send { min-width:130px; }
  .card { padding-left:10px; padding-right:10px; }
}
@media (prefers-reduced-motion:reduce) { * { scroll-behavior:auto; } }
'''

JS = r'''
export default function(component) {
  const { parentElement, data, setTriggerValue } = component;
  const $ = (q) => parentElement.querySelector(q);
  const registry = globalThis.__twoHeartsDiaryOfflineV2 || (globalThis.__twoHeartsDiaryOfflineV2 = new Map());
  if (!registry.has(data.client_id)) registry.set(data.client_id, {identity:null,drafts:{},selectedRole:"A",pending:null,modal:null,shareDraft:"",shareDirty:false,setupNames:{a:"별이",b:"달이"}});
  const state = registry.get(data.client_id);
  if (state.identity !== data.me) {
    state.identity = data.me; state.drafts = {}; state.pending = null; state.modal = null;
    $("#password").value = ""; $("#diary").value = ""; $("#partner-copy").textContent = "";
    $("#detail-body").textContent = ""; $("#detail-meta").textContent = "";
    $("#setup-pass-a").value = ""; $("#setup-pass-b").value = "";
  }
  let toastTimer;
  const chars = (value) => [...value].length;
  const timeLabel = (iso, withDate=false) => {
    if (!iso) return "";
    try {
      return new Intl.DateTimeFormat("ko-KR", {timeZone:"Asia/Seoul",hour:"2-digit",minute:"2-digit",hour12:false,
        ...(withDate ? {month:"numeric",day:"numeric"} : {})}).format(new Date(iso));
    } catch (_) { return ""; }
  };
  const moodLabel = (id) => {
    const m = data.moods.find(item => item.id === id);
    return m ? `${m.emoji} ${m.label}` : "기다리는 중";
  };
  const showToast = (text, error=false) => {
    clearTimeout(toastTimer);
    const box = $("#toast"); box.textContent = text; box.classList.toggle("error", error); box.hidden = !text;
    if (text) toastTimer = setTimeout(() => { box.hidden = true; }, 8000);
  };
  const fromSaved = () => ({text:data.own?.text || "",mood:data.own?.mood || "",version:data.own?.version || "",
      baseText:data.own?.text || "",baseMood:data.own?.mood || "",dirty:false});
  const ack = data.ack;
  if (state.pending && ack?.id === state.pending.id) {
    const done = state.pending;
    state.pending = null;
    if (ack.ok && done.kind === "save" && ack.day === data.day) state.drafts[data.day] = fromSaved();
    if (ack.ok && done.kind === "read") state.modal = null;
    if (ack.ok && done.kind === "share_url") { state.shareDirty = false; state.shareDraft = data.app_url || ""; }
  }
  // Recover a successful save even if the write response was interrupted.
  if (state.pending?.kind === "save" && data.own?.last_request === state.pending.id) {
    state.drafts[data.day] = fromSaved(); state.pending = null;
  }
  if (data.me && !state.drafts[data.day]) state.drafts[data.day] = fromSaved();
  let draft = data.me ? state.drafts[data.day] : null;
  if (draft && !draft.dirty && draft.version !== (data.own?.version || "")) {
    state.drafts[data.day] = fromSaved(); draft = state.drafts[data.day];
  }
  const isRead = !!data.partner && data.partner.read_by === data.me && !!data.partner.read_at;
  const dirtyUpdate = () => {
    if (draft) draft.dirty = draft.text !== draft.baseText || draft.mood !== draft.baseMood;
  };
  const updateControls = () => {
    const busy = !!state.pending;
    $("#setup-submit").disabled = busy;
    $("#setup-submit").textContent = state.pending?.kind === "setup" ? "만드는 중…" : "이 PC에 다이어리 만들기";
    $("#make-qr").disabled = busy || !data.me;
    $("#share-address").disabled = busy || !data.me;
    $("#login-submit").disabled = busy;
    $("#login-submit").textContent = state.pending?.kind === "login" ? "입장하는 중…" : "우리 다이어리 열기";
    $("#refresh").disabled = busy;
    $("#logout").disabled = busy;
    if (!data.me || !draft) return;
    $("#diary").disabled = !draft.mood || data.offline || state.pending?.kind === "save";
    $("#diary").placeholder = draft.mood ? "오늘 너에게 전하고 싶은 말…" : "먼저 기분을 골라 주세요";
    $("#count").textContent = `${chars(draft.text)} / ${data.max_chars}`;
    $("#count").classList.toggle("over", chars(draft.text) > data.max_chars);
    $("#save").disabled = busy || data.offline || !draft.mood || !draft.text.trim() || !draft.dirty || chars(draft.text) > data.max_chars;
    $("#save").textContent = state.pending?.kind === "save" ? "보내는 중…" : (data.own ? "수정해서 보내기" : "마음 보내기");
    $("#reset-draft").disabled = busy || (!data.own && !draft.dirty);
    $("#read-check").disabled = busy || data.offline || !data.partner || isRead;
    $("#detail-read").disabled = busy || data.offline || !data.partner || isRead;
    $("#detail-read").textContent = isRead ? "✓ 확인 완료" : (state.pending?.kind === "read" ? "표시하는 중…" : "✓ 읽었어요 표시하기");
    $("#prev-day").disabled = busy || data.day <= "2000-01-01";
    $("#next-day").disabled = busy || data.day >= data.today;
    $("#day").disabled = busy;
    $("#today").disabled = busy;
    parentElement.querySelectorAll(".mood").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.mood === draft.mood));
      button.disabled = data.offline || state.pending?.kind === "save";
    });
  };
  const send = (kind, payload={}) => {
    if (state.pending) return;
    const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    state.pending = {id,kind,at:Date.now()};
    updateControls();
    setTriggerValue("event", {id,kind,payload});
  };
  const requestRead = () => {
    if (!data.partner || isRead) return;
    send("read", {day:data.day,version:data.partner.version});
  };
  const openModal = (name) => {
    state.modal = name;
    const dialog = name === "qr" ? $("#qr-dialog") : $("#detail-dialog");
    if (!dialog.open) dialog.showModal();
  };
  const closeModal = (name) => {
    const dialog = name === "qr" ? $("#qr-dialog") : $("#detail-dialog");
    state.modal = null; if (dialog.open) dialog.close();
  };

  $("#login-view").hidden = !!data.me;
  $("#home-view").hidden = !data.me;
  $("#setup-form").hidden = !data.needs_setup;
  $("#login-form").hidden = !!data.needs_setup;
  $("#welcome-title").textContent = data.needs_setup ? "우리 다이어리 시작하기" : "둘만의 작은 기록";
  $("#welcome-intro").textContent = data.needs_setup ? "인터넷 연결이나 외부 DB 설정 없이, 이 PC에 둘만의 공간을 만들어요." : "기분 하나, 짧은 글 하나. 오늘의 나를 너에게 남겨요.";
  $("#login-hint").textContent = data.needs_setup ? "처음 한 번만 만들면 돼요. 공개 공유 전에 PC에서 먼저 설정해 주세요." : "같은 Wi-Fi에서 QR로 입장해요. PC 앱은 계속 켜 두세요.";
  $("#setup-name-a").value = state.setupNames.a;
  $("#setup-name-b").value = state.setupNames.b;
  $("#setup-form").onsubmit = event => {
    event.preventDefault();
    const nameA = $("#setup-name-a").value.trim(), nameB = $("#setup-name-b").value.trim();
    const passA = $("#setup-pass-a").value, passB = $("#setup-pass-b").value;
    state.setupNames = {a:nameA,b:nameB};
    if (!nameA || !nameB || nameA === nameB) { showToast("두 사람의 이름을 서로 다르게 정해 주세요.",true); return; }
    if (chars(passA) < 8 || chars(passB) < 8 || passA === passB) { showToast("비밀번호는 서로 다른 8자 이상으로 정해 주세요.",true); return; }
    send("setup", {name_a:nameA,name_b:nameB,password_a:passA,password_b:passB});
    $("#setup-pass-a").value = ""; $("#setup-pass-b").value = "";
  };
  $("#refresh").hidden = !data.me; $("#logout").hidden = !data.me;
  const identities = $("#identities");
  identities.replaceChildren();
  ["A","B"].forEach(role => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "identity"; button.textContent = data.names[role];
    button.setAttribute("aria-pressed", String(state.selectedRole === role));
    button.onclick = () => {
      state.selectedRole = role;
      identities.querySelectorAll("button").forEach((b,i) => b.setAttribute("aria-pressed", String(["A","B"][i] === role)));
      $("#password").focus();
    };
    identities.appendChild(button);
  });
  $("#login-form").onsubmit = event => {
    event.preventDefault();
    const password = $("#password").value;
    if (!password) return;
    send("login", {role:state.selectedRole,password});
    $("#password").value = "";
  };
  $("#logout").onclick = () => {
    if (Object.values(state.drafts).some(item => item.dirty) && !window.confirm("아직 보내지 않은 글이 있어요. 나가면 초안이 사라져요. 나갈까요?")) return;
    send("logout");
  };
  $("#refresh").onclick = () => send("refresh");

  const moveDay = (delta) => {
    const target = new Date(`${data.day}T12:00:00Z`);
    target.setUTCDate(target.getUTCDate() + delta);
    send("load", {day:target.toISOString().slice(0,10)});
  };
  $("#prev-day").onclick = () => moveDay(-1);
  $("#next-day").onclick = () => moveDay(1);
  $("#today").onclick = () => send("load", {day:data.today});
  $("#day").max = data.today; $("#day").value = data.day;
  $("#day").onchange = event => { if (event.target.value) send("load", {day:event.target.value}); };

  if (data.me && draft) {
    const other = data.me === "A" ? "B" : "A";
    $("#own-title").textContent = `${data.names[data.me]}의 마음`;
    $("#partner-title").textContent = `${data.names[other]}의 마음`;
    $("#day-label").textContent = data.day === data.today ? "오늘의 기록" : "그날의 기록";
    const moods = $("#moods"); moods.replaceChildren();
    data.moods.forEach(mood => {
      const button = document.createElement("button"); button.type = "button"; button.className = "mood"; button.dataset.mood = mood.id;
      button.setAttribute("aria-label", mood.label); button.setAttribute("aria-pressed", String(draft.mood === mood.id));
      const emoji = document.createElement("span"); emoji.className = "emoji"; emoji.textContent = mood.emoji;
      const label = document.createElement("span"); label.textContent = mood.label;
      button.append(emoji,label);
      button.onclick = () => { draft.mood = mood.id; dirtyUpdate(); updateControls(); };
      moods.appendChild(button);
    });
    const textarea = $("#diary");
    if (textarea.value !== draft.text) textarea.value = draft.text;
    const captureText = event => {
      if (!event?.isComposing && chars(textarea.value) > data.max_chars) textarea.value = [...textarea.value].slice(0,data.max_chars).join("");
      draft.text = textarea.value; dirtyUpdate(); updateControls();
    };
    textarea.oninput = captureText;
    textarea.oncompositionend = captureText;
    $("#save").onclick = () => send("save", {day:data.day,mood:draft.mood,text:draft.text,version:draft.version});
    $("#reset-draft").onclick = () => {
      if (draft.dirty && !window.confirm("아직 보내지 않은 내용 대신, 마지막으로 불러온 저장본으로 바꿀까요?")) return;
      state.drafts[data.day] = fromSaved(); draft = state.drafts[data.day];
      textarea.value = draft.text; updateControls();
    };
    const ownSeen = data.own?.read_by === other && !!data.own?.read_at;
    $("#own-receipt").textContent = data.own
      ? (ownSeen ? `✓ ${data.names[other]}님이 ${timeLabel(data.own.read_at,true)} 확인했어요` : `💌 ${data.names[other]}님이 아직 확인하기 전이에요`)
      : "보낸 뒤 상대의 확인 여부가 여기에 보여요.";
    $("#own-receipt").classList.toggle("seen", ownSeen);
    $("#partner-mood").textContent = moodLabel(data.partner?.mood);
    $("#partner-copy").textContent = data.partner?.text || "아직 일기가 도착하지 않았어요.\n오늘의 마음을 먼저 보내 볼까요?";
    $("#partner-copy").classList.toggle("empty", !data.partner);
    $("#read-check").checked = isRead;
    $("#read-text").textContent = isRead ? "확인 완료" : "읽었어요";
    $("#read-label").classList.toggle("done", isRead);
    $("#read-check").onchange = event => { if (event.target.checked) requestRead(); };
    $("#detail-open").disabled = !data.partner;
    $("#detail-open").onclick = () => openModal("detail");
    $("#detail-title").textContent = `${data.names[other]}의 하루`;
    $("#detail-meta").textContent = `${data.day} · ${moodLabel(data.partner?.mood)}`;
    $("#detail-body").textContent = data.partner?.text || "";
    $("#detail-read").onclick = requestRead;
    $("#sync-label").textContent = data.offline ? "로컬 저장을 확인해 주세요 · ↻로 다시 확인" : `이 PC에 저장 · ${data.sync_seconds}초마다 확인 · ${timeLabel(data.server_time)}`;
  } else {
    $("#sync-label").textContent = "오프라인 / 같은 Wi-Fi · 데이터는 이 PC에만 저장돼요.";
  }

  $("#qr-open").onclick = () => openModal("qr");
  const qrImage = $("#qr-image"); qrImage.hidden = !data.qr;
  if (data.qr) qrImage.src = data.qr; else qrImage.removeAttribute("src");
  $("#qr-url").textContent = data.app_url || "";
  $("#qr-help").textContent = !data.qr ? "PC의 실제 Wi-Fi IPv4 주소 뒤에 :8501을 붙여 입력해 주세요. localhost는 휴대폰에서 열리지 않아요."
    : "QR에는 주소만 들어가요. 주소나 PC의 Wi-Fi가 바뀌면 다시 만들어 주세요.";
  $("#share-address").value = state.shareDirty ? state.shareDraft : (data.app_url || "");
  $("#share-address").oninput = event => { state.shareDraft = event.target.value; state.shareDirty = true; };
  $("#share-form").onsubmit = event => {
    event.preventDefault();
    if (!data.me) { showToast("먼저 내 이름으로 입장해 주세요.",true); return; }
    send("share_url", {url:$("#share-address").value.trim()});
  };
  $("#url-access-hint").hidden = !!data.me;
  $("#copy-url").textContent = "접속 링크 복사";
  $("#copy-url").disabled = !data.app_url;
  $("#copy-url").onclick = async () => {
    try { await navigator.clipboard.writeText(data.app_url); $("#copy-url").textContent = "✓ 링크를 복사했어요"; }
    catch (_) { $("#qr-help").textContent = "아래 주소를 길게 눌러 복사해 주세요."; }
  };
  $("#qr-close").onclick = () => closeModal("qr");
  $("#detail-close").onclick = () => closeModal("detail");
  ["qr","detail"].forEach(name => {
    const dialog = name === "qr" ? $("#qr-dialog") : $("#detail-dialog");
    dialog.oncancel = () => { state.modal = null; };
    if (state.modal !== name && dialog.open) dialog.close();
    if (state.modal === name && !dialog.open) dialog.showModal();
  });
  updateControls();
  if (data.notice) showToast(data.notice.text,data.notice.kind === "error"); else $("#toast").hidden = true;

  // Only poll a visible authenticated tab. A rerun does not overwrite a dirty draft.
  const poller = setInterval(() => {
    if (data.me && document.visibilityState === "visible" && !state.pending) send("refresh");
  }, data.sync_seconds * 1000);
  const pendingWatch = setInterval(() => {
    if (state.pending && Date.now() - state.pending.at > 18000) {
      state.pending = null; updateControls();
      showToast("PC 앱과 같은 Wi-Fi 연결을 확인해 주세요. ↻로 저장 여부를 먼저 확인해 주세요.", true);
    }
  }, 2000);
  return () => { clearInterval(poller); clearInterval(pendingWatch); clearTimeout(toastTimer); };
}
'''

GLOBAL_STYLE = """
<style>
.stApp, [data-testid="stMain"] { background:#F9F3EC; color:#342C31; color-scheme:light; }
[data-testid="stMarkdownContainer"] { color:#342C31; }
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
.stAppDeployButton, #MainMenu, footer { display:none !important; }
[data-testid="stMainBlockContainer"] { padding:8px 12px !important; max-width:478px; }
[data-testid="stVerticalBlock"] { gap:0 !important; }
/* No global overflow:hidden: small screens and accessibility zoom stay usable. */
</style>
"""


@st.cache_resource(show_spinner=False)
def ui_component():
    return st.components.v2.component("two_hearts_diary_offline_v2", html=HTML, css=CSS, js=JS,
                                      isolate_styles=True)


@st.fragment
def diary_screen() -> None:
    store = get_store(str(DB_PATH))
    settings = store.settings()
    ui_component()(
        data=frontend_data(store, settings),
        key=COMPONENT_KEY,
        default={"event": None},
        on_event_change=handle_event,
    )


def main() -> None:
    st.set_page_config(page_title="둘의 하루 · 오프라인 다이어리", page_icon="💌", layout="centered",
                       initial_sidebar_state="collapsed")
    st.html(GLOBAL_STYLE)
    try:
        diary_screen()
    except StorageError as exc:
        st.markdown("## 💌 둘의 하루")
        st.error(str(exc))
        st.caption("쓰기 가능한 폴더에서 실행해 주세요. 기존 데이터 파일이 있다면 지우지 말고 먼저 백업해 주세요.")


if __name__ == "__main__":
    main()
