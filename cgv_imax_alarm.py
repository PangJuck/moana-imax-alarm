# -*- coding: utf-8 -*-
"""
CGV IMAX 예매 오픈 텔레그램 알리미
- 등록한 영화의 IMAX 예매가 용산/왕십리/천호에서 열리면 텔레그램으로 알림
- 순수 requests (브라우저 불필요). CGV 신규 API + 요청서명(HMAC) 사용.
사용:
  python cgv_imax_alarm.py          # 상시 실행(봇)
  python cgv_imax_alarm.py check    # 1회 점검(현재 열린 IMAX 출력, 텔레그램 미사용)
"""
import os, sys, time, json, hmac, hashlib, base64, traceback
from urllib.parse import urlsplit, urlencode
import requests

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)   # exe로 빌드된 경우 exe와 같은 폴더 기준
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
TARGETS_PATH = os.path.join(BASE, "targets.json")
SUBS_PATH = os.path.join(BASE, "subscribers.json")
STATE_PATH = os.path.join(BASE, "state.json")

VERSION = "v2.2"
API = "https://api.cgv.co.kr"
SECRET = b"ydqXY0ocnFLmJGHr_zNzFcpjwAsXq_8JcBNURAkRscg"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
IMAX_GRADE = "03"  # TCSCNS_GRAD_CD: 03 = 아이맥스

DEFAULT_CONFIG = {
    "telegram_token": "여기에_봇토큰_붙여넣기",
    "poll_interval_sec": 120,
    "summary_interval_sec": 21600,
    "theaters": {"0013": "용산아이파크몰", "0074": "왕십리", "0199": "천호"},
}

# 런타임 상태(/status 명령용). 두 실행 경로(cmd_run, gui._worker_loop)가 모두 갱신한다.
RUNTIME = {"start": 0.0, "last_poll": 0.0, "last_summary": 0.0, "last_summary_n": 0, "status_q": 0.0,
           "last_alert": None}  # {"time": float, "movie": str, "theater": str}

# ---------- 파일 입출력 ----------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def migrate_config():
    """기존 배포본 config의 옛 요약주기 기본값(8시간=28800)을 6시간(21600)으로 1회 자동 전환.
    새 exe로 교체만 하면 손대지 않아도 6시간이 적용되도록. (의도적으로 다른 값이면 건드리지 않음)"""
    cfg = load_json(CONFIG_PATH, None)
    if cfg and cfg.get("summary_interval_sec") == 28800:
        cfg["summary_interval_sec"] = 21600
        save_json(CONFIG_PATH, cfg)
        log("요약 주기를 6시간(21600)으로 자동 전환했습니다.")

