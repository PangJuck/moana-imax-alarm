# -*- coding: utf-8 -*-
"""
지금 현재 용산/왕십리/천호에 떠 있는 IMAX 영화를 1회 조회해 출력.
(영화 이름을 등록하지 않고 "극장에 뜬 것 전부"를 보는 용도. '전체 IMAX 감시 모드'의 1회 조회판 = 폴링 루프에 그대로 이식 가능.)
실행: python show_current.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cgv_imax_alarm as core

THEATERS = {"0013": "용산아이파크몰", "0074": "왕십리", "0199": "천호"}

for no, nm in THEATERS.items():
    try:
        d = core.cgv_get("/cnm/atkt/searchSiteScnscYmdListBySite", {"coCd": "A420", "siteNo": no})
        dates = [x.get("scnYmd") for x in (d.get("data") or []) if x.get("scnYmd")]
    except Exception as e:
        print(f"\n=== {nm} === 날짜조회 실패: {e}")
        continue
    movies = {}
    for ymd in dates:
        try:
            r = core.cgv_get("/cnm/atkt/searchThtAtktMovListByTime",
                             {"coCd": "A420", "siteNo": no, "scnYmd": ymd, "gradAttr": "3", "movNo": ""})
            for row in (r.get("data") or []):
                if (row.get("tcscnsGradNm") or "") == "아이맥스":
                    movies.setdefault(row.get("movNm"), set()).add(ymd)
        except Exception:
            pass
    print(f"\n=== {nm} (IMAX) — 예매가능 {len(dates)}일, 영화 {len(movies)}편 ===")
    for mv, ds in sorted(movies.items(), key=lambda x: -len(x[1])):
        ds2 = sorted(ds)
        print(f"  {mv}  ({len(ds)}일, {ds2[0]}~{ds2[-1]})")
