from __future__ import annotations

import json
import os
import ssl
import sys
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = (
    "https://yssports.yong-san.or.kr/fmcs/2?"
    "page=1&lecture_type=R&center=YGSN01&event=1010000000&"
    "class=1010010000&subject=%EC%9B%94%EC%88%98%EA%B8%88"
)
DEFAULT_TARGET_NUMBERS = ("1", "2")
CLOSED_TEXT_COMPACT = "접수종료"


@dataclass(frozen=True)
class RowStatus:
    number: str
    button_text: str
    href: str
    classes: tuple[str, ...]

    @property
    def is_open(self) -> bool:
        return compact(self.button_text) != CLOSED_TEXT_COMPACT


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    children: list["HtmlNode | str"] = field(default_factory=list)

    def descendants(self) -> Iterable["HtmlNode"]:
        for child in self.children:
            if isinstance(child, HtmlNode):
                yield child
                yield from child.descendants()

    def text(self) -> str:
        return "".join(
            child.text() if isinstance(child, HtmlNode) else child
            for child in self.children
        )


class HtmlTreeParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document", {})
        self.stack = [self.root]

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = HtmlNode(tag.lower(), {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = HtmlNode(tag.lower(), {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def normalize(value: str) -> str:
    return " ".join(value.split())


def compact(value: str) -> str:
    return "".join(value.split())


def parse_target_rows(
    html: str, target_numbers: Iterable[str] = DEFAULT_TARGET_NUMBERS
) -> dict[str, RowStatus]:
    targets = {str(number).strip() for number in target_numbers}
    parser = HtmlTreeParser()
    parser.feed(html)
    found: dict[str, RowStatus] = {}

    rows = (node for node in parser.root.descendants() if node.tag == "tr")
    for row in rows:
        row_nodes = list(row.descendants())
        number_cell = next(
            (node for node in row_nodes if node.attrs.get("data-title") == "번호"),
            None,
        )
        if not number_cell:
            continue

        number = normalize(number_cell.text())
        if number not in targets:
            continue

        application_cell = next(
            (node for node in row_nodes if node.attrs.get("data-title") == "신청"),
            None,
        )
        if application_cell and application_cell.tag == "a":
            link = application_cell
        elif application_cell:
            link = next(
                (node for node in application_cell.descendants() if node.tag == "a"),
                None,
            )
        else:
            link = None
        if not link:
            raise ValueError(f"{number}번 행에서 신청 a 태그를 찾지 못했습니다.")

        button_text = normalize(link.text())
        if not button_text:
            raise ValueError(f"{number}번 행의 신청 버튼 텍스트가 비어 있습니다.")

        found[number] = RowStatus(
            number=number,
            button_text=button_text,
            href=link.attrs.get("href", ""),
            classes=tuple(link.attrs.get("class", "").split()),
        )

    missing = targets.difference(found)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(
            f"대상 번호({missing_text})를 찾지 못했습니다. 페이지 구조나 응답을 확인하세요."
        )

    return found


def decode_body(body: bytes, charset: str | None) -> str:
    candidates = [charset, "utf-8", "cp949", "euc-kr"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def fetch_page(url: str, timeout_seconds: int = 20) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }

    request = Request(url, headers=headers)

    def read_with_context(context: ssl.SSLContext) -> str:
        with urlopen(
            request, timeout=timeout_seconds, context=context
        ) as response:
            body = response.read()
            return decode_body(body, response.headers.get_content_charset())

    try:
        return read_with_context(ssl.create_default_context())
    except URLError as error:
        reason = getattr(error, "reason", error)
        is_certificate_error = isinstance(reason, ssl.SSLCertVerificationError) or (
            "CERTIFICATE_VERIFY_FAILED" in str(reason).upper()
        )
        if not is_certificate_error:
            raise

        # 현재 대상 사이트의 인증서 체인이 일부 실행 환경에서 완전하지 않아
        # 읽기 전용 GET 요청에 한해서만 인증서 검증 없이 한 번 재시도합니다.
        return read_with_context(ssl._create_unverified_context())


def write_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return

    delimiter = "BADMINTON_MONITOR_OUTPUT"
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    url = os.getenv("TARGET_URL", DEFAULT_URL)
    target_numbers = tuple(
        item.strip()
        for item in os.getenv("TARGET_NUMBERS", "1,2").split(",")
        if item.strip()
    )

    if not target_numbers:
        print("TARGET_NUMBERS가 비어 있습니다.", file=sys.stderr)
        return 2

    try:
        html = fetch_page(url)
        statuses = parse_target_rows(html, target_numbers)
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        print(f"모니터링 실패: {error}", file=sys.stderr)
        return 2

    ordered = [statuses[number] for number in target_numbers]
    open_rows = [status for status in ordered if status.is_open]
    details = "\n".join(
        f"- {status.number}번: {status.button_text}"
        for status in ordered
    )

    write_github_output("open_found", "true" if open_rows else "false")
    write_github_output("details", details)
    write_github_output("target_url", url)

    print(
        json.dumps(
            {
                "open_found": bool(open_rows),
                "rows": [asdict(status) | {"is_open": status.is_open} for status in ordered],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
