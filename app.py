"""둘의 하루 — QR로 여는, 두 사람만의 작은 커플 다이어리.

배포할 파일은 이 app.py와 requirements.txt, 딱 두 개입니다.
Python 3.11 또는 3.12를 권장합니다.

[처음 배포하기]
1. 두 파일을 GitHub 저장소의 같은 위치에 올립니다.
2. https://console.upstash.com 에서 Redis 데이터베이스를 만듭니다.
   Connection / REST API에서 HTTPS URL과 쓰기 가능한 Token을 복사합니다.
   Read Only Token이 아닌 일반 Token이 필요합니다. SQL/테이블 생성은 없습니다.
3. Streamlit Community Cloud에서 GitHub 저장소, 실행 파일 app.py를 선택합니다.
4. Advanced settings -> Secrets에 아래 예시를 붙여넣고 실제 값으로 바꿉니다.
   비밀번호/토큰을 이 Python 파일이나 GitHub에 직접 적지 마세요.
5. 배포 후 APP_URL을 실제 https://....streamlit.app 주소로 바꿉니다.
   APP_URL을 비워 두면 현재 Streamlit 주소로 자동 생성을 시도합니다.
6. 앱 상단 QR 버튼을 눌러 상대에게 공유합니다. 각자 자신의 비밀번호로 입장합니다.

[Secrets 예시 — GitHub에 올리는 별도 파일이 아니라 Streamlit 설정에 입력]
UPSTASH_REDIS_REST_URL = "https://YOUR-DATABASE.upstash.io"
UPSTASH_REDIS_REST_TOKEN = "YOUR_WRITE_TOKEN"
COUPLE_ID = "our-unique-couple-2026"
MEMBER_A_NAME = "별이"
MEMBER_B_NAME = "달이"
MEMBER_A_PASSWORD = "CHANGE_ME_A_12chars"
MEMBER_B_PASSWORD = "CHANGE_ME_B_12chars"
APP_URL = ""
SYNC_SECONDS = 20

CHANGE_ME로 시작하는 예시 비밀번호는 사용할 수 없습니다.
서로 다른 10~128자 비밀번호로 바꾸세요. COUPLE_ID도 커플별로 다르게 정합니다.
같은 저장소를 계속 사용하려면 배포 후 COUPLE_ID를 바꾸지 마세요.

[동작]
- 한 사람당 날짜별 일기 1개, 기분 5종, 본문 최대 240자. 보낸 일기는 수정 가능.
- 상대가 직접 '읽었어요'를 체크해야 확인 시각이 표시됩니다.
- 본문/기분을 실제로 수정하면 기존 읽음이 초기화됩니다. 같은 내용 재전송은 유지.
- 글 버전 비교로 동시 수정과 오래된 글의 읽음 표시가 섞이는 것을 방지합니다.
- 앱 화면이 보이는 동안 20초마다 동기화합니다. ↻로 즉시 확인할 수도 있습니다.
- 일기는 외부 Redis에 만료시간 없이 저장합니다. 로그인 제한 키만 5분 후 만료.
- 모바일 메인 화면을 컴팩트하게 유지하고, 긴 글/QR은 같은 페이지의 팝업에 표시.
- 아주 작은 화면, 큰 글씨 설정, 가상 키보드에서는 접근성을 위해 스크롤을 허용합니다.
- 전송 전 초안은 열린 탭의 메모리에만 남습니다. 새로고침/로그아웃 시 사라집니다.

[개인정보]
비밀번호는 서버의 Streamlit Secrets에서만 확인하고 QR에는 주소만 넣습니다.
이는 종단간 암호화 앱이 아닙니다. 운영자/저장소 제공자는 데이터에 접근할 수 있습니다.
외부 저장소의 요금, 한도, 백업/보존 정책은 운영자가 확인해야 합니다.

[로컬 실행 — 로컬 개발용 Secrets를 설정한 경우]
python -m pip install -r requirements.txt
streamlit run app.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import qrcode
import streamlit as st

KST = timezone(timedelta(hours=9))
MAX_CHARS = 240
SESSION_SECONDS = 12 * 60 * 60
COMPONENT_KEY = "couple_ui"
MOODS = [
    {"id": "happy", "emoji": "😍", "label": "행복"},
    {"id": "calm", "emoji": "🙂", "label": "평온"},
    {"id": "tired", "emoji": "😴", "label": "피곤"},
    {"id": "sad", "emoji": "😢", "label": "우울"},
    {"id": "upset", "emoji": "😤", "label": "속상"},
]
MOOD_IDS = {m["id"] for m in MOODS}
SECRET_EXAMPLE = '''UPSTASH_REDIS_REST_URL = "https://YOUR-DATABASE.upstash.io"
UPSTASH_REDIS_REST_TOKEN = "YOUR_WRITE_TOKEN"
COUPLE_ID = "our-unique-couple-2026"
MEMBER_A_NAME = "별이"
MEMBER_B_NAME = "달이"
MEMBER_A_PASSWORD = "CHANGE_ME_A_12chars"
MEMBER_B_PASSWORD = "CHANGE_ME_B_12chars"
APP_URL = ""
SYNC_SECONDS = 20'''


class ConfigurationError(Exception):
    pass


class StorageError(Exception):
    pass


class VersionConflict(Exception):
    pass


class UserMessage(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    redis_url: str
    redis_token: str = field(repr=False)
    couple_id: str
    name_a: str
    name_b: str
    password_a: str = field(repr=False)
    password_b: str = field(repr=False)
    app_url: str = ""
    sync_seconds: int = 20

    @property
    def namespace(self) -> str:
        return hashlib.sha256(self.couple_id.encode("utf-8")).hexdigest()[:32]

    @property
    def auth_stamp(self) -> str:
        value = json.dumps([self.namespace, self.password_a, self.password_b])
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

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


def load_settings() -> Settings:
    try:
        values = dict(st.secrets)
    except Exception:
        raise ConfigurationError("아직 연결 설정이 없어요. 아래 안내를 따라 한 번만 설정해 주세요.") from None
    required = ["UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "COUPLE_ID",
                "MEMBER_A_PASSWORD", "MEMBER_B_PASSWORD"]
    missing = [key for key in required if not str(values.get(key, "")).strip()]
    if missing:
        raise ConfigurationError("Secrets에 필요한 항목이 빠졌어요: " + ", ".join(missing))
    url = str(values["UPSTASH_REDIS_REST_URL"]).strip().rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".upstash.io")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path or ":" in parsed.netloc):
        raise ConfigurationError("Upstash의 HTTPS REST URL을 그대로 넣어 주세요. 명령 경로는 붙이지 않습니다.")
    token = str(values["UPSTASH_REDIS_REST_TOKEN"]).strip()
    if "\n" in token or "\r" in token or token == "YOUR_WRITE_TOKEN":
        raise ConfigurationError("예시 토큰 대신 Upstash의 실제 쓰기 가능한 Token을 넣어 주세요.")
    passwords = [str(values["MEMBER_A_PASSWORD"]), str(values["MEMBER_B_PASSWORD"])]
    if any(not 10 <= len(p) <= 128 or p.startswith("CHANGE_ME") for p in passwords):
        raise ConfigurationError("각 비밀번호를 예시가 아닌, 서로 다른 10~128자 값으로 바꿔 주세요.")
    if hmac.compare_digest(hashlib.sha256(passwords[0].encode()).digest(),
                           hashlib.sha256(passwords[1].encode()).digest()):
        raise ConfigurationError("서로의 계정을 구분하도록 두 비밀번호를 다르게 정해 주세요.")
    names = [str(values.get("MEMBER_A_NAME", "별이")).strip(),
             str(values.get("MEMBER_B_NAME", "달이")).strip()]
    if any(not 1 <= len(n) <= 12 or any(ord(c) < 32 for c in n) for n in names) or names[0] == names[1]:
        raise ConfigurationError("두 이름은 서로 다른 1~12자 이름으로 설정해 주세요.")
    couple_id = str(values["COUPLE_ID"]).strip()
    if not 8 <= len(couple_id) <= 128:
        raise ConfigurationError("COUPLE_ID는 커플별로 다른 8~128자 값으로 정해 주세요.")
    try:
        sync = int(values.get("SYNC_SECONDS", 20))
    except (ValueError, TypeError):
        raise ConfigurationError("SYNC_SECONDS는 15~120 사이의 정수로 입력해 주세요.") from None
    if not 15 <= sync <= 120:
        raise ConfigurationError("SYNC_SECONDS는 15~120 사이의 정수로 입력해 주세요.")
    return Settings(url, token, couple_id, names[0], names[1], passwords[0], passwords[1],
                    str(values.get("APP_URL", "")).strip(), sync)


# All keys touched by a script are declared in KEYS; the namespace hash tag keeps
# keys for this couple in one Redis slot. Diary keys intentionally have no TTL.
SAVE_LUA = r'''
local raw = redis.call('HGET', KEYS[1], ARGV[1])
local next = cjson.decode(ARGV[4])
if raw then
    local current = cjson.decode(raw)
    if current.last_request == ARGV[3] then return {'ok', raw} end
    if current.version ~= ARGV[2] then return {'conflict', raw} end
    if current.text == next.text and current.mood == next.mood then
        return {'unchanged', raw}
    end
    next.created_at = current.created_at
    raw = cjson.encode(next)
else
    if ARGV[2] ~= '' then return {'conflict', ''} end
    raw = ARGV[4]
end
redis.call('HSET', KEYS[1], ARGV[1], raw)
return {'ok', raw}
'''
READ_LUA = r'''
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return {'missing', ''} end
local current = cjson.decode(raw)
if current.version ~= ARGV[2] then return {'conflict', raw} end
if current.author == ARGV[3] then return {'forbidden', ''} end
if current.read_by ~= '' then return {'ok', raw} end
current.read_by = ARGV[3]
current.read_at = ARGV[4]
raw = cjson.encode(current)
redis.call('HSET', KEYS[1], ARGV[1], raw)
return {'ok', raw}
'''
LOGIN_LUA = r'''
local attempts = redis.call('INCR', KEYS[1])
if attempts == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) end
if attempts > tonumber(ARGV[1]) then return 0 end
return 1
'''


class RedisStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def key(self, day: str) -> str:
        return f"couple:{{{self.settings.namespace}}}:day:{day}"

    def command(self, *args: Any) -> Any:
        # Secrets stay server-side. Entries go in the POST body, not in the URL.
        request = Request(
            self.settings.redis_url,
            data=json.dumps(list(args), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.settings.redis_token}",
                     "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read(262_144).decode("utf-8"))
            if not isinstance(payload, dict) or "error" in payload or "result" not in payload:
                raise ValueError("invalid storage response")
            return payload["result"]
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            # Do not expose tokens, endpoint details, raw database errors, or text.
            raise StorageError("저장소에 연결하지 못했어요. 잠시 뒤 ↻로 다시 확인해 주세요.") from None

    @staticmethod
    def decode_document(raw: Any, role: str, day: str) -> dict[str, Any] | None:
        if raw is None or raw == "":
            return None
        try:
            doc = json.loads(raw)
            if (not isinstance(doc, dict) or doc.get("author") != role or doc.get("day") != day
                    or doc.get("mood") not in MOOD_IDS or not isinstance(doc.get("text"), str)
                    or not 1 <= len(doc["text"]) <= MAX_CHARS
                    or not isinstance(doc.get("version"), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", doc["version"])
                    or doc.get("read_by", "") not in ("", "A", "B")
                    or doc.get("read_by", "") == role):
                raise ValueError("invalid document")
            return doc
        except (ValueError, TypeError, KeyError):
            raise StorageError("저장된 기록 형식을 확인할 수 없어요. 운영자에게 문의해 주세요.") from None

    def get_day(self, day: str) -> dict[str, dict[str, Any] | None]:
        result = self.command("HMGET", self.key(day), "A", "B")
        if not isinstance(result, list) or len(result) != 2:
            raise StorageError("일기를 불러오지 못했어요. ↻로 다시 확인해 주세요.")
        return {role: self.decode_document(raw, role, day)
                for role, raw in zip(("A", "B"), result)}

    def permit_login(self, role: str) -> bool:
        if role not in ("A", "B"):
            raise UserMessage("이름을 먼저 선택해 주세요.")
        key = f"couple:{{{self.settings.namespace}}}:login:{role}"
        # Shared server-side limit: opening another tab does not bypass it.
        return self.command("EVAL", LOGIN_LUA, 1, key, 10, 300) == 1

    def save(self, day: str, role: str, mood: str, text: str,
             expected_version: str, request_id: str) -> dict[str, Any]:
        stamp = now_iso()
        document = {"author": role, "day": day, "mood": mood, "text": text,
                    "version": uuid.uuid4().hex, "created_at": stamp, "updated_at": stamp,
                    "read_by": "", "read_at": "", "last_request": request_id}
        result = self.command("EVAL", SAVE_LUA, 1, self.key(day), role, expected_version,
                              request_id, json.dumps(document, ensure_ascii=False))
        if not isinstance(result, list) or len(result) != 2:
            raise StorageError("저장 결과를 확인하지 못했어요. ↻로 저장 여부를 확인해 주세요.")
        if result[0] == "conflict":
            raise VersionConflict("다른 기기에서 글이 바뀌었어요. 글을 복사해 두고 '저장본'을 불러온 뒤 다시 수정해 주세요.")
        if result[0] not in ("ok", "unchanged"):
            raise StorageError("저장 결과를 확인하지 못했어요. ↻로 다시 확인해 주세요.")
        doc = self.decode_document(result[1], role, day)
        if doc is None:
            raise StorageError("저장된 글을 확인하지 못했어요.")
        return doc

    def mark_read(self, day: str, viewer: str, version: str) -> dict[str, Any]:
        author = "B" if viewer == "A" else "A"
        result = self.command("EVAL", READ_LUA, 1, self.key(day), author, version, viewer, now_iso())
        if not isinstance(result, list) or len(result) != 2:
            raise StorageError("확인 표시를 저장하지 못했어요. 다시 시도해 주세요.")
        if result[0] == "conflict":
            raise VersionConflict("상대가 방금 글을 수정했어요. 새 내용을 읽고 다시 체크해 주세요.")
        if result[0] != "ok":
            raise UserMessage("확인할 일기가 없어요. ↻로 다시 불러와 주세요.")
        doc = self.decode_document(result[1], author, day)
        if doc is None:
            raise StorageError("확인 결과를 읽지 못했어요.")
        return doc


def authenticated_member(settings: Settings) -> str | None:
    auth = st.session_state.get("_auth", {})
    if (auth.get("role") in ("A", "B") and auth.get("stamp") == settings.auth_stamp
            and 0 <= time.time() - auth.get("issued_at", 0) < SESSION_SECONDS):
        return auth["role"]
    st.session_state.pop("_auth", None)
    return None


def notice(text: str, kind: str = "success") -> None:
    st.session_state["_notice"] = {"text": text, "kind": kind, "at": time.time()}


def handle_event(settings: Settings) -> None:
    event = st.session_state.get(COMPONENT_KEY, {}).get("event")
    if not isinstance(event, dict):
        return
    eid, kind = event.get("id"), event.get("kind")
    if not isinstance(eid, str) or not 1 <= len(eid) <= 80 or not isinstance(kind, str):
        return
    if eid == st.session_state.get("_last_event"):
        return
    st.session_state["_last_event"] = eid
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    ack: dict[str, Any] = {"id": eid, "kind": kind, "ok": False}
    store = RedisStore(settings)
    try:
        if kind == "login":
            role, password = payload.get("role"), payload.get("password", "")
            if role not in ("A", "B") or not isinstance(password, str) or len(password) > 128:
                raise UserMessage("이름과 비밀번호를 확인해 주세요.")
            if not store.permit_login(role):
                raise UserMessage("로그인 시도가 많아요. 최대 5분 뒤 다시 시도해 주세요.")
            expected = settings.password_a if role == "A" else settings.password_b
            if not hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                                       hashlib.sha256(expected.encode()).digest()):
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
                raise UserMessage("다시 입장해 주세요. 로그인은 12시간 동안 유지돼요.")
            if kind == "load":
                selected = parse_day(payload.get("day"))
                st.session_state["_selected_day"] = selected
                st.session_state["_follow_today"] = selected == today_kst()
            elif kind == "refresh":
                pass
            elif kind == "save":
                selected = parse_day(payload.get("day"))
                mood, text = payload.get("mood"), payload.get("text")
                if mood not in MOOD_IDS:
                    raise UserMessage("오늘의 기분을 먼저 골라 주세요.")
                if not isinstance(text, str):
                    raise UserMessage("일기 내용을 입력해 주세요.")
                text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
                if not text or len(text) > MAX_CHARS or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
                    raise UserMessage(f"빈 글은 보낼 수 없어요. {MAX_CHARS}자 안으로 적어 주세요.")
                version = payload.get("version", "")
                if not isinstance(version, str) or len(version) > 64:
                    raise UserMessage("저장본을 다시 불러온 뒤 보내 주세요.")
                doc = store.save(selected, role, mood, text, version, eid)
                st.session_state["_selected_day"] = selected
                st.session_state["_follow_today"] = selected == today_kst()
                ack.update({"day": selected, "version": doc["version"]})
                notice("마음을 보냈어요. 상대가 체크하면 확인 시각이 보여요.")
            elif kind == "read":
                selected, version = parse_day(payload.get("day")), payload.get("version")
                if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{32}", version):
                    raise UserMessage("새 일기를 불러온 뒤 다시 체크해 주세요.")
                store.mark_read(selected, role, version)
                notice("읽었다는 마음을 전했어요. ✓")
            else:
                raise UserMessage("지원하지 않는 동작이에요.")
        ack["ok"] = True
    except (UserMessage, VersionConflict, StorageError) as exc:
        notice(str(exc), "error")
    finally:
        # Do not retain a raw password in the component trigger object.
        if kind == "login":
            payload.pop("password", None)
        st.session_state["_ack"] = ack


def public_url(settings: Settings) -> str:
    value = settings.app_url
    if not value:
        try:
            value = str(st.context.url)
        except Exception:
            return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname
                or parsed.username or parsed.password or "\n" in value or "\r" in value):
            return ""
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            return ""
        # Never encode authentication or other query parameters into a QR code.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    except ValueError:
        return ""


@st.cache_data(show_spinner=False, max_entries=16)
def qr_data_uri(url: str) -> str:
    qr = qrcode.QRCode(box_size=8, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def frontend_data(settings: Settings) -> dict[str, Any]:
    me = authenticated_member(settings)
    today = today_kst()
    selected = today if st.session_state.get("_follow_today", True) else st.session_state.get("_selected_day", today)
    docs: dict[str, Any] = {"A": None, "B": None}
    offline = False
    if me:
        try:
            docs = RedisStore(settings).get_day(selected)
            st.session_state["_last_snapshot"] = {"day": selected, "docs": docs}
        except StorageError as exc:
            offline = True
            previous = st.session_state.get("_last_snapshot", {})
            if previous.get("day") == selected:
                docs = previous["docs"]
            notice(str(exc), "error")
    app_url = public_url(settings)
    current_notice = st.session_state.get("_notice")
    if current_notice and time.time() - current_notice["at"] > 8:
        current_notice = None
    return {
        "client_id": st.session_state.setdefault("_client_id", uuid.uuid4().hex),
        "me": me or "", "names": settings.names, "today": today, "day": selected,
        "own": docs[me] if me else None,
        "partner": docs["B" if me == "A" else "A"] if me else None,
        "moods": MOODS, "max_chars": MAX_CHARS, "sync_seconds": settings.sync_seconds,
        "offline": offline, "server_time": now_iso(),
        "app_url": app_url, "qr": qr_data_uri(app_url) if app_url else "",
        "local_url": bool(app_url and urlsplit(app_url).hostname in ("localhost", "127.0.0.1", "::1")),
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
      <h2>둘만의 작은 기록</h2>
      <p class="intro">기분 하나, 짧은 글 하나.<br>오늘의 나를 너에게 남겨요.</p>
      <form id="login-form">
        <fieldset><legend>내 이름을 선택해 주세요</legend><div id="identities" class="identity-grid"></div></fieldset>
        <label class="password-label" for="password">내 비밀번호</label>
        <input id="password" type="password" autocomplete="current-password" maxlength="128" required placeholder="우리끼리 정한 비밀번호">
        <button id="login-submit" class="primary" type="submit">우리 다이어리 열기</button>
      </form>
      <p class="privacy-hint">QR에는 접속 주소만 담겨요.<br>서로의 비밀번호는 따로 사용해 주세요.</p>
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
    <p class="dialog-intro">카메라로 찍고, 각자 비밀번호로 입장해요.</p>
    <img id="qr-image" class="qr-image" alt="커플 다이어리 접속 QR 코드">
    <p id="qr-help" class="dialog-intro"></p>
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
#login-submit { width:100%; }
.privacy-hint { color:#7D6772; font-size:10px; line-height:1.7; margin-top:12px; text-align:center; }
.toast { position:absolute; bottom:27px; left:0; right:0; z-index:30; border-radius:13px; background:#E9F3ED; border:1px solid #A5C6B2; color:#28563E; padding:11px 13px; font-size:12px; line-height:1.6; box-shadow:0 6px 24px #342C311A; }
.toast.error { background:#FFF0EF; color:#8A2F3D; border-color:#DEA8AC; }
dialog { width:min(91vw,410px); max-height:calc(100svh - 38px); padding:20px; border:1px solid #E5D7D0; border-radius:23px; background:#FFFDF9; color:#342C31; box-shadow:0 22px 75px #33203335; }
dialog::backdrop { background:#35243266; backdrop-filter:blur(3px); }
.dialog-heading { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }
.dialog-heading h2 { font-size:17px; }
.dialog-intro { font-size:12px; color:#7A6470; line-height:1.7; }
.qr-image { display:block; width:min(220px,65vw); height:auto; margin:12px auto; border-radius:10px; background:#FFF; }
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
  const registry = globalThis.__twoHeartsDiaryV1 || (globalThis.__twoHeartsDiaryV1 = new Map());
  if (!registry.has(data.client_id)) registry.set(data.client_id, {identity:null,drafts:{},selectedRole:"A",pending:null,modal:null});
  const state = registry.get(data.client_id);
  if (state.identity !== data.me) {
    state.identity = data.me; state.drafts = {}; state.pending = null; state.modal = null;
    $("#password").value = ""; $("#diary").value = ""; $("#partner-copy").textContent = "";
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
    $("#sync-label").textContent = data.offline ? "연결이 끊겼어요 · ↻로 다시 확인해 주세요" : `${data.sync_seconds}초마다 확인 · ${timeLabel(data.server_time)} 동기화`;
  } else {
    $("#sync-label").textContent = "둘만의 마음을 천천히 쌓아가요.";
  }

  $("#qr-open").onclick = () => openModal("qr");
  const qrImage = $("#qr-image"); qrImage.hidden = !data.qr;
  if (data.qr) qrImage.src = data.qr; else qrImage.removeAttribute("src");
  $("#qr-url").textContent = data.app_url || "";
  $("#qr-help").textContent = !data.qr ? "Streamlit Secrets의 APP_URL에 실제 배포 주소를 넣어 주세요."
    : (data.local_url ? "로컬 주소는 다른 휴대폰에서 열리지 않아요. 배포 후 APP_URL을 설정해 주세요." : "주소를 알아도 비밀번호 없이는 일기를 볼 수 없어요.");
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
      showToast("응답이 늦어지고 있어요. ↻로 저장 여부를 먼저 확인해 주세요.", true);
    }
  }, 2000);
  return () => { clearInterval(poller); clearInterval(pendingWatch); clearTimeout(toastTimer); };
}
'''

GLOBAL_STYLE = """
<style>
.stApp, [data-testid="stMain"] { background:#F9F3EC; }
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
.stAppDeployButton, #MainMenu, footer { display:none !important; }
[data-testid="stMainBlockContainer"] { padding:8px 12px !important; max-width:478px; }
[data-testid="stVerticalBlock"] { gap:0 !important; }
/* No global overflow:hidden: small screens and accessibility zoom stay usable. */
</style>
"""


@st.cache_resource(show_spinner=False)
def ui_component():
    return st.components.v2.component("two_hearts_diary_v1", html=HTML, css=CSS, js=JS,
                                      isolate_styles=True)


@st.fragment
def diary_screen(settings: Settings) -> None:
    ui_component()(
        data=frontend_data(settings),
        key=COMPONENT_KEY,
        default={"event": None},
        on_event_change=lambda: handle_event(settings),
    )


def main() -> None:
    st.set_page_config(page_title="둘의 하루 · 커플 다이어리", page_icon="💌", layout="centered",
                       initial_sidebar_state="collapsed")
    st.html(GLOBAL_STYLE)
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        st.markdown("## 💌 둘의 하루")
        st.info(str(exc))
        st.markdown("**처음 한 번만 설정하면 돼요.**\n\n"
                    "1. Upstash에서 Redis 데이터베이스를 만들고 REST URL과 Token을 복사해요.\n"
                    "2. Streamlit 앱의 **Settings → Secrets**에 아래 내용을 넣어요.\n"
                    "3. 예시 URL·토큰·비밀번호를 실제 값으로 바꾸고 저장해요.")
        st.code(SECRET_EXAMPLE, language="toml")
        st.caption("토큰/비밀번호는 GitHub에 올리지 마세요. 배포 방법은 app.py 맨 위에도 적혀 있어요.")
        return
    diary_screen(settings)


if __name__ == "__main__":
    main()
