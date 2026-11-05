"""교대근무 생활 매니저 — Streamlit + 외부 PostgreSQL

배포 방법 (이 파일과 requirements.txt, 두 파일만 GitHub에 업로드)
1. Neon 또는 Supabase 등에서 PostgreSQL 데이터베이스를 만든다.
   서버리스 환경에서는 제공되는 pooled connection URL 사용을 권장한다.
   이 앱 전용 DB/사용자를 사용하고 테이블 생성 권한을 부여한다.
2. Streamlit Community Cloud에서 저장소를 선택하고 실행 파일을 app.py로 지정한다.
3. 앱 Settings > Secrets에 아래 TOML을 입력한다. 비밀 값은 GitHub에 올리지 않는다.

DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
FAMILY_ID = "our-family-choose-a-long-random-id"
DAD_PASSWORD = "아빠용-길고-서로다른-비밀번호"
FAMILY_PASSWORD = "가족용-길고-서로다른-비밀번호"

4. 배포 후 같은 앱 주소로 접속한다. 아빠는 아빠 비밀번호, 가족은 가족 비밀번호와
   서로 다른 이름을 사용한다. 한 가족 구성원은 모든 휴대폰에서 같은 이름을 쓴다.
   이 앱은 가족 공유 비밀번호 방식이며 개인별 본인 인증 서비스는 아니다.

특징
- 모든 변경은 DB 행 잠금 트랜잭션으로 처리하여 동시 변경으로 인한 덮어쓰기를 방지한다.
- 다른 기기의 변경은 약 10초마다 반영되며 수동 새로고침도 가능하다.
- 상태 내용이 실제 변경될 때만 버전 증가 및 가족 확인 초기화.
- 준비물은 아빠에게만 표시. 가족의 부탁은 확인/완료를 독립 기록(완료 시 확인도 적용).
- 최근 상태 기록 100건, 부탁 200건(오래된 완료 부탁부터 정리), 가족 확인 최대 50명.
- 가족/아빠 공유 비밀번호를 가진 사람은 각 역할로 접속할 수 있다.
  근무지·보안업무·민감정보는 메모하지 않는다. 별도 DB 백업을 권장한다.
- 로그인 정보는 현재 Streamlit 세션에만 유지된다. DB 비밀번호를 바꾸면 재시작한다.

로컬 실행: pip install -r requirements.txt && streamlit run app.py
로컬 비밀 설정: .streamlit/secrets.toml (Git에 절대 포함하지 말 것)
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import html
import logging
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb
import streamlit as st

st.set_page_config(page_title="우리 가족 · 교대근무 매니저", page_icon="🌙", layout="centered", initial_sidebar_state="collapsed")
KST = ZoneInfo("Asia/Seoul")
SHIFTS = ["주간", "야간", "휴무"]
STATES = ["연락 가능", "근무 중", "쉬는 중", "수면 중"]
CONDITIONS = ["좋음", "보통", "피곤함"]
ICONS = {"연락 가능": "🟢", "근무 중": "🔵", "쉬는 중": "☕", "수면 중": "🌙"}

st.markdown("""<style>
:root {color-scheme: light;}
.stApp {background:#F8F5EC;color:#172B46;}
.block-container {max-width:700px;padding:1.1rem 1rem 2rem;}
header[data-testid="stHeader"] {background:transparent;}
h1,h2,h3,p,label,[data-testid="stMarkdownContainer"] {color:#172B46;}
h1 {font-size:1.65rem!important;} h2 {font-size:1.2rem!important;}
[data-testid="stVerticalBlock"] {gap:.55rem;}
.stButton>button,.stFormSubmitButton>button {min-height:46px;font-size:1rem;border-radius:12px;}
button[kind="primary"],button[kind="primaryFormSubmit"] {background:#172B46;color:#FFFDF5;border-color:#172B46;}
[data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea {font-size:17px;background:#FFFDF7;color:#172B46;}
[data-testid="stExpander"] {background:#FFFDF7;border-radius:12px;border:1px solid #DCDDD7;}
[data-testid="stCheckbox"] label p,[data-testid="stRadio"] label p {font-size:17px;}
.status-card {background:#172B46;color:#FFFDF5;padding:18px;border-radius:18px;margin:4px 0 8px;}
.status-card .state {font-size:30px;font-weight:800;line-height:1.35;color:#FFFDF5;}
.status-card .meta {font-size:17px;color:#F2EBD9;margin-top:6px;}
.status-card .memo {font-size:18px;color:#FFFDF5;margin-top:10px;white-space:pre-wrap;overflow-wrap:anywhere;}
.small-note {font-size:14px;color:#566377;}
[data-testid="stForm"] {border-color:#DCDDD7;padding:12px;border-radius:14px;}
</style>""", unsafe_allow_html=True)


def now():
    return datetime.now(KST).isoformat(timespec="seconds")


def pretty(value):
    if not value:
        return "아직 없음"
    return datetime.fromisoformat(value).astimezone(KST).strftime("%m/%d %H:%M")


def initial_data():
    return {
        "status": {"shift": "휴무", "state": "쉬는 중", "condition": "보통", "contact": "", "memo": "", "version": 0, "updated_at": None},
        "acks": {}, "requests": [], "history": [],
        "kit_revision": 0,
        "kit": [{"id": uuid.uuid4().hex, "text": text, "done": False} for text in ["신분증", "휴대전화", "도시락 · 물", "개인 준비물"]],
    }


try:
    DB_URL = str(st.secrets["DATABASE_URL"])
    FAMILY_ID = str(st.secrets["FAMILY_ID"]).strip()
    DAD_PASSWORD = str(st.secrets["DAD_PASSWORD"])
    FAMILY_PASSWORD = str(st.secrets["FAMILY_PASSWORD"])
    if not FAMILY_ID or len(DAD_PASSWORD) < 8 or len(FAMILY_PASSWORD) < 8 or DAD_PASSWORD == FAMILY_PASSWORD:
        raise ValueError("가족 ID 및 비밀번호 설정 확인")
except Exception:
    st.title("🌙 교대근무 생활 매니저")
    st.error("먼저 앱 Secrets에 DATABASE_URL, FAMILY_ID, DAD_PASSWORD, FAMILY_PASSWORD를 설정해 주세요. 비밀번호는 서로 다르게 8자 이상 사용하세요.")
    st.info("자세한 배포 순서와 Secrets 예시는 app.py 맨 위 설명에 있습니다. 외부 DB 연결 없이 임시 저장으로 실행하지 않습니다.")
    st.stop()


def connect():
    # 외부 DB와의 통신 암호화 및 무한 대기 방지.
    return psycopg.connect(DB_URL, sslmode="require", connect_timeout=10, options="-c statement_timeout=10000 -c lock_timeout=5000")


@st.cache_resource(show_spinner=False)
def initialize_db(url_fingerprint):
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS shift_family_app_v1 (
            family_id TEXT PRIMARY KEY,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    return True


def ensure_family():
    with connect() as conn:
        conn.execute("INSERT INTO shift_family_app_v1 (family_id,payload) VALUES (%s,%s) ON CONFLICT (family_id) DO NOTHING", (FAMILY_ID, Jsonb(initial_data())))


def load_data():
    with connect() as conn:
        row = conn.execute("SELECT payload FROM shift_family_app_v1 WHERE family_id=%s", (FAMILY_ID,)).fetchone()
        if not row:
            raise RuntimeError("가족 데이터 없음")
        return row[0]


def mutate(action):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM shift_family_app_v1 WHERE family_id=%s FOR UPDATE", (FAMILY_ID,)).fetchone()
        if not row:
            raise RuntimeError("가족 데이터 없음")
        data = row[0]
        result = action(data)
        conn.execute("UPDATE shift_family_app_v1 SET payload=%s,updated_at=now() WHERE family_id=%s", (Jsonb(data), FAMILY_ID))
        return result


class Conflict(ValueError):
    pass


def run_change(action, message):
    try:
        mutate(action)
    except (ValueError, Conflict) as exc:
        st.warning(str(exc))
        return False
    except Exception:
        logging.warning("교대근무 앱 DB 변경 실패 (연결 문자열 등 비밀 값은 기록하지 않음)")
        st.error("저장하지 못했습니다. 인터넷 또는 DB 상태를 확인하고 다시 시도하세요.")
        return False
    st.toast(message)
    return True


def update_status(data, version, values):
    old = data["status"]
    if old["version"] != version:
        raise Conflict("다른 기기에서 상태가 변경됐습니다. 최신 내용을 확인한 뒤 다시 저장하세요.")
    if all(old[k] == v for k, v in values.items()):
        return
    if old["updated_at"]:
        data["history"].insert(0, {"status": copy.deepcopy(old), "acks": copy.deepcopy(data["acks"])})
        data["history"] = data["history"][:100]
    data["status"] = {**values, "version": old["version"] + 1, "updated_at": now()}
    data["acks"] = {}


def acknowledge(data, version, name):
    if data["status"]["version"] != version:
        raise Conflict("아빠의 상태가 바뀌었습니다. 새 내용을 읽고 다시 확인해 주세요.")
    if not data["status"]["updated_at"]:
        raise ValueError("아빠가 상태를 처음 저장한 뒤 확인할 수 있어요.")
    if name not in data["acks"] and len(data["acks"]) >= 50:
        raise ValueError("확인 가능한 가족 수를 초과했습니다.")
    if name not in data["acks"]:
        data["acks"][name] = {"at": now(), "version": version}


def add_request(data, name, text):
    if not text.strip():
        raise ValueError("부탁 내용을 입력해 주세요.")
    if len(data["requests"]) >= 200:
        completed = [r for r in data["requests"] if r["done_at"]]
        if not completed:
            raise ValueError("부탁이 200개입니다. 먼저 기존 부탁을 완료해 주세요.")
        oldest = min(completed, key=lambda r: r["created_at"])
        data["requests"].remove(oldest)
    data["requests"].insert(0, {"id": uuid.uuid4().hex, "author": name, "text": text.strip()[:160], "created_at": now(), "seen_at": None, "done_at": None, "revision": 0})


def update_request(data, rid, revision, seen, done):
    item = next((r for r in data["requests"] if r["id"] == rid), None)
    if not item or item["revision"] != revision:
        raise Conflict("이 부탁이 다른 기기에서 변경됐습니다. 최신 내용을 확인해 주세요.")
    if done:
        seen = True
    item["seen_at"] = (item["seen_at"] or now()) if seen else None
    item["done_at"] = (item["done_at"] or now()) if done else None
    item["revision"] += 1


def update_kit(data, revision, checks=None, texts=None, reset=False):
    if data["kit_revision"] != revision:
        raise Conflict("준비물이 다른 기기에서 변경됐습니다. 최신 목록을 확인해 주세요.")
    if texts is not None:
        lines = [s.strip() for s in texts.splitlines() if s.strip()]
        if not 1 <= len(lines) <= 20 or any(len(s) > 50 for s in lines):
            raise ValueError("준비물은 1~20개, 한 항목당 50자 이내로 입력해 주세요.")
        if len(set(lines)) != len(lines):
            raise ValueError("중복된 준비물 이름을 정리해 주세요.")
        old = {i["text"]: i for i in data["kit"]}
        data["kit"] = [copy.deepcopy(old[t]) if t in old else {"id": uuid.uuid4().hex, "text": t, "done": False} for t in lines]
    elif reset:
        for item in data["kit"]:
            item["done"] = False
    elif checks is not None:
        for item in data["kit"]:
            item["done"] = bool(checks.get(item["id"], item["done"]))
    data["kit_revision"] += 1


st.title("🌙 우리 가족 근무 매니저")

if "role" not in st.session_state:
    st.caption("각자의 휴대폰에서도 함께 보는 아빠의 하루")
    with st.form("login"):
        role = st.radio("누가 사용하나요?", ["가족", "아빠"], horizontal=True)
        name = st.text_input("가족 이름 (가족으로 접속할 때 필수)", max_chars=20, placeholder="예: 엄마, 지우 — 항상 같은 이름을 써 주세요")
        password = st.text_input("접속 비밀번호", type="password")
        submitted = st.form_submit_button("함께 보기", type="primary", use_container_width=True)
    if submitted:
        if time.time() < st.session_state.get("login_after", 0):
            st.warning("잠시 후 다시 시도하세요.")
        elif hmac.compare_digest(password.encode(), (DAD_PASSWORD if role == "아빠" else FAMILY_PASSWORD).encode()):
            if role == "가족" and not name.strip():
                st.warning("가족 이름을 입력해 주세요.")
            else:
                st.session_state.role = role
                st.session_state.member = "아빠" if role == "아빠" else name.strip()
                st.session_state.pop("login-Password", None)
                st.rerun()
        else:
            st.session_state.login_after = time.time() + 3
            st.error("비밀번호가 맞지 않습니다.")
    st.caption("가족끼리만 주소와 비밀번호를 공유하세요. 근무 관련 민감정보는 입력하지 마세요.")
    st.stop()

try:
    initialize_db(hashlib.sha256(DB_URL.encode()).hexdigest())
    ensure_family()
except Exception:
    st.error("외부 데이터베이스에 연결하지 못했습니다. Secrets의 연결 주소, DB 활성 상태, 테이블 생성 권한을 확인해 주세요.")
    if st.button("연결 다시 시도", use_container_width=True):
        st.rerun()
    st.stop()

DAD = st.session_state.role == "아빠"
MEMBER = st.session_state.member


@st.fragment(run_every="10s")
def dashboard():
    try:
        data = load_data()
    except Exception:
        st.error("최신 정보를 불러오지 못했습니다. 아래 내용은 갱신되지 않았을 수 있어요.")
        if st.button("다시 불러오기", use_container_width=True):
            st.rerun(scope="fragment")
        return
    status = data["status"]
    version = status["version"]
    esc = lambda s: html.escape(str(s))
    st.markdown(f'<div class="status-card"><div class="state">{ICONS[status["state"]]} {esc(status["state"])}</div><div class="meta">{esc(status["shift"])} 근무 · 컨디션 {esc(status["condition"])}</div><div class="meta">연락 시간 · {esc(status["contact"] or "아직 입력하지 않았어요")}</div><div class="memo">{esc(status["memo"] or "오늘의 메모가 없어요.")}</div></div>', unsafe_allow_html=True)
    if not status["updated_at"]:
        st.info("아직 저장된 상태가 없습니다. 위 내용은 기본값입니다.")
    st.caption(f'{MEMBER} 접속 · 상태 저장 {pretty(status["updated_at"])} · 약 10초마다 자동 동기화')
    if st.button("↻ 최신 상태", key="refresh", use_container_width=True):
        st.rerun(scope="fragment")

    if DAD:
        acks = data["acks"]
        st.write("**가족 확인** · " + (" / ".join(f'{n} {pretty(a["at"])}' for n, a in acks.items()) if acks else "아직 확인 전이에요"))
        with st.expander("✏️ 오늘의 상태 설정", expanded=not bool(status["updated_at"])):
            with st.form(f"status_{version}"):
                shift = st.radio("근무 유형", SHIFTS, index=SHIFTS.index(status["shift"]), horizontal=True)
                state = st.selectbox("현재 상태", STATES, index=STATES.index(status["state"]))
                condition = st.radio("컨디션", CONDITIONS, index=CONDITIONS.index(status["condition"]), horizontal=True)
                contact = st.text_input("연락 가능한 시간", value=status["contact"], max_chars=80, placeholder="예: 오늘 18:00~20:00 / 급하면 문자")
                memo = st.text_input("짧은 메모", value=status["memo"], max_chars=160, placeholder="예: 자고 일어나면 연락할게요")
                st.caption("내용이 바뀌면 가족의 확인 표시는 초기화됩니다.")
                if st.form_submit_button("상태 저장", type="primary", use_container_width=True):
                    values = {"shift": shift, "state": state, "condition": condition, "contact": contact.strip(), "memo": memo.strip()}
                    if run_change(lambda d: update_status(d, version, values), "상태를 저장했어요"):
                        st.rerun(scope="fragment")
    else:
        ack = data["acks"].get(MEMBER)
        if ack:
            st.success(f'✓ 확인했어요 · {pretty(ack["at"])}')
        else:
            with st.form(f"ack_{version}"):
                checked = st.checkbox("아빠의 상태와 메모를 확인했어요", key=f"ack_check_{version}")
                if st.form_submit_button("확인 전달", type="primary", use_container_width=True, disabled=not bool(status["updated_at"])):
                    if not checked:
                        st.warning("확인했어요에 체크해 주세요.")
                    elif run_change(lambda d: acknowledge(d, version, MEMBER), "아빠에게 확인을 전달했어요"):
                        st.rerun(scope="fragment")

    active = [r for r in data["requests"] if not r["done_at"]]
    st.markdown(f"### 💬 가족의 부탁 · {len(active)}개")
    if not DAD:
        with st.form("new_request", clear_on_submit=True):
            text = st.text_input("아빠에게 부탁 남기기", max_chars=160, placeholder="예: 퇴근길에 우유 부탁해요")
            if st.form_submit_button("부탁 보내기", use_container_width=True):
                if run_change(lambda d: add_request(d, MEMBER, text), "부탁을 남겼어요"):
                    st.rerun(scope="fragment")

    def render_request(item):
        st.write(f'**{item["author"]}** · {item["text"]}')
        st.caption(f'등록 {pretty(item["created_at"])} · 확인 {pretty(item["seen_at"])} · 완료 {pretty(item["done_at"])}')
        if DAD:
            with st.form(f'request_{item["id"]}_{item["revision"]}'):
                c1, c2 = st.columns(2)
                seen = c1.checkbox("확인", value=bool(item["seen_at"]), key=f'seen_{item["id"]}_{item["revision"]}')
                done = c2.checkbox("완료", value=bool(item["done_at"]), key=f'done_{item["id"]}_{item["revision"]}')
                if st.form_submit_button("처리 상태 저장", use_container_width=True):
                    if run_change(lambda d: update_request(d, item["id"], item["revision"], seen, done), "부탁 처리 상태를 저장했어요"):
                        st.rerun(scope="fragment")

    if not active:
        st.caption("아직 처리할 부탁이 없어요.")
    for item in active[:2]:
        render_request(item)
    if len(active) > 2:
        with st.expander(f"나머지 부탁 {len(active) - 2}개 보기"):
            for item in active[2:]:
                render_request(item)

    if DAD:
        revision = data["kit_revision"]
        completed_count = sum(i["done"] for i in data["kit"])
        with st.expander(f'🎒 출근 준비 · {completed_count}/{len(data["kit"])}', expanded=False):
            st.caption("아빠 개인용 · 체크 후 저장해 주세요.")
            with st.form(f"kit_{revision}"):
                checks = {i["id"]: st.checkbox(i["text"], value=i["done"], key=f'kit_{revision}_{i["id"]}') for i in data["kit"]}
                if st.form_submit_button("준비물 체크 저장", type="primary", use_container_width=True):
                    if run_change(lambda d: update_kit(d, revision, checks=checks), "준비물 체크를 저장했어요"):
                        st.rerun(scope="fragment")
            if st.button("🔄 새 출근 준비", key=f"reset_{revision}", use_container_width=True):
                if run_change(lambda d: update_kit(d, revision, reset=True), "준비물 체크를 초기화했어요"):
                    st.rerun(scope="fragment")

    with st.expander("🕰️ 과거 기록 · 완료된 부탁"):
        st.caption("최근 상태 100건 · 부탁 최대 200건 보관. 상태 변경 전 가족 확인도 함께 기록해요.")
        done_items = [r for r in data["requests"] if r["done_at"]]
        st.markdown("**완료된 부탁**")
        if not done_items:
            st.caption("완료된 부탁이 없어요.")
        for item in done_items:
            render_request(item)
        st.markdown("**이전 상태**")
        if not data["history"]:
            st.caption("이전 상태가 없어요.")
        for entry in data["history"]:
            s = entry["status"]
            st.write(f'**{pretty(s["updated_at"])}** · {s["shift"]} · {s["state"]} · {s["condition"]}')
            st.write(f'연락: {s["contact"] or "미입력"} / 메모: {s["memo"] or "없음"}')
            st.caption("당시 확인: " + (", ".join(f'{n} {pretty(a["at"])}' for n, a in entry["acks"].items()) or "없음"))

    with st.expander("⚙️ 설정 · 이용 안내"):
        st.write(f"현재 접속: {MEMBER} ({st.session_state.role})")
        st.caption("가족 이름은 확인자 식별에 사용합니다. 이름을 바꾸려면 로그아웃 후 다시 접속하세요.")
        if DAD:
            with st.form(f"kit_edit_{data['kit_revision']}"):
                texts = st.text_area("개인 준비물 수정 (한 줄에 하나, 최대 20개)", value="\n".join(i["text"] for i in data["kit"]), height=140, max_chars=1100)
                st.caption("동일한 이름의 항목은 체크 유지 · 삭제 항목은 제거 · 새 항목은 미체크")
                if st.form_submit_button("준비물 목록 저장", use_container_width=True):
                    if run_change(lambda d: update_kit(d, data["kit_revision"], texts=texts), "준비물 목록을 저장했어요"):
                        st.rerun(scope="fragment")
        st.caption("모든 시각은 한국 시간입니다. 앱이 다시 시작되어도 외부 DB의 기록은 유지됩니다. 작성 중인 미저장 입력은 기기 간 공유되지 않습니다. 완료된 부탁에서 완료를 해제하면 다시 처리 목록에 표시됩니다.")
        st.caption("가족 전용 앱입니다. 근무지, 수용자, 보안업무 등 민감정보를 기록하지 마세요. 공유 비밀번호와 DB 비밀 값은 GitHub에 올리지 마세요.")
        if st.button("로그아웃", key="logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()


dashboard()