# ---------- CGV API ----------
def cgv_get(path, params):
    url = API + path + ("?" + urlencode(params) if params else "")
    ts = str(int(time.time()))
    sig = base64.b64encode(
        hmac.new(SECRET, f"{ts}|{urlsplit(url).path}|".encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    headers = {
        "Accept": "application/json", "Accept-Language": "ko-KR",
        "Origin": "https://cgv.co.kr", "Referer": "https://cgv.co.kr/",
        "User-Agent": UA, "X-TIMESTAMP": ts, "X-SIGNATURE": sig,
    }
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    return r.json()

def resolve_movies(title, theaters=None):
    """제목 키워드로 예매가능 영화 검색 -> [{'movNo','movNm'}] (제목 포함 매칭).
    검색 API는 띄어쓰기 토큰 단위로만 매칭해 빈 결과를 줄 때가 있다
    (예: '토이스토리' -> '토이 스토리 5' 누락). 검색이 비면 극장 IMAX
    편성 목록(전체 IMAX 표와 같은 신뢰 데이터)에서 직접 부분매칭으로 폴백한다."""
    norm = lambda s: (s or "").replace(" ", "").lower()
    t = norm(title)
    found, seen = [], set()
    try:
        d = cgv_get("/tme/more/itgrSrch/searchItgrSrchAtktPsblMov",
                    {"coCd": "A420", "swrd": title, "lmtSrchYn": "N"})
    except Exception as e:
        log(f"  검색 실패({title}): {e}")
        d = {}

    def walk(o):
        if isinstance(o, dict):
            mv = o.get("movNo")
            nm = o.get("movNm") or o.get("movNmKor") or o.get("prodNm")
            if mv and nm and mv not in seen and t in norm(nm):
                seen.add(mv); found.append({"movNo": mv, "movNm": nm})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d.get("data"))
    if not found:  # 검색 API 누락 폴백: 극장 편성 목록에서 부분매칭
        for m in imax_movies_now(theaters or DEFAULT_CONFIG["theaters"]):
            if m["movNo"] not in seen and t in norm(m["movNm"]):
                seen.add(m["movNo"]); found.append(m)
    return found

def imax_open_sites(movno):
    """movNo의 IMAX 상영이 열린 극장 {siteNo: siteNm}"""
    try:
        d = cgv_get("/cnm/atkt/searchSscnsSchdCntList", {"coCd": "A420", "movNo": movno})
    except Exception as e:
        log(f"  IMAX조회 실패({movno}): {e}")
        return None
    for row in (d.get("data") or []):
        if row.get("comCd") == "TCSCNS_GRAD_CD" and row.get("comCdval") == IMAX_GRADE:
            return {s.get("siteNo"): s.get("siteNm") for s in (row.get("sscnsSiteList") or [])}
    return {}

# ---------- 텔레그램 ----------
def tg_call(token, method, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.post(url, json=params, timeout=40)
    return r.json()

# ---------- 자동 업데이트 ----------
def fetch_update(config):
    """GitHub Releases에서 최신 버전 확인.
    반환: (latest_tag, download_url) — 이미 최신이면 (None, None)."""
    repo = config.get("update_repo", "PangJuck/moana-imax-alarm")
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/releases/latest",
        timeout=10, headers={"Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    data = resp.json()
    latest = data.get("tag_name", "")
    if not latest or latest <= VERSION:
        return None, None
    for asset in data.get("assets", []):
        if asset["name"] == "moana_alarm.exe":
            return latest, asset["browser_download_url"]
    raise RuntimeError(f"릴리즈 {latest}에 moana_alarm.exe 파일 없음")

def apply_update(url):
    """새 exe 다운로드 후 현재 exe 교체(PyInstaller onefile은 실행 중 덮어쓰기 가능)."""
    current = sys.executable
    tmp = current + ".new"
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(65536):
            f.write(chunk)
    bak = current + ".bak"
    if os.path.exists(bak):
        os.remove(bak)
    os.rename(current, bak)
    os.rename(tmp, current)
    os.remove(bak)

def broadcast(token, subs, text):
    dead = []
    for cid in subs:
        try:
            res = tg_call(token, "sendMessage", chat_id=cid, text=text,
                          parse_mode="HTML", disable_web_page_preview=True)
            if not res.get("ok") and res.get("error_code") in (403, 400):
                dead.append(cid)
        except Exception as e:
            log(f"  전송 실패({cid}): {e}")
    return dead

# ---------- 명령 처리 ----------
HELP = (
    "🎬 <b>CGV IMAX 예매 알리미</b>\n"
    "등록한 영화의 IMAX 예매가 용산·왕십리·천호에서 열리면 알려드려요.\n\n"
    "/status  서버·봇 상태 + 현재 IMAX 확인\n"
    "/test  지금 모든 기능 점검 + 요약 샘플 보기\n"
    "/list  현재 감시 영화 목록\n"
    "/add 제목  감시 영화 추가 (예: /add 아바타)\n"
    "/del 제목  감시 영화 제거\n"
    "/stop  알림 구독 해지\n"
    "/업데이트  최신 버전 확인 및 자동 업데이트\n"
    "/help  도움말"
)

def handle_update(token, upd, subs, targets):
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        return
    chat = msg.get("chat", {})
    cid = chat.get("id")
    text = (msg.get("text") or "").strip()
    if cid is None:
        return
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/help"):
        if cid not in subs:
            subs.append(cid); save_json(SUBS_PATH, subs)
        tg_call(token, "sendMessage", chat_id=cid, text="✅ 구독되었습니다.\n\n" + HELP, parse_mode="HTML")
    elif cmd == "/stop":
        if cid in subs:
            subs.remove(cid); save_json(SUBS_PATH, subs)
        tg_call(token, "sendMessage", chat_id=cid, text="🔕 구독 해지되었습니다. /start 로 다시 구독할 수 있어요.")
    elif cmd == "/list":
        body = "\n".join(f"• {t}" for t in targets) if targets else "(없음)"
        tg_call(token, "sendMessage", chat_id=cid, text=f"🎬 감시 중인 영화:\n{body}")
    elif cmd == "/add":
        if not arg:
            tg_call(token, "sendMessage", chat_id=cid, text="사용법: /add 영화제목")
        elif arg in targets:
            tg_call(token, "sendMessage", chat_id=cid, text=f"이미 감시 중: {arg}")
        else:
            targets.append(arg); save_json(TARGETS_PATH, targets)
            tg_call(token, "sendMessage", chat_id=cid, text=f"➕ 추가됨: {arg}")
    elif cmd == "/del":
        if arg in targets:
            targets.remove(arg); save_json(TARGETS_PATH, targets)
            tg_call(token, "sendMessage", chat_id=cid, text=f"➖ 제거됨: {arg}")
        else:
            tg_call(token, "sendMessage", chat_id=cid, text=f"목록에 없음: {arg}")
    elif cmd == "/업데이트":
        tg_call(token, "sendMessage", chat_id=cid, text=f"🔍 업데이트 확인 중... (현재: {VERSION})")
        try:
            cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
            ver, url = fetch_update(cfg)
            if not ver:
                tg_call(token, "sendMessage", chat_id=cid,
                        text=f"✅ 이미 최신 버전입니다 ({VERSION})")
            else:
                tg_call(token, "sendMessage", chat_id=cid,
                        text=f"⬇️ {ver} 다운로드 중... (잠시 기다려주세요)")
                apply_update(url)
                tg_call(token, "sendMessage", chat_id=cid,
                        text=f"✅ {ver} 업데이트 완료!\n창을 닫고 다시 열면 새 버전이 실행됩니다.")
        except Exception as e:
            tg_call(token, "sendMessage", chat_id=cid, text=f"❌ 업데이트 실패: {e}")
    elif cmd == "/status":
        now = time.time()
        # 즉석 CGV 조회는 1분에 1회로 제한(과한 호출 방지)
        if now - RUNTIME.get("status_q", 0) < 60:
            tg_call(token, "sendMessage", chat_id=cid,
                    text="⏳ 잠시 후 다시 시도해주세요. (현재 IMAX 조회는 1분에 한 번)")
            return
        RUNTIME["status_q"] = now
        theaters = (load_json(CONFIG_PATH, DEFAULT_CONFIG).get("theaters")) or DEFAULT_CONFIG["theaters"]
        start = RUNTIME.get("start", 0)
        up = int(now - start) if start else 0
        if not start:
            up_txt = "방금 시작"
        elif up >= 3600:
            up_txt = f"{up // 3600}시간째"
        elif up >= 60:
            up_txt = f"{up // 60}분째"
        else:
            up_txt = "방금 시작"
        lp = RUNTIME.get("last_poll", 0)
        lp_txt = (f"{time.strftime('%H:%M', time.localtime(lp))} ({int((now - lp) // 60)}분 전)"
                  if lp else "아직 없음")
        ls = RUNTIME.get("last_summary", 0)
        ls_txt = (f"{time.strftime('%H:%M', time.localtime(ls))} ({RUNTIME.get('last_summary_n', 0)}명에게)"
                  if ls else "아직 없음")
        mv_txt = ", ".join(targets) if targets else "(없음, 전체 IMAX 모드)"
        la = RUNTIME.get("last_alert")
        if la:
            la_txt = (f"{la['movie']} — {la['theater']} "
                      f"({time.strftime('%m-%d %H:%M', time.localtime(la['time']))})")
        else:
            la_txt = "(이번 세션 동안 없음)"
        lines = [
            "🟢 <b>정상 작동 중</b>",
            f"· 가동: {up_txt}",
            f"· 마지막 점검: {lp_txt}",
            f"· 마지막 자동 발송: {ls_txt}",
            f"· 감시 영화: {mv_txt}",
            f"· 구독자: {len(subs)}명",
            "",
            f"📣 마지막 예매 오픈 알람\n· {la_txt}",
        ]
        tg_call(token, "sendMessage", chat_id=cid, text="\n".join(lines), parse_mode="HTML")
        # 후속 메시지: 현재 상영작 + 모든 회차 (벽글)
        tg_call(token, "sendMessage", chat_id=cid, text=build_detail(theaters), disable_web_page_preview=True)
    elif cmd == "/test":
        now = time.time()
        # 즉석 CGV 조회는 1분에 1회로 제한(/status 와 공유)
        if now - RUNTIME.get("status_q", 0) < 60:
            tg_call(token, "sendMessage", chat_id=cid,
                    text="⏳ 잠시 후 다시 시도해주세요. (점검은 1분에 한 번)")
            return
        RUNTIME["status_q"] = now
        cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        theaters = cfg.get("theaters") or DEFAULT_CONFIG["theaters"]
        interval = int(cfg.get("summary_interval_sec", 21600))
        hours = max(1, interval // 3600)
        sample = build_summary(theaters)       # 8/6시간마다 가는 그 메시지와 동일
        cgv_ok = "(조회 실패)" not in sample     # build_summary가 실패 극장에 표시
        start = RUNTIME.get("start", 0)
        up = int(now - start) if start else 0
        up_txt = (f"{up // 3600}시간째" if up >= 3600 else (f"{up // 60}분째" if up >= 60 else "방금 시작"))
        ls = RUNTIME.get("last_summary", 0)
        ls_txt = (f"{time.strftime('%H:%M', time.localtime(ls))} ({RUNTIME.get('last_summary_n', 0)}명에게)"
                  if ls else "아직 없음 (다음 주기에 첫 발송)")
        nxt = (ls or start or now) + interval
        nxt_txt = time.strftime('%m-%d %H:%M', time.localtime(nxt))
        la = RUNTIME.get("last_alert")
        if la:
            la_txt = (f"{la['movie']} — {la['theater']} "
                      f"({time.strftime('%m-%d %H:%M', time.localtime(la['time']))})")
        else:
            la_txt = "(이번 세션 동안 없음)"
        report = (
            "🧪 <b>점검 결과</b>\n"
            f"· CGV 연결: {'정상 ✅' if cgv_ok else '실패 ⚠'}\n"
            "· 텔레그램: 정상 ✅ (이 메시지가 증거)\n"
            f"· 구독자: {len(subs)}명\n"
            f"· 가동: {up_txt}\n"
            f"· 요약 주기: {hours}시간마다\n"
            f"· 마지막 요약: {ls_txt}\n"
            f"· 다음 요약 예정: {nxt_txt}\n"
            f"· 마지막 예매 오픈 알람: {la_txt}\n\n"
            f"아래는 {hours}시간마다 자동으로 가는 요약 샘플입니다 👇"
        )
        tg_call(token, "sendMessage", chat_id=cid, text=report, parse_mode="HTML")
        tg_call(token, "sendMessage", chat_id=cid, text=sample, disable_web_page_preview=True)

# ---------- 감지 ----------
def check_once(theaters, targets, state, baseline):
    """CGV 점검. 새로 열린 (영화,극장)에 대한 알림 메시지 리스트 반환."""
    seen = state.setdefault("seen", {})
    alerts, baseline_open = [], []
    for title in targets:
        for mv in resolve_movies(title):
            sites = imax_open_sites(mv["movNo"])
            if sites is None:
                continue
            for site_no, site_nm in theaters.items():
                key = f"{mv['movNo']}|{site_no}"
                present = site_no in sites
                prev = seen.get(key)
                if present:
                    if prev != "open":
                        seen[key] = "open"
                        if baseline:
                            baseline_open.append(f"{mv['movNm']} — {site_nm}")
                        else:
                            msg = (f"🎬 <b>{mv['movNm']}</b>\n"
                                   f"📍 {site_nm} <b>IMAX 예매 오픈!</b>\n")
                            sched = fmt_showtimes(movie_showtimes(site_no, mv["movNo"]))
                            if sched:
                                msg += sched + "\n"
                            msg += "👉 https://cgv.co.kr/"
                            alerts.append(msg)
                            RUNTIME["last_alert"] = {"time": time.time(), "movie": mv["movNm"], "theater": site_nm}
                            log(f"  *** OPEN: {mv['movNm']} @ {site_nm}")
                else:
                    if prev != "closed":
                        seen[key] = "closed"
    return alerts, baseline_open

_LISTING_CACHE = {"at": 0.0, "key": None, "movies": []}

def imax_movies_now(theaters, ttl=180):
    """현재 IMAX 편성 영화 [{'movNo','movNm'}] (극장 listing 기반, 신뢰 데이터).
    전체 IMAX 표와 같은 출처이며 movNo도 들어 있어 resolve_movies 폴백/회차 조회에 쓴다.
    호출이 잦을 수 있어 ttl초 동안 결과를 캐시한다."""
    now = time.time()
    key = tuple(sorted(theaters))
    if _LISTING_CACHE["movies"] and _LISTING_CACHE["key"] == key and now - _LISTING_CACHE["at"] < ttl:
        return _LISTING_CACHE["movies"]
    seen, out = set(), []
    for site_no in theaters:
        try:
            d = cgv_get("/cnm/atkt/searchSiteScnscYmdListBySite", {"coCd": "A420", "siteNo": site_no})
        except Exception:
            continue
        for ymd in [x.get("scnYmd") for x in (d.get("data") or []) if x.get("scnYmd")]:
            try:
                r = cgv_get("/cnm/atkt/searchThtAtktMovListByTime",
                            {"coCd": "A420", "siteNo": site_no, "scnYmd": ymd, "gradAttr": "3", "movNo": ""})
            except Exception:
                continue
            for row in (r.get("data") or []):
                mv, nm = row.get("movNo"), row.get("movNm")
                if (row.get("tcscnsGradNm") or "") == "아이맥스" and mv and nm and mv not in seen:
                    seen.add(mv); out.append({"movNo": mv, "movNm": nm})
    _LISTING_CACHE.update(at=now, key=key, movies=out)
    return out

def site_imax_movies(site_no):
    """극장 1곳에 현재 IMAX 예매가능한 영화명 집합 반환. 조회 실패 시 None."""
    try:
        d = cgv_get("/cnm/atkt/searchSiteScnscYmdListBySite", {"coCd": "A420", "siteNo": site_no})
    except Exception as e:
        log(f"  날짜조회 실패({site_no}): {e}")
        return None
    dates = [x.get("scnYmd") for x in (d.get("data") or []) if x.get("scnYmd")]
    movies = set()
    for ymd in dates:
        try:
            r = cgv_get("/cnm/atkt/searchThtAtktMovListByTime",
                        {"coCd": "A420", "siteNo": site_no, "scnYmd": ymd, "gradAttr": "3", "movNo": ""})
            for row in (r.get("data") or []):
                if (row.get("tcscnsGradNm") or "") == "아이맥스" and row.get("movNm"):
                    movies.add(row["movNm"])
        except Exception:
            pass
    return movies

def site_imax_schedule(site_no):
    """극장 1곳의 IMAX 상영 스케줄.
    반환: [{"movNo": str, "movNm": str, "dates": [ymd, ...], "showtimes": {ymd: [HH:MM, ...]}}, ...]
    조회 실패 시 None."""
    try:
        d = cgv_get("/cnm/atkt/searchSiteScnscYmdListBySite", {"coCd": "A420", "siteNo": site_no})
    except Exception as e:
        log(f"  날짜조회 실패({site_no}): {e}")
        return None
    dates = [x.get("scnYmd") for x in (d.get("data") or []) if x.get("scnYmd")]
    acc = {}  # movNo -> {"movNm": str, "dates": set, "showtimes": {ymd: set}}
    for ymd in dates:
        try:
            r = cgv_get("/cnm/atkt/searchThtAtktMovListByTime",
                        {"coCd": "A420", "siteNo": site_no, "scnYmd": ymd, "gradAttr": "3", "movNo": ""})
            for row in (r.get("data") or []):
                if (row.get("tcscnsGradNm") or "") == "아이맥스":
                    mv, nm = row.get("movNo"), row.get("movNm")
                    if mv and nm:
                        if mv not in acc:
                            acc[mv] = {"movNm": nm, "dates": set(), "showtimes": {}}
                        acc[mv]["dates"].add(ymd)
                        t = row.get("scnsrtTm")
                        if t:
                            t = str(t)
                            if len(t) == 4 and t.isdigit():
                                t = t[:2] + ":" + t[2:]
                            acc[mv]["showtimes"].setdefault(ymd, set()).add(t)
        except Exception:
            pass
    return [{"movNo": mv, "movNm": v["movNm"], "dates": sorted(v["dates"]),
             "showtimes": {ymd: sorted(times) for ymd, times in v["showtimes"].items()}}
            for mv, v in acc.items()]

def check_all_once(theaters, state, baseline):
    """전체 IMAX 모드 점검. 극장별로 새로 등장한 영화 = 예매 오픈 알림."""
    seen_all2 = state.setdefault("seen_all2", {})  # {site_no: [movNo, ...]} — v2.1부터 movNo 기준
    alerts, baseline_open = [], []
    for site_no, site_nm in theaters.items():
        schedule = site_imax_schedule(site_no)
        if schedule is None:
            continue  # 조회 실패한 극장은 이번 회차 건너뜀(state 유지)
        id_to_info = {m["movNo"]: m for m in schedule}
        cur_ids = set(id_to_info)
        # 이 극장에 seen_all2 기록이 없으면 = 신규설치 첫 점검 또는 v2.0→v2.1 업그레이드.
        # 이때는 알람을 쏘지 않고 현재 상영작을 기준선으로만 기록한다(오탐 폭탄 방지).
        first_time = site_no not in seen_all2
        new_ids = cur_ids - set(seen_all2.get(site_no, []))
        for mov_no in sorted(new_ids):
            mov_nm = id_to_info[mov_no]["movNm"]
            if baseline:
                baseline_open.append(f"{mov_nm} — {site_nm}")
            elif first_time:
                pass  # 업그레이드 첫 점검: 기준만 기록, 알람 생략
            else:
                msg = (f"🎬 <b>{mov_nm}</b>\n"
                       f"📍 {site_nm} <b>IMAX 예매 오픈!</b>\n")
                sched = fmt_showtimes(movie_showtimes(site_no, mov_no))
                if sched:
                    msg += sched + "\n"
                msg += "👉 https://cgv.co.kr/"
                alerts.append(msg)
                RUNTIME["last_alert"] = {"time": time.time(), "movie": mov_nm, "theater": site_nm}
                log(f"  *** OPEN(전체): {mov_nm} @ {site_nm}")
        seen_all2[site_no] = sorted(cur_ids)
    return alerts, baseline_open

def movie_showtimes(site_no, movno, max_days=2):
    """그 극장에서 영화의 IMAX 회차를 가장 빠른 max_days일치 반환.
    반환: [(scnYmd, [시간...]), ...]. 회차 시간 필드는 scnsrtTm
    (인수인계 문서 기준, 집 PC 실측 검증 권장)."""
    try:
        d = cgv_get("/cnm/atkt/searchSiteScnscYmdListBySite", {"coCd": "A420", "siteNo": site_no})
    except Exception:
        return []
    dates = sorted(x.get("scnYmd") for x in (d.get("data") or []) if x.get("scnYmd"))
    result = []
    for ymd in dates:
        if len(result) >= max_days:
            break
        try:
            r = cgv_get("/cnm/atkt/searchThtAtktMovListByTime",
                        {"coCd": "A420", "siteNo": site_no, "scnYmd": ymd, "gradAttr": "3", "movNo": movno})
        except Exception:
            continue
        times = []
        for row in (r.get("data") or []):
            if (row.get("tcscnsGradNm") or "") == "아이맥스":
                t = row.get("scnsrtTm")
                if t:
                    t = str(t)
                    if len(t) == 4 and t.isdigit():
                        t = t[:2] + ":" + t[2:]
                    times.append(t)
        if times:
            result.append((ymd, sorted(set(times))))
    return result

def fmt_showtimes(showtimes):
    """movie_showtimes 결과를 메시지용 문자열로. 비면 빈 문자열."""
    if not showtimes:
        return ""
    lines = ["🗓 가장 빠른 회차"]
    for ymd, times in showtimes:
        md = f"{ymd[4:6]}/{ymd[6:8]}" if len(ymd) == 8 else ymd
        lines.append(f"· {md} " + ", ".join(times))
    return "\n".join(lines)

def fmt_date_range(dates):
    """[ymd, ...] → '06/23~06/25' 또는 '06/23' 형식."""
    if not dates:
        return ""
    fmt = lambda y: f"{y[4:6]}/{y[6:8]}" if len(y) == 8 else y
    if len(dates) == 1:
        return fmt(dates[0])
    return f"{fmt(dates[0])}~{fmt(dates[-1])}"

def fmt_md(ymd):
    """'20260623' → '06/23'."""
    return f"{ymd[4:6]}/{ymd[6:8]}" if len(ymd) == 8 else ymd

def build_summary(theaters):
    """6시간 자동 요약: 극장·영화별 정확한 상영 날짜만 나열(범위 아님). 회차 시간 제외 → 가벼움."""
    lines = ["세상은 wz를 중심으로 돌아갑니다", "",
             f"🎬 현재 IMAX 상영작 ({time.strftime('%m-%d %H:%M')})"]
    for site_no, site_nm in theaters.items():
        schedule = site_imax_schedule(site_no)
        if schedule is None:
            lines.append(f"[{site_nm}] (조회 실패)")
        elif schedule:
            lines.append(f"[{site_nm}]")
            for m in sorted(schedule, key=lambda x: x["movNm"]):
                ds = ", ".join(fmt_md(y) for y in m["dates"])
                lines.append(f"  · {m['movNm']}  {ds}" if ds else f"  · {m['movNm']}")
        else:
            lines.append(f"[{site_nm}] 없음")
    return "\n".join(lines)

def build_detail(theaters):
    """전체 상세(벽글): 극장·영화별 날짜 + 그 날의 모든 회차 시간. /status 후속 메시지용."""
    lines = [f"🎬 현재 IMAX 상영작 — 회차 전체 ({time.strftime('%m-%d %H:%M')})"]
    for site_no, site_nm in theaters.items():
        schedule = site_imax_schedule(site_no)
        if schedule is None:
            lines.append(f"[{site_nm}] (조회 실패)")
        elif schedule:
            lines.append(f"[{site_nm}]")
            for m in sorted(schedule, key=lambda x: x["movNm"]):
                dr = fmt_date_range(m["dates"])
                lines.append(f"  · {m['movNm']}  {dr}" if dr else f"  · {m['movNm']}")
                for ymd in m["dates"]:
                    times = m["showtimes"].get(ymd, [])
                    if times:
                        lines.append(f"      {fmt_md(ymd)}  " + ", ".join(times))
        else:
            lines.append(f"[{site_nm}] 없음")
    return "\n".join(lines)

# ---------- 메인 ----------
def cmd_check():
    cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    theaters = cfg["theaters"]
    targets = load_json(TARGETS_PATH, [])
    if not targets:
        print("targets.json 이 비어있음 → 전체 IMAX 모드")
        print(f"감시 극장: {list(theaters.values())}\n")
        for site_no, site_nm in theaters.items():
            movies = site_imax_movies(site_no)
            if movies is None:
                print(f"  {site_nm}: 조회 실패")
            else:
                print(f"  {site_nm}: IMAX 예매중 = {sorted(movies) if movies else '없음'}")
        return
    print(f"감시 영화: {targets}")
    print(f"감시 극장: {list(theaters.values())}\n")
    for title in targets:
        movies = resolve_movies(title)
        if not movies:
            print(f"  '{title}': 예매가능 영화 없음(미오픈/제목불일치)")
        for mv in movies:
            sites = imax_open_sites(mv["movNo"]) or {}
            opened = [theaters[s] for s in theaters if s in sites]
            print(f"  '{title}' → {mv['movNm']}({mv['movNo']}): IMAX 열린 극장 = {opened if opened else '없음'}")

def cmd_run():
    migrate_config()
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        save_json(CONFIG_PATH, DEFAULT_CONFIG)
        print(f"config.json 을 생성했습니다. telegram_token 을 채운 뒤 다시 실행하세요:\n  {CONFIG_PATH}")
        return
    token = cfg.get("telegram_token", "")
    if not token or "여기에" in token:
        print("config.json 의 telegram_token 을 실제 봇 토큰으로 채워주세요.")
        return
    theaters = cfg["theaters"]
    interval = int(cfg.get("poll_interval_sec", 120))
    summary_interval = int(cfg.get("summary_interval_sec", 21600))  # 기본 6시간
    subs = load_json(SUBS_PATH, [])
    targets = load_json(TARGETS_PATH, [])
    if not os.path.exists(TARGETS_PATH):
        save_json(TARGETS_PATH, targets)
    state_exists = os.path.exists(STATE_PATH)
    state = load_json(STATE_PATH, {"seen": {}})

    log(f"시작. 극장={list(theaters.values())} 영화={targets} 구독자={len(subs)}명 간격={interval}s")
    # 텔레그램 업데이트 오프셋 초기화(과거 메시지 무시)
    offset = None
    try:
        init = tg_call(token, "getUpdates", timeout=0)
        if init.get("ok") and init["result"]:
            offset = init["result"][-1]["update_id"] + 1
    except Exception as e:
        log(f"getUpdates 초기화 실패: {e}")

    last_poll = 0.0
    last_summary = time.time()  # 시작 직후엔 안 보내고, 다음 주기부터
    baseline_done = state_exists  # state.json 이 이미 있으면 베이스라인 끝난 것
    RUNTIME["start"] = time.time()
    while True:
        try:
            # 1) 텔레그램 명령 수신(롱폴 최대 15초)
            res = tg_call(token, "getUpdates", offset=offset, timeout=15)
            if res.get("ok"):
                for upd in res["result"]:
                    offset = upd["update_id"] + 1
                    try:
                        handle_update(token, upd, subs, targets)
                    except Exception as e:
                        log(f"명령 처리 오류: {e}")

            # 2) 주기적 CGV 점검
            now = time.time()
            if now - last_poll >= interval:
                last_poll = now
                RUNTIME["last_poll"] = now
                targets = load_json(TARGETS_PATH, targets)  # 외부/명령 변경 반영
                baseline = not baseline_done
                if targets:
                    alerts, baseline_open = check_once(theaters, targets, state, baseline)
                else:
                    alerts, baseline_open = check_all_once(theaters, state, baseline)
                save_json(STATE_PATH, state)
                if baseline:
                    baseline_done = True
                    mode_label = "특정 영화" if targets else "전체 IMAX"
                    summary = (f"🟢 감시를 시작합니다. (모드: {mode_label})\n현재 이미 IMAX 예매중:\n" +
                               ("\n".join(f"• {x}" for x in baseline_open) if baseline_open else "• (없음)"))
                    if subs:
                        broadcast(token, subs, summary)
                    log("베이스라인 완료. " + summary.replace("\n", " "))
                for a in alerts:
                    dead = broadcast(token, subs, a)
                    for cid in dead:
                        if cid in subs:
                            subs.remove(cid)
                    if dead:
                        save_json(SUBS_PATH, subs)
                log(f"점검 완료. 새 알림 {len(alerts)}건, 구독자 {len(subs)}명")

            # 3) 주기적 상영작 요약 발송
            if time.time() - last_summary >= summary_interval:
                last_summary = time.time()
                if subs:
                    msg = build_summary(theaters)
                    broadcast(token, subs, msg)
                    RUNTIME["last_summary"] = time.time()
                    RUNTIME["last_summary_n"] = len(subs)
                    log("상영작 요약 발송. " + msg.replace("\n", " | "))
        except Exception:
            log("루프 오류:\n" + traceback.format_exc())
            time.sleep(10)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        cmd_check()
    else:
        cmd_run()
