# -*- coding: utf-8 -*-
"""
CGV IMAX 예매 오픈 알리미 - GUI
- 봇 로직(cgv_imax_alarm.py)을 그대로 재사용. 같은 config/targets/subscribers/state 파일 공유.
- 토큰 입력, 감시 영화 추가/삭제, 감시 시작/정지, 현재 IMAX 상태 확인, 실시간 로그.
실행: gui.bat 또는  pythonw gui.py
"""
import os, sys, time, threading, queue
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cgv_imax_alarm as core

ALL_THEATERS = {"0013": "용산아이파크몰", "0074": "왕십리", "0199": "천호"}


def _resource(name):
    """일반 실행/ exe(PyInstaller) 둘 다에서 동봉 리소스 경로 반환."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class App:
    def __init__(self, root):
        self.root = root
        root.title("CGV IMAX 예매 알리미")
        root.geometry("780x640")
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        core.migrate_config()  # 옛 8시간 요약주기를 6시간으로 1회 자동 전환
        self.cfg = core.load_json(core.CONFIG_PATH, dict(core.DEFAULT_CONFIG))
        if "theaters" not in self.cfg or not self.cfg["theaters"]:
            self.cfg["theaters"] = dict(ALL_THEATERS)
        self._build()
        self._refresh_targets()
        self.q.put(("subs", len(core.load_json(core.SUBS_PATH, []))))
        self.root.after(250, self._drain)
        self.root.after(500, self._live_refresh)  # 켜지면 현재 IMAX 자동 조회
        self.root.after(900, self._autostart)     # 토큰 저장돼 있으면 자동으로 감시 시작
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------
    def _build(self):
        pad = {"padx": 6, "pady": 4}

        # 설정
        f1 = ttk.LabelFrame(self.root, text="1. 설정")
        f1.pack(fill="x", **pad)
        ttk.Label(f1, text="텔레그램 봇 토큰").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.token_var = tk.StringVar(value=self.cfg.get("telegram_token", ""))
        self.token_entry = ttk.Entry(f1, textvariable=self.token_var, width=58, show="*")
        self.token_entry.grid(row=0, column=1, columnspan=3, sticky="we", padx=6)
        self.show_tok = tk.BooleanVar(value=False)
        ttk.Checkbutton(f1, text="보기", variable=self.show_tok, command=self._toggle_tok).grid(row=0, column=4, padx=4)

        ttk.Label(f1, text="감시 주기(초)").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.interval_var = tk.StringVar(value=str(self.cfg.get("poll_interval_sec", 120)))
        ttk.Entry(f1, textvariable=self.interval_var, width=8).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(f1, text="감시 극장").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.th_vars = {}
        cf = ttk.Frame(f1)
        cf.grid(row=2, column=1, columnspan=4, sticky="w")
        for i, (no, nm) in enumerate(ALL_THEATERS.items()):
            v = tk.BooleanVar(value=(no in self.cfg.get("theaters", {})))
            self.th_vars[no] = v
            ttk.Checkbutton(cf, text=nm, variable=v).grid(row=0, column=i, padx=6)
        ttk.Button(f1, text="설정 저장", command=self._save_cfg).grid(row=1, column=4, padx=6, sticky="e")
        f1.columnconfigure(1, weight=1)

        # 영화 + 상태
        mid = ttk.Frame(self.root)
        mid.pack(fill="both", expand=False, **pad)

        f2 = ttk.LabelFrame(mid, text="2. 감시 영화")
        f2.pack(side="left", fill="both", expand=True)
        guide = ("사용법: 아래 칸에 영화 제목을 자연어로 입력하고 [추가].\n"
                 "정식 제목 전부 아니어도 일부만 맞으면 됩니다 (예: 모아나, 아바타).\n"
                 "추가하면 로그에 매칭 결과(✓ 찾음 / ⚠ 없음)가 표시됩니다.\n"
                 "예매 열리는 순간 + 6시간마다 상영작 요약이 텔레그램으로 갑니다.")
        ttk.Label(f2, text=guide, foreground="#555", justify="left",
                  wraplength=300).pack(anchor="w", padx=6, pady=(4, 0))
        self.lst = tk.Listbox(f2, height=8)
        self.lst.pack(fill="both", expand=True, padx=6, pady=4)
        addf = ttk.Frame(f2)
        addf.pack(fill="x", padx=6, pady=4)
        self.mov_var = tk.StringVar()
        e = ttk.Entry(addf, textvariable=self.mov_var)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self._add_movie())
        ttk.Button(addf, text="추가", command=self._add_movie).pack(side="left", padx=3)
        ttk.Button(addf, text="선택 삭제", command=self._del_movie).pack(side="left")

        f3 = ttk.LabelFrame(mid, text="3. 현재 IMAX 상태")
        f3.pack(side="left", fill="both", expand=True, padx=(8, 0))

        ttk.Label(f3, text="알림 받을 영화", foreground="#555").pack(anchor="w", padx=6, pady=(4, 0))
        self.tree = ttk.Treeview(f3, columns=("state",), show="tree headings", height=4)
        self.tree.heading("#0", text="영화")
        self.tree.column("#0", width=110)
        self.tree.heading("state", text="지금 상태")
        self.tree.column("state", width=210, anchor="w")
        self.tree.pack(fill="x", padx=6, pady=(0, 2))
        self.btn_status = ttk.Button(f3, text="새로고침", command=self._refresh_status)
        self.btn_status.pack(anchor="e", padx=6, pady=(0, 6))

        live_head = ttk.Frame(f3)
        live_head.pack(fill="x", padx=6, pady=(2, 0))
        ttk.Label(live_head, text="현재 전체 IMAX (30분 자동갱신)", foreground="#555").pack(side="left")
        self.live_info = tk.StringVar(value="불러오는 중...")
        ttk.Label(live_head, textvariable=self.live_info, foreground="#888").pack(side="right")
        self.live_tree = ttk.Treeview(f3, columns=("y", "w", "c"), show="tree headings", height=6)
        self.live_tree.heading("#0", text="영화")
        self.live_tree.column("#0", width=160)
        for c, t in (("y", "용산"), ("w", "왕십리"), ("c", "천호")):
            self.live_tree.heading(c, text=t)
            self.live_tree.column(c, width=50, anchor="center")
        self.live_tree.pack(fill="both", expand=True, padx=6, pady=(0, 2))
        self.btn_all = ttk.Button(f3, text="지금 새로고침", command=self._live_refresh)
        self.btn_all.pack(anchor="e", padx=6, pady=(0, 4))

        # 제어
        f4 = ttk.LabelFrame(self.root, text="4. 감시 제어")
        f4.pack(fill="x", **pad)
        self.btn_run = ttk.Button(f4, text="감시 시작", command=self._toggle_run)
        self.btn_run.pack(side="left", padx=8, pady=6)
        self.status_var = tk.StringVar(value="상태: 정지")
        ttk.Label(f4, textvariable=self.status_var).pack(side="left", padx=10)
        self.subs_var = tk.StringVar(value="구독자 0명")
        ttk.Label(f4, textvariable=self.subs_var).pack(side="left", padx=10)

        f5 = ttk.LabelFrame(self.root, text="로그")
        f5.pack(fill="both", expand=True, **pad)
        self.logbox = scrolledtext.ScrolledText(f5, height=10, state="disabled", wrap="word")
        self.logbox.pack(fill="both", expand=True, padx=6, pady=4)

    def _toggle_tok(self):
        self.token_entry.config(show="" if self.show_tok.get() else "*")

    # ---------- helpers ----------
    def log(self, msg):
        self.q.put(("log", f"[{time.strftime('%H:%M:%S')}] {msg}"))

    def _bg(self, fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    def _save_cfg(self):
        try:
            iv = int(self.interval_var.get())
            if iv < 30:
                iv = 30
        except ValueError:
            messagebox.showwarning("확인", "감시 주기는 숫자(초)여야 합니다.")
            return
        theaters = {no: ALL_THEATERS[no] for no, v in self.th_vars.items() if v.get()}
        if not theaters:
            messagebox.showwarning("확인", "극장을 최소 1개 선택하세요.")
            return
        self.cfg["telegram_token"] = self.token_var.get().strip()
        self.cfg["poll_interval_sec"] = iv
        self.cfg["theaters"] = theaters
        core.save_json(core.CONFIG_PATH, self.cfg)
        self.interval_var.set(str(iv))
        self.log("설정을 저장했습니다.")

    def _refresh_targets(self):
        self.lst.delete(0, "end")
        for t in core.load_json(core.TARGETS_PATH, []):
            self.lst.insert("end", t)

    def _add_movie(self):
        title = self.mov_var.get().strip()
        if not title:
            return
        targets = core.load_json(core.TARGETS_PATH, [])
        if title in targets:
            messagebox.showinfo("확인", f"이미 감시 중: {title}")
            return
        targets.append(title)
        core.save_json(core.TARGETS_PATH, targets)
        self.mov_var.set("")
        self._refresh_targets()
        self.log(f"추가: {title} (확인 중...)")
        self._bg(self._verify, title)

    def _verify(self, title):
        try:
            ms = core.resolve_movies(title)
            if ms:
                self.log(f"  ✓ '{title}' → " + ", ".join(m["movNm"] for m in ms[:3]))
            else:
                self.log(f"  ⚠ '{title}' 예매가능 영화 없음(아직 미오픈이거나 제목 확인 필요)")
        except Exception as e:
            self.log(f"  확인 실패: {e}")

    def _del_movie(self):
        sel = self.lst.curselection()
        if not sel:
            return
        title = self.lst.get(sel[0])
        targets = [t for t in core.load_json(core.TARGETS_PATH, []) if t != title]
        core.save_json(core.TARGETS_PATH, targets)
        self._refresh_targets()
        self.log(f"삭제: {title}")

    def _refresh_status(self):
        self.btn_status.config(state="disabled")
        self._bg(self._status_job)

    def _live_refresh(self):
        self.live_info.set("불러오는 중...")
        self.btn_all.config(state="disabled")
        self._bg(self._live_job)

    def _live_job(self):
        rows = []
        try:
            theaters = self.cfg.get("theaters", ALL_THEATERS)
            movie_sites = {}
            for site_no in theaters:
                movies = core.site_imax_movies(site_no)
                if movies is None:
                    continue
                for mv in movies:
                    movie_sites.setdefault(mv, set()).add(site_no)
            rows = sorted(movie_sites.items(), key=lambda x: x[0])
        except Exception as e:
            self.q.put(("log", f"[{time.strftime('%H:%M:%S')}] 현재 IMAX 조회 실패: {e}"))
        self.q.put(("live_status", rows))

    def _status_job(self):
        rows = []
        try:
            theaters = self.cfg.get("theaters", ALL_THEATERS)
            for title in core.load_json(core.TARGETS_PATH, []):
                ms = core.resolve_movies(title)
                if not ms:
                    rows.append((title, None))  # None = 예매가능 영화 자체가 없음(미오픈)
                    continue
                for m in ms:
                    sites = core.imax_open_sites(m["movNo"]) or {}
                    rows.append((m["movNm"], set(sites.keys())))
        except Exception as e:
            self.q.put(("log", f"[{time.strftime('%H:%M:%S')}] 상태 조회 실패: {e}"))
        self.q.put(("status", rows))

    def _autostart(self):
        """토큰이 저장돼 있으면 실행 직후 자동으로 감시 시작(부팅 자동시작 대비)."""
        tok = self.token_var.get().strip()
        if tok and "여기에" not in tok and not (self.worker and self.worker.is_alive()):
            self.log("저장된 토큰 확인 → 자동으로 감시를 시작합니다.")
            self._toggle_run()

    def _toggle_run(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
            self.btn_run.config(state="disabled")
            self.log("정지 요청...")
            return
        token = self.token_var.get().strip()
        if not token or "여기에" in token:
            messagebox.showwarning("확인", "텔레그램 봇 토큰을 입력하고 '설정 저장'을 누르세요.")
            return
        self._save_cfg()
        self.stop_flag.clear()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    def _worker_loop(self):
        token = self.cfg.get("telegram_token", "").strip()
        theaters = self.cfg.get("theaters", dict(ALL_THEATERS))
        interval = int(self.cfg.get("poll_interval_sec", 120))
        summary_interval = int(self.cfg.get("summary_interval_sec", 21600))  # 기본 6시간
        self.q.put(("running", True))
        self.log(f"감시 시작. 극장={list(theaters.values())} 주기={interval}s")
        state_exists = os.path.exists(core.STATE_PATH)
        state = core.load_json(core.STATE_PATH, {"seen": {}})
        baseline_done = state_exists
        offset = None
        try:
            init = core.tg_call(token, "getUpdates", timeout=0)
            if init.get("ok") and init["result"]:
                offset = init["result"][-1]["update_id"] + 1
        except Exception as e:
            self.log(f"텔레그램 초기화 경고: {e}")
        last_poll = 0.0
        last_summary = time.time()  # 시작 직후엔 안 보내고, 다음 주기부터
        core.RUNTIME["start"] = time.time()
        while not self.stop_flag.is_set():
            try:
                subs = core.load_json(core.SUBS_PATH, [])
                targets = core.load_json(core.TARGETS_PATH, [])
                res = core.tg_call(token, "getUpdates", offset=offset, timeout=8)
                if res.get("ok"):
                    changed = False
                    for upd in res["result"]:
                        offset = upd["update_id"] + 1
                        core.handle_update(token, upd, subs, targets)
                        changed = True
                    if changed:
                        self.q.put(("subs", len(core.load_json(core.SUBS_PATH, []))))
                        self.q.put(("targets_changed", None))
                now = time.time()
                if now - last_poll >= interval:
                    last_poll = now
                    core.RUNTIME["last_poll"] = now
                    targets = core.load_json(core.TARGETS_PATH, [])
                    if not targets:
                        self.log("감시할 영화가 없습니다. 영화를 추가하세요.")
                    else:
                        baseline = not baseline_done
                        alerts, baseline_open = core.check_once(theaters, targets, state, baseline)
                        core.save_json(core.STATE_PATH, state)
                        if baseline:
                            baseline_done = True
                            summary = ("🟢 감시 시작. 현재 이미 IMAX 예매중:\n" +
                                       ("\n".join("• " + x for x in baseline_open) if baseline_open else "• (없음)"))
                            if subs:
                                core.broadcast(token, subs, summary)
                            self.log("[베이스라인] " + summary.replace("\n", " "))
                        for a in alerts:
                            dead = core.broadcast(token, subs, a)
                            if dead:
                                subs = [c for c in subs if c not in dead]
                                core.save_json(core.SUBS_PATH, subs)
                            self.log("*** 알림 발송: " + a.split("\n")[0])
                        self.log(f"점검 완료. 새 알림 {len(alerts)}건 / 구독자 {len(subs)}명")

                # 주기적 상영작 요약 발송 (기본 6시간마다). 명령창 경로와 동일 로직.
                if time.time() - last_summary >= summary_interval:
                    last_summary = time.time()
                    subs = core.load_json(core.SUBS_PATH, [])
                    if subs:
                        msg = core.build_summary(theaters)
                        core.broadcast(token, subs, msg)
                        core.RUNTIME["last_summary"] = time.time()
                        core.RUNTIME["last_summary_n"] = len(subs)
                        self.log("상영작 요약 발송. " + msg.split("\n")[0])
            except Exception as e:
                self.log(f"루프 오류: {e}")
                time.sleep(5)
        self.q.put(("running", False))
        self.log("감시 정지됨")

    # ---------- queue drain (UI 스레드) ----------
    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.logbox.config(state="normal")
                    self.logbox.insert("end", val + "\n")
                    self.logbox.see("end")
                    self.logbox.config(state="disabled")
                elif kind == "subs":
                    self.subs_var.set(f"구독자 {val}명")
                elif kind == "running":
                    if val:
                        self.status_var.set("상태: 실행중 🟢")
                        self.btn_run.config(text="감시 정지", state="normal")
                    else:
                        self.status_var.set("상태: 정지")
                        self.btn_run.config(text="감시 시작", state="normal")
                elif kind == "targets_changed":
                    self._refresh_targets()
                elif kind == "status":
                    self._fill_status(val)
                elif kind == "live_status":
                    self._fill_live(val)
        except queue.Empty:
            pass
        self.root.after(300, self._drain)

    def _fill_status(self, rows):
        for i in self.tree.get_children():
            self.tree.delete(i)
        names = {"0013": "용산", "0074": "왕십리", "0199": "천호"}
        for name, sites in rows:
            if sites is None:
                txt = "예매 전 (아직 안 열림)"
            elif sites:
                opened = ", ".join(names[s] for s in ("0013", "0074", "0199") if s in sites)
                txt = f"있음! → {opened}"
            else:
                txt = "없음 (IMAX 미편성)"
            self.tree.insert("", "end", text=name, values=(txt,))
        self.btn_status.config(state="normal")

    def _fill_live(self, rows):
        for i in self.live_tree.get_children():
            self.live_tree.delete(i)
        mark = lambda no, s: "O" if no in s else "X"
        if rows:
            for name, sites in rows:
                self.live_tree.insert("", "end", text=name,
                                      values=(mark("0013", sites), mark("0074", sites), mark("0199", sites)))
        else:
            self.live_tree.insert("", "end", text="(현재 IMAX 상영작 없음)", values=("", "", ""))
        self.live_info.set(f"갱신 {time.strftime('%H:%M')}")
        self.btn_all.config(state="normal")
        # 30분 후 자동 재조회 예약
        self.root.after(30 * 60 * 1000, self._live_refresh)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
        self.root.after(200, self.root.destroy)


def main():
    root = tk.Tk()
    try:
        _icon = tk.PhotoImage(file=_resource("moana.png"))
        root.iconphoto(True, _icon)
        root._icon_ref = _icon  # 가비지컬렉션 방지
    except Exception:
        pass
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
