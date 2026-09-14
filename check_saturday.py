#!/usr/bin/env python3
"""
앵봉산캠핑장(xticket) 빈자리 조회기.

인증 불필요. 세션도 스크립트가 스스로 부트스트랩하므로 쿠키를 수동으로
넣을 필요가 없다. 브라우저가 하는 순서를 그대로 재현한다:

    1) GET /web/main            -> JSESSIONID + AWSELB(스티키 라우팅) 쿠키 획득
    2) POST GetShopInformation  -> shop 을 세션에 등록
    3) POST 조회 API            -> 실제 빈자리 데이터

핵심은 세 요청이 같은 쿠키 jar(특히 AWSELB)를 공유해서 동일한 백엔드
인스턴스로 라우팅되는 것. http.cookiejar 로 자동 유지한다.

사용법:
    python3 check_saturday.py [--day 화|tue|1] [YYYYMM ...]
    (요일 생략 시 토요일, 월 생략 시 이번 달 + 다음 달)

빈자리 판정:
    - GetBookPlayDate.json      : 날짜별 book_remain_count (전체 그룹 합산)
    - GetBookProduct010001.json : 그룹별 사이트. status_code=="0" && select_yn=="1" 이면 예약 가능.
"""
from __future__ import annotations

import calendar
import datetime as dt
import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request

BASE = "https://camp.xticket.kr"
SHOP_ENCODE = "a12d6508ae5ea0562923cb1f2762761f3413ab4c988a6c8aa92ea7873e263bec"
SHOP_CODE = "110821190701"

