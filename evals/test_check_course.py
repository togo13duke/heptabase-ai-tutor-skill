"""check_course.py の回帰テスト。

    python3 -I -m unittest discover -s evals -p 'test_*.py'
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures" / "check-course"
spec = importlib.util.spec_from_file_location(
    "check_course", ROOT / "skills" / "heptabase-ai-tutor" / "scripts" / "check_course.py")
check_course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_course)


def run(course):
    return check_course.check_course(FIXTURES / course)


class GoodCourse(unittest.TestCase):
    def test_model_course_is_clean(self):
        self.assertEqual(run("good/stats-reading"), [])


class MarkdownAndCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.course = Path(self.temp.name) / "course"
        shutil.copytree(FIXTURES / "good/stats-reading", self.course)

    def write_progress(self, question, phase="awaiting_answer"):
        (self.course / "PROGRESS.md").write_text(
            "# 再開ポイント\n"
            "- 更新日時：2026-11-09T09:00:00+09:00\n"
            "- コース状態：`active`\n"
            "- 現在：Session 002 / `T1.P2`\n"
            f"- フェーズ：`{phase}`\n"
            f"- 回答待ち：{question}\n"
            "- 次の一手：回答を評価する\n", encoding="utf-8")

    def test_inline_code_in_structured_records(self):
        self.write_progress("`print(2 + 3)` の結果と理由は？")
        for name, tokens in (
                ("SYLLABUS.md", ("finished", "T1.P1", "T1.P2", "covered", "demonstrated", "developing")),
                ("SHORTCUTS.md", ("SC1", "SC2", "paid", "open")),
                ("SOURCES.md", ("S1",))):
            path = self.course / name
            text = path.read_text(encoding="utf-8")
            for token in tokens:
                text = text.replace(token, f"`{token}`")
            path.write_text(text, encoding="utf-8")
        self.assertEqual(check_course.check_course(self.course), [])

    def test_invalid_inline_state_is_not_ignored(self):
        self.write_progress("出力は？", phase="invalid")
        items = check_course.check_course(self.course)
        self.assertTrue(any("invalid" in i[3] and i[0] == "エラー" for i in items), items)

    def test_multiline_question_with_code_and_choices(self):
        self.write_progress("\n  次の出力と理由は？\n\n"
                            "  ```python\n  print(2 + 3)\n  ```\n"
                            "  - A: 5\n  - B: 23")
        self.assertEqual(check_course.check_course(self.course), [])

    def test_field_like_text_in_code_does_not_override_progress(self):
        self.write_progress("\n  このMarkdownを表示するコードを書いてください。\n"
                            "```text\n- 次の一手：なし\n```\n")
        self.assertEqual(check_course.check_course(self.course), [])

    def test_missing_question_is_still_an_error(self):
        for question in ("", "`なし`"):
            with self.subTest(question=question):
                self.write_progress(question)
                items = check_course.check_course(self.course)
                self.assertTrue(any("回答待ちの質問が書かれていない" in i[3]
                                    and i[0] == "エラー" for i in items), items)

    def test_prose_checks_still_ignore_code_examples(self):
        part = self.course / "sessions/002/PART-01.md"
        with part.open("a", encoding="utf-8") as f:
            f.write('\n`自明` は避ける言葉です。\n```text\n明らかに\n'
                    '[S99: 例](missing.md)\n正解：テスト用の文字列\n```\n')
        self.assertEqual(check_course.check_course(self.course), [])

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(Path(check_course.__file__)), *map(str, args)],
            capture_output=True, text=True)

    def test_missing_path_cannot_succeed(self):
        for paths in ((self.course / "missing",), (self.course, self.course / "missing")):
            with self.subTest(paths=paths):
                result = self.cli(*paths)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("フォルダではない", result.stderr)

    def test_unknown_option_is_rejected(self):
        result = self.cli(self.course, "--strcit")
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_cli_clean_course_and_strict_warning(self):
        self.assertEqual(self.cli(self.course, "--strict").returncode, 0)
        path = self.course / "PROGRESS.md"
        path.write_text(path.read_text(encoding="utf-8").replace("+09:00", ""), encoding="utf-8")
        self.assertEqual(self.cli(self.course).returncode, 0)
        self.assertEqual(self.cli(self.course, "--strict").returncode, 1)


class BrokenCourse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = run("broken/broken-course")

    def assertFound(self, level, file, line, fragment):
        hits = [i for i in self.items
                if i[0] == level and i[1] == file and i[2] == line and fragment in i[3]]
        self.assertTrue(hits, f"見つからない：{level} {file}:{line} {fragment}\n{self.items}")

    def test_syllabus(self):
        self.assertFound("エラー", "SYLLABUS.md", 6, "T1.P2 の進行の値が")
        self.assertFound("エラー", "SYLLABUS.md", 7, "ID T1.P2 が重複")
        self.assertFound("注意", "SYLLABUS.md", 8, "T1.P3 は demonstrated だが")

    def test_progress(self):
        self.assertFound("注意", "PROGRESS.md", 2, "タイムゾーン")
        self.assertFound("エラー", "PROGRESS.md", 4, "T9.P1 が SYLLABUS.md にない")
        self.assertFound("エラー", "PROGRESS.md", 6, "回答待ちの質問が書かれていない")

    def test_part(self):
        part = "sessions/001/PART-01.md"
        self.assertFound("エラー", part, 2, "「明らかに」は使わない")
        self.assertFound("エラー", part, 4, "資料 S2 が SOURCES.md にない")
        self.assertFound("エラー", part, 4, "リンク先がない：missing.pdf")
        self.assertFound("エラー", part, 5, "近道 SC9 が SHORTCUTS.md にない")
        self.assertFound("エラー", part, 6, "リンク先がない：../000/SUMMARY.md")
        self.assertFound("エラー", part, 11, "確認問題の答え")

    def test_banned_word_false_positives(self):
        part = "sessions/001/PART-01.md"
        line2 = [i for i in self.items if i[1] == part and i[2] == 2 and "明らか" in i[3]]
        self.assertEqual(len(line2), 1, "「明らかにする」まで拾っている")
        self.assertFalse([i for i in self.items if i[1] == part and i[2] == 3],
                         "引用符の中の言葉を拾っている")

    def test_shortcuts_and_cards(self):
        self.assertFound("注意", "SHORTCUTS.md", 4, "SC1 は回収予定の T1.P1 を扱い終えたのに open")
        self.assertFound("エラー", "FLASHCARDS.md", 4, "分類「用語」")
        self.assertFound("注意", "FLASHCARDS.md", 5, "はい／いいえ")
        self.assertFound("注意", "GOAL.md", 1, "山括弧")

    def test_no_unexpected_errors(self):
        errors = [i for i in self.items if i[0] == "エラー"]
        self.assertEqual(len(errors), 11, errors)


if __name__ == "__main__":
    unittest.main()
