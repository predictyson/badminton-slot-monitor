import unittest

from monitor import parse_target_rows


def page(button_1: str = "접수 종료", button_2: str = "접수 종료") -> str:
    return f"""
    <html><body><table><tbody>
      <tr>
        <td data-title="번호">1</td>
        <td data-title="신청">
          <a class="btn_ty_c4 btn_sm end" href="#one">{button_1}</a>
        </td>
      </tr>
      <tr>
        <td data-title="번호">2</td>
        <td data-title="신청">
          <a class="btn_ty_c4 btn_sm end" href="#two">{button_2}</a>
        </td>
      </tr>
    </tbody></table></body></html>
    """


class ParseTargetRowsTest(unittest.TestCase):
    def test_closed_rows_are_not_open(self) -> None:
        result = parse_target_rows(page())

        self.assertFalse(result["1"].is_open)
        self.assertFalse(result["2"].is_open)

    def test_spacing_in_closed_text_is_ignored(self) -> None:
        result = parse_target_rows(page(button_1="  접수   종료 "))

        self.assertFalse(result["1"].is_open)

    def test_changed_button_is_open(self) -> None:
        result = parse_target_rows(page(button_2="접수 가능"))

        self.assertFalse(result["1"].is_open)
        self.assertTrue(result["2"].is_open)
        self.assertEqual("#two", result["2"].href)

    def test_missing_target_fails_closed(self) -> None:
        html = page().replace('<td data-title="번호">2</td>', '<td data-title="번호">3</td>')

        with self.assertRaisesRegex(ValueError, "대상 번호"):
            parse_target_rows(html)

    def test_missing_application_link_fails_closed(self) -> None:
        html = page().replace(
            '<a class="btn_ty_c4 btn_sm end" href="#one">접수 종료</a>',
            "접수 종료",
        )

        with self.assertRaisesRegex(ValueError, "신청 a 태그"):
            parse_target_rows(html)


if __name__ == "__main__":
    unittest.main()
