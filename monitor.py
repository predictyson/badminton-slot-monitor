from __future__ import annotations

import json
import os
import ssl
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_API_URL = "https://yssports.yong-san.or.kr/rest/lecture/list"
DEFAULT_TARGET_CLASS_CODES = ("00146", "00147")
EXCLUDED_STATUS_TEXTS = {"접수종료", "준비중"}


@dataclass(frozen=True)
class RowStatus:
    number: str
    button_text: str
    href: str
    is_open: bool


def compact(value: str) -> str:
    return "".join(str(value).split())


def fetch_lecture_list(
    url: str,
    payload_data: dict[str, str],
    timeout_seconds: int = 20,
) -> list[dict]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }

    encoded_data = urlencode(payload_data).encode("utf-8")
    request = Request(url, data=encoded_data, headers=headers, method="POST")

    def read_with_context(context: ssl.SSLContext) -> bytes:
        with urlopen(request, timeout=timeout_seconds, context=context) as response:
            return response.read()

    try:
        body = read_with_context(ssl.create_default_context())
    except URLError as error:
        reason = getattr(error, "reason", error)
        is_cert_error = isinstance(reason, ssl.SSLCertVerificationError) or (
            "CERTIFICATE_VERIFY_FAILED" in str(reason).upper()
        )
        if not is_cert_error:
            raise
        body = read_with_context(ssl._create_unverified_context())

    response_json = json.loads(body.decode("utf-8", errors="replace"))

    if isinstance(response_json, list):
        return response_json
    if isinstance(response_json, dict):
        for key in ("list", "data", "rows", "lectureList"):
            if key in response_json and isinstance(response_json[key], list):
                return response_json[key]
    raise ValueError(f"예상치 못한 JSON 응답 구조입니다: {type(response_json)}")


def parse_lecture_statuses(
    lectures: list[dict], target_class_codes: tuple[str, ...]
) -> dict[str, RowStatus]:
    targets = set(target_class_codes)
    found: dict[str, RowStatus] = {}

    status_map = {
        "R": "접수중",
        "W": "접수대기",
        "F": "접수마감",
        "E": "접수종료",
        "P": "준비중",
    }

    for item in lectures:
        class_cd = str(item.get("class_cd", "")).strip()
        if class_cd not in targets:
            continue

        status_code = str(item.get("status", "")).upper()
        status_text = status_map.get(
            status_code,
            str(item.get("state_nm") or item.get("stat_nm") or "알수없음").strip(),
        )

        class_nm = str(item.get("class_nm", class_cd)).strip()
        compact_status = compact(status_text)

        # 상태 텍스트가 '접수종료' 또는 '준비중'이 아니면 오픈(True) 상태로 판단
        is_open = compact_status not in EXCLUDED_STATUS_TEXTS

        found[class_cd] = RowStatus(
            number=class_cd,
            button_text=f"{class_nm} [{status_text}]",
            href=class_cd,
            is_open=is_open,
        )

    missing = targets.difference(found)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(
            f"대상 class_cd({missing_text})를 API 응답에서 찾지 못했습니다."
        )

    return found


def write_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return

    delimiter = "BADMINTON_MONITOR_OUTPUT"
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    api_url = os.getenv("TARGET_API_URL", DEFAULT_API_URL)
    target_class_codes = tuple(
        item.strip()
        for item in os.getenv("TARGET_CLASS_CODES", "00146,00147").split(",")
        if item.strip()
    )

    if not target_class_codes:
        print("TARGET_CLASS_CODES가 비어 있습니다.", file=sys.stderr)
        return 2

    payload = {
        "company_code": os.getenv("COMPANY_CODE", "YGSN01"),
        "mem_no": "",
        "search_type": "%",
        "category_cd": os.getenv("CATEGORY_CD", "1010010000"),
        "category_level": os.getenv("CATEGORY_LEVEL", "2"),
        "class_nm": "",
        "train_day": "",
        "page": "1",
        "page_size": "10",
    }

    try:
        lectures = fetch_lecture_list(api_url, payload)
        statuses = parse_lecture_statuses(lectures, target_class_codes)
    except (
        HTTPError,
        URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"모니터링 실패: {error}", file=sys.stderr)
        return 2

    ordered = [statuses[code] for code in target_class_codes if code in statuses]
    open_rows = [status for status in ordered if status.is_open]
    details = "\n".join(
        f"- 코드 {status.number}: {status.button_text}" for status in ordered
    )

    write_github_output("open_found", "true" if open_rows else "false")
    write_github_output("details", details)
    write_github_output(
        "target_url",
        os.getenv("TARGET_URL", "https://yssports.yong-san.or.kr/fmcs/2"),
    )

    print(
        json.dumps(
            {
                "open_found": bool(open_rows),
                "rows": [asdict(status) for status in ordered],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
