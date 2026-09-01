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

import os
import sys
import urllib.parse
import urllib.request

import check_saturday as cs

WEEKDAY_ALIASES = cs.WEEKDAY_ALIASES


def _collect(weekday: int) -> list[str]:
    """잔여 있는 날의 요약 라인만 수집(콘솔 출력 없이)."""
    lines: list[str] = []
    for ym in cs.current_and_next_month():
        remain = cs.get_play_dates(ym)
        for day in cs.weekdays_in(ym, weekday):
            total = remain.get(day)
            if not total:
                continue
            parts = []
            for code, name in cs.PRODUCT_GROUPS.items():
                sites = cs.get_available_sites(code, day)
                if sites:
                    names = ", ".join(s["product_name"] for s in sites)
                    parts.append(f"{name} {len(sites)}자리 [{names}]")
                else:
                    parts.append(f"{name} 0")
            wl = cs.WEEKDAY_LABELS[weekday]
            lines.append(f"• {cs.fmt(day)} ({wl}) 잔여 {total} → " + " / ".join(parts))
    return lines


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

    token_arg = os.environ.get("WEEKDAY", "sat").lower()
    weekday = WEEKDAY_ALIASES.get(
        token_arg, int(token_arg) if token_arg.isdigit() else 5)

    lines = _collect(weekday)
    wl = cs.WEEKDAY_LABELS[weekday]

    if lines:
        msg = (f"🏕️ 앵봉산캠핑장 {wl}요일 빈자리\n\n" + "\n".join(lines)
               + f"\n\n예약: {cs.BASE}/web/main?shopEncode={cs.SHOP_ENCODE}")
        _send_telegram(token, chat_id, msg)
        print(f"sent {len(lines)} line(s)")
    elif os.environ.get("NOTIFY_ALWAYS") == "1":
        _send_telegram(token, chat_id, f"앵봉산캠핑장 {wl}요일: 잔여 있는 날 없음")
        print("sent (none)")
    else:
        print("no availability; nothing sent")


if __name__ == "__main__":
    main()