# 상품 그룹 (GetBookProductGroup.json 응답 기준)
PRODUCT_GROUPS = {
    "0001": "데크캠핑장",
    "0002": "글램핑",
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
REFERER = f"{BASE}/web/main?shopEncode={SHOP_ENCODE}"

# 쿠키 jar 를 유지하는 전역 opener (JSESSIONID/AWSELB 자동 보존)
_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_BOOTSTRAPPED = False


class SessionExpired(RuntimeError):
    pass


def _request(url: str, data: bytes | None = None, ajax: bool = True) -> str:
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data else "GET")
    req.add_header("User-Agent", UA)
    req.add_header("Referer", REFERER)
    req.add_header("Accept", "*/*")
    if data is not None:
        req.add_header("Origin", BASE)
        req.add_header("Content-Type",
                       "application/x-www-form-urlencoded; charset=UTF-8")
    if ajax:
        req.add_header("X-Requested-With", "XMLHttpRequest")
    with _OPENER.open(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def _bootstrap() -> None:
    """세션을 확립하고 shop 을 세션에 등록한다. 최초 1회."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    # 1) main 페이지 GET -> JSESSIONID + AWSELB
    _request(REFERER, data=None, ajax=False)
    # 2) shop 을 세션에 등록
    body = urllib.parse.urlencode({"shop_encode": SHOP_ENCODE}).encode()
    raw = _request(f"{BASE}/Web/Book/GetShopInformation.json", data=body)
    info = json.loads(raw)
    if isinstance(info, dict) and info.get("error"):
        raise RuntimeError(
            "세션 부트스트랩 실패: " + info["error"].get("message", ""))
    _BOOTSTRAPPED = True


def _post(path: str, params: dict) -> dict:
    """xticket .json 엔드포인트에 POST 하고 파싱된 JSON을 반환."""
    _bootstrap()
    body = urllib.parse.urlencode({**params, "shopCode": SHOP_CODE}).encode()
    data = json.loads(_request(f"{BASE}{path}", data=body))
    if isinstance(data, dict) and data.get("error"):
        msg = data["error"].get("message", "")
        if "세션" in msg or data["error"].get("code") == "0009":
            # 세션이 죽었으면 한 번 재부트스트랩 후 재시도
            global _BOOTSTRAPPED
            _BOOTSTRAPPED = False
            _bootstrap()
            data = json.loads(_request(f"{BASE}{path}", data=body))
            if isinstance(data, dict) and data.get("error"):
                raise SessionExpired(data["error"].get("message", msg))
        else:
            raise RuntimeError(f"{path} 오류: {msg}")
    return data


def get_play_dates(year_month: str) -> dict[str, int]:
    """해당 월의 {play_date: book_remain_count} 반환 (예약 가능 날짜만 나옴)."""
    data = _post("/Web/Book/GetBookPlayDate.json", {"play_month": year_month})
    return {
        d["play_date"]: int(d.get("book_remain_count", 0))
        for d in data.get("data", {}).get("bookPlayDateList", [])
    }


def get_available_sites(product_group_code: str, date: str) -> list[dict]:
    """특정 그룹/날짜의 예약 가능한 사이트 목록."""
    data = _post("/Web/Book/GetBookProduct010001.json", {
        "product_group_code": product_group_code,
        "start_date": date,
        "end_date": date,
        "book_days": 1,
        "two_stay_days": 0,
    })
    return [
        p for p in data.get("data", {}).get("bookProductList", [])
        if p.get("status_code") == "0" and p.get("select_yn") == "1"
    ]


WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]


def weekdays_in(year_month: str, weekday: int) -> list[str]:
    """YYYYMM 안의 특정 요일(0=월 ... 6=일) 날짜를 YYYYMMDD 리스트로."""
    year, month = int(year_month[:4]), int(year_month[4:6])
    _, last = calendar.monthrange(year, month)
    out = []
    for day in range(1, last + 1):
        d = dt.date(year, month, day)
        if d.weekday() == weekday:
            out.append(d.strftime("%Y%m%d"))
    return out


def fmt(date: str) -> str:
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}"


def check_month(year_month: str, weekday: int = 5,
                groups: list[str] | None = None) -> list[str]:
    """해당 월의 지정 요일 빈자리를 조회해 출력하고, 빈자리 요약 라인 리스트를 반환.

    groups: 조회/표시할 상품 그룹 코드(['0001']=데크, ['0002']=글램핑).
            None이면 전체 그룹.
    """
    group_items = [(c, n) for c, n in PRODUCT_GROUPS.items()
                   if groups is None or c in groups]
    wl = WEEKDAY_LABELS[weekday]
    print(f"\n=== {year_month[:4]}년 {year_month[4:6]}월 {wl}요일 ===")
    found = []
    try:
        remain = get_play_dates(year_month)
    except SessionExpired as e:
        print(f"  [세션 오류] {e}")
        print("  자동 재접속에도 실패했습니다. 잠시 후 다시 시도하세요.")
        raise SystemExit(2)

    for day in weekdays_in(year_month, weekday):
        total = remain.get(day)
        # 잔여 없는 날(예약불가/매진)은 출력하지 않음
        if not total:  # None 또는 0
            continue

        # 그룹별 상세 빈자리
        parts = []
        available = 0
        for code, name in group_items:
            try:
                sites = get_available_sites(code, day)
            except SessionExpired:
                raise SystemExit(2)
            available += len(sites)
            if sites:
                names = ", ".join(s["product_name"] for s in sites)
                parts.append(f"{name} {len(sites)}자리 [{names}]")
            else:
                parts.append(f"{name} 0")
        # book_remain_count(total)>0이라도 대상 그룹에 실제 예약 가능 사이트가
        # 하나도 없으면(고향사랑기부제 우선예약 등) 오탐이므로 건너뜀
        if available == 0:
            continue
        line = f"{fmt(day)} ({wl}) : 잔여 {total} → " + " / ".join(parts)
        print(f"  {line}")
        found.append(line)

    if not found:
        print("  (잔여 있는 날 없음)")
    return found


def current_and_next_month() -> list[str]:
    """호출 시점 기준 이번 달과 다음 달을 YYYYMM 으로."""
    today = dt.date.today()
    this = today.strftime("%Y%m")
    nxt = (today.replace(day=1) + dt.timedelta(days=32)).strftime("%Y%m")
    return [this, nxt]


WEEKDAY_ALIASES = {
    "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def main() -> None:
    # 사용법:
    #   python3 check_saturday.py                 -> 토요일(전체) + 일요일(데크·이번달)
    #   python3 check_saturday.py --day 화 [YYYYMM ...]  -> 특정 요일만(전체 그룹)
    args = sys.argv[1:]

    if args and args[0] == "--day":
        token = args[1].lower()
        weekday = WEEKDAY_ALIASES.get(token,
                                      int(token) if token.isdigit() else 5)
        months = args[2:] if args[2:] else current_and_next_month()
        print(f"앵봉산캠핑장 {WEEKDAY_LABELS[weekday]}요일 빈자리 조회")
        for ym in months:
            check_month(ym, weekday)
        return

    # 기본: 알림 규칙과 동일하게 토요일(전체 그룹, 이번+다음 달)
    #       + 일요일(데크만, 이번 달만) 조회
    months = current_and_next_month()
    print("앵봉산캠핑장 빈자리 조회 (토: 전체 / 일: 데크·이번달)")
    for ym in months:
        check_month(ym, 5)  # 토요일, 전체 그룹
    check_month(months[0], 6, groups=["0001"])  # 일요일, 데크만, 이번 달만


if __name__ == "__main__":
    main()
