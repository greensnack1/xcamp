#!/usr/bin/env python3
"""GitHub Actions용 앵봉산캠핑장 빈자리 텔레그램 알림.

check_saturday.py의 조회 로직을 재사용해 '잔여 있는 날'만 모아
텔레그램으로 전송한다. 잔여가 하나도 없으면 아무것도 보내지 않는다
(NOTIFY_ALWAYS=1 이면 없어도 '없음'을 보냄).

환경변수:
    TELEGRAM_BOT_TOKEN  (필수) BotFather 봇 토큰
    TELEGRAM_CHAT_ID    (필수) 알림 받을 chat_id
    WEEKDAY             (선택) 감시 요일. 화|tue|1 등. 기본 토요일(sat)
    NOTIFY_ALWAYS       (선택) '1'이면 잔여 없어도 전송

사용:
    TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... WEEKDAY=sat python3 notify_telegram.py
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import urllib.parse
import urllib.request

import check_saturday as cs

WEEKDAY_ALIASES = cs.WEEKDAY_ALIASES

# 매월 9일 13:00~23:59(KST)엔 '다음 달' 물량이 고향사랑기부제 우선예약(데크 2면)으로
# 잠깐 열린다. 일반 빈자리가 아니라 우선예약분이라 오탐 알림이 되므로,
# KST 기준 9일에는 다음 달을 조회 대상에서 빼고 '이번 달'만 조회한다.
# (GitHub Actions는 UTC로 돌아 KST로 변환해 판정)
SKIP_NEXTMONTH_DAY = 9
KST = _dt.timezone(_dt.timedelta(hours=9))


def _months_to_check(now_utc: _dt.datetime | None = None) -> list[str]:
    """조회할 월 목록. KST 기준 매월 9일이면 이번 달만, 그 외엔 이번 달+다음 달."""
    now_utc = now_utc or _dt.datetime.now(_dt.timezone.utc)
    months = cs.current_and_next_month()  # [이번달, 다음달]
    if now_utc.astimezone(KST).day == SKIP_NEXTMONTH_DAY:
        return months[:1]  # 다음 달 제외
    return months


def _collect(weekday: int, groups: list[str] | None = None,
             months: list[str] | None = None) -> list[str]:
    """실제 예약 가능한 사이트가 있는 날의 요약 라인만 수집(콘솔 출력 없이).

    groups: 조회/표시할 상품 그룹 코드 리스트(['0001']=데크, ['0002']=글램핑).
            None이면 전체 그룹(데크캠핑장+글램핑).
    months: 조회할 월(YYYYMM) 리스트. None이면 _months_to_check().

    주의: GetBookPlayDate의 book_remain_count(total)는 사전예약일 등에서
    실제 예약 가능 여부와 무관하게 0보다 클 수 있으므로, 대상 그룹의 실제
    예약 가능 사이트 수(status_code=='0' && select_yn=='1')의 합이 0보다
    큰 날만 알림 대상으로 삼는다.
    """
    group_items = [(c, n) for c, n in cs.PRODUCT_GROUPS.items()
                   if groups is None or c in groups]
    lines: list[str] = []
    for ym in (months if months is not None else _months_to_check()):
        remain = cs.get_play_dates(ym)
        for day in cs.weekdays_in(ym, weekday):
            total = remain.get(day)
            if not total:
                continue
            parts = []
            available = 0
            for code, name in group_items:
                sites = cs.get_available_sites(code, day)
                available += len(sites)
                parts.append(f"{name} {len(sites)}")
            # 대상 그룹에 실제 예약 가능 사이트가 하나도 없으면(사전예약일 등) 건너뜀
            if available == 0:
                continue
            wl = cs.WEEKDAY_LABELS[weekday]
            lines.append(f"• {cs.fmt(day)} ({wl}) → " + " / ".join(parts))
    return lines


def _collect_date(date: str, groups: list[str] | None = None) -> list[str]:
    """특정 날짜(YYYYMMDD)의 실제 예약 가능 사이트가 있으면 요약 라인 반환.

    요일 감시(_collect)와 달리 '그 날짜 하루'만 본다. groups=None이면 전체 그룹.
    해당 월의 GetBookPlayDate에 그 날이 없거나 실제 예약가능 사이트가 0이면 빈 리스트.
    """
    group_items = [(c, n) for c, n in cs.PRODUCT_GROUPS.items()
                   if groups is None or c in groups]
    remain = cs.get_play_dates(date[:6])  # YYYYMM
    if not remain.get(date):
        return []
    parts = []
    available = 0
    for code, name in group_items:
        sites = cs.get_available_sites(code, date)
        available += len(sites)
        parts.append(f"{name} {len(sites)}")
    if available == 0:
        return []
    d = _dt.datetime.strptime(date, "%Y%m%d")
    wl = cs.WEEKDAY_LABELS[d.weekday()]
    return [f"• {cs.fmt(date)} ({wl}) → " + " / ".join(parts)]


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as r:
        r.read()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다.",
              file=sys.stderr)
        raise SystemExit(1)

    # 감시 규칙:
    #   토요일 = 전체 그룹(데크+글램핑), 이번 달+다음 달  (항상 감시)
    #   일요일 = 데크(0001)만, 이번 달만  (env WATCH_SUNDAY_DECK=1 일 때만 감시)
    #     -> 일요일 데크 예약을 잡으면 끄고, 다시 필요하면 켜는 식으로 토글.
    #        코드 수정 없이 xticket.yml env / 수동실행 입력으로 on/off.
    DECK = "0001"
    watch_sunday = os.environ.get("WATCH_SUNDAY_DECK", "0") == "1"

    lines = _collect(5)  # 토요일: 항상
    if watch_sunday:
        lines += _collect(6, groups=[DECK], months=_months_to_check()[:1])

    # 특정 날짜 감시(하드코딩): 10월 9일(금) 데크+글램핑 빈자리도 함께 조회
    lines += _collect_date("20261009")

    header = ("🏕️ 앵봉산캠핑장 빈자리 (토: 전체 / 일: 데크·이번달 / 10.9)" if watch_sunday
              else "🏕️ 앵봉산캠핑장 빈자리 (토: 전체 / 10.9)")

    if lines:
        msg = (header + "\n\n" + "\n".join(lines)
               + f"\n\n예약: {cs.BASE}/web/main?shopEncode={cs.SHOP_ENCODE}")
        _send_telegram(token, chat_id, msg)
        print(f"sent {len(lines)} line(s)")
    elif os.environ.get("NOTIFY_ALWAYS") == "1":
        _send_telegram(token, chat_id, header + ": 잔여 있는 날 없음")
        print("sent (none)")
    else:
        print("no availability; nothing sent")


if __name__ == "__main__":
    main()
