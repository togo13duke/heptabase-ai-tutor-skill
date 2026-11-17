#!/usr/bin/env python3
"""学習記録の自己点検。

使い方:
    python3 check_course.py learning/<course-slug> [...] [--strict]
    python3 check_course.py learning            # PROGRESS.md を持つ全コース

検査すること:
  - SYLLABUS / PROGRESS / SHORTCUTS / FLASHCARDS の値が決められた語か
  - フェーズが awaiting_answer なのに回答待ちの質問が書かれていない、などの食い違い
  - 相対リンク切れ、SOURCES.md にない資料ID、SHORTCUTS.md にない近道ID
  - 回収時期を過ぎた未回収の近道、根拠の見当たらない demonstrated
  - 出題範囲との対応表で、対応も「対象外」もない出題項目、存在しない対応先
  - PART の確認問題の後に答えが書かれていないか
  - チューターが書いた教材に「明らかに」「自明」などを使っていないか

「エラー」は直す。「注意」は一つずつ確認し、理由があれば残してよい。
エラーがあれば終了コード 1。--strict では注意も 1 にする。標準ライブラリのみ。
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

PROGRESS_VALUES = ("not_started", "in_progress", "covered")
UNDERSTANDING_VALUES = ("unverified", "developing", "demonstrated")
PHASES = ("planning", "teaching", "awaiting_answer", "review", "wrap_up")
COURSE_STATES = ("draft", "active", "paused", "finished")
SHORTCUT_STATES = ("open", "paid")
CARD_TYPES = ("定義", "プロセス・手順", "よくある間違い")
EXAM_DEPTHS = ("深く", "標準", "要点のみ", "対象外")

# 学習者を怖がらせる言葉。引用符の中は、その言葉について話しているとみなして除外する。
# 「明らかにする／なる」は「解明する」の意味なので対象外。
BANNED_ERROR = [
    ("明らかに", r"明らか(?:です|だ|な(?![っるりら])|に(?![すしなさ]))"),
    ("自明", r"自明"),
    ("言うまでもなく", r"言うまでもな"),
    ("周知のとおり", r"周知の(?:とおり|通り|ように|事実)"),
    ("読者の演習", r"読者の演習"),
]
BANNED_WARN = [
    ("当然", r"当然(?:ですが|ながら|のように|わかる|分かる)"),
    ("容易に分かる", r"容易に(?:わか|分か|理解|示せ|導け)"),
    ("簡単に分かる", r"(?:簡単|すぐ)に?(?:わか|分か)(?:る|ります)"),
    ("誰でも分かる", r"誰でも(?:わか|分か|でき)"),
]

ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
FIELD_RE = re.compile(r"^\s*[-*]\s*([^：:|]{1,20})[：:]\s*(.*)$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
SOURCE_REF_RE = re.compile(r"\[(?!SC\d)([A-Z]{1,3}\d+)\s*[:：]")
SHORTCUT_REF_RE = re.compile(r"(?<![A-Za-z0-9])SC\d+(?!\d)")
SYLLABUS_ID_RE = re.compile(r"(?<![A-Za-z0-9])T\d+(?:\.P\d+)?(?![\d.])")
QUOTED_RE = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"")
PLACEHOLDER_RE = re.compile(r"<[^<>\n]*[^\x00-\x7f][^<>\n]*>")
TIMEZONE_RE = re.compile(r"(?:[+-]\d\d:?\d\d|Z|JST|UTC|GMT)\b|\b(?:JST|UTC)")
ANSWER_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:正解|正答|解答例|模範解答|想定解|想定解答|期待する答え|期待される答え|答え)(?:\*\*)?\s*[:：]")
YES_NO_RE = re.compile(r"^\s*(?:はい|いいえ|yes|no)\b", re.IGNORECASE)


class Report:
    def __init__(self, course):
        self.course = course
        self.items = []

    def add(self, level, path, line, msg):
        rel = path.relative_to(self.course) if path else None
        self.items.append((level, str(rel) if rel else "", line, msg))

    def error(self, path, line, msg):
        self.add("エラー", path, line, msg)

    def warn(self, path, line, msg):
        self.add("注意", path, line, msg)


def strip_code(lines, keep_inline=False):
    """コード・数式を除外する。構造の検査ではインラインコードの中身を残す。"""
    out, fence = [], None
    for line in lines:
        s = line.lstrip()
        if fence:
            if s.startswith(fence):
                fence = None
            out.append("")
            continue
        if s.startswith("```") or s.startswith("~~~"):
            fence = s[:3]
            out.append("")
            continue
        line = re.sub(r"`([^`]*)`", r"\1" if keep_inline else " ", line)
        line = re.sub(r"\$\$.*?\$\$", " ", line)
        line = re.sub(r"(?<!\\)\$[^$\n]+\$", " ", line)
        out.append(line)
    return out


def read(path, keep_inline=False):
    return strip_code(path.read_text(encoding="utf-8").splitlines(), keep_inline)


def split_cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def tables(lines):
    """(見出しのセル, [(行番号, セル)]) を順に返す。"""
    i = 0
    while i < len(lines) - 1:
        if ROW_RE.match(lines[i]) and SEP_RE.match(lines[i + 1]):
            header = split_cells(lines[i])
            rows, j = [], i + 2
            while j < len(lines) and ROW_RE.match(lines[j]):
                rows.append((j + 1, split_cells(lines[j])))
                j += 1
            yield header, rows
            i = j
        else:
            i += 1


def column(header, *keywords):
    for i, h in enumerate(header):
        if any(k in h for k in keywords):
            return i
    return None


def cell(cells, i):
    return cells[i] if i is not None and i < len(cells) else ""


def enum_value(text, allowed):
    """セルから決められた語を一つ取り出す。(値, 問題) を返す。"""
    found = []
    for token in re.findall(r"[a-z_]+", text):
        if token in allowed and token not in found:
            found.append(token)
    if not found:
        return None, f"値が {' / '.join(allowed)} のどれでもない：「{text[:30]}」"
    if len(found) > 1:
        return None, f"値が一つに決まっていない：「{text[:30]}」"
    return found[0], None


def fields(lines):
    """トップレベルの項目と続きの行を読む。問題内のコードは項目と解釈しない。"""
    out = {}
    current, fence = None, None
    for n, line in enumerate(lines, 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        in_code = fence is not None or marker is not None
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            fence = marker[1]
        m = FIELD_RE.match(line) if not in_code and line == line.lstrip() else None
        if m:
            key = m.group(1).strip()
            current = key if key not in out else None
            if current:
                out[current] = (n, m.group(2).strip())
        elif not in_code and line.startswith("#"):
            current = None
        elif current and line.strip():
            start, value = out[current]
            out[current] = (start, (value + "\n" + line).strip())
    return out


def is_none(value):
    value = value.strip().strip("`").strip()
    return not value or re.match(r"^(?:なし|無し|ない|none|-|ー)(?:[。、.\s]|$)", value, re.I)


def outside_quotes(line, start):
    return not any(a <= start < b for a, b in (m.span() for m in QUOTED_RE.finditer(line)))


def check_syllabus(course, rep):
    path = course / "SYLLABUS.md"
    rows = {}
    if not path.exists():
        return rows
    lines = read(path, keep_inline=True)
    for n, line in enumerate(lines, 1):
        m = re.match(r"^\s*状態\s*[：:]\s*(.*)$", line)
        if m:
            _, problem = enum_value(m.group(1), ("draft", "active", "finished"))
            if problem:
                rep.error(path, n, "シラバスの状態の" + problem)
    for header, body in tables(lines):
        id_col = column(header, "ID")
        prog_col = column(header, "進行")
        und_col = column(header, "理解")
        if id_col is None or prog_col is None or und_col is None:
            continue
        for n, cells in body:
            sid = cell(cells, id_col)
            if not sid:
                continue
            if sid in rows:
                rep.error(path, n, f"ID {sid} が重複している")
            prog, problem = enum_value(cell(cells, prog_col), PROGRESS_VALUES)
            if problem:
                rep.error(path, n, f"{sid} の進行の" + problem)
            und_text = cell(cells, und_col)
            und, problem = enum_value(und_text, UNDERSTANDING_VALUES)
            if problem:
                rep.error(path, n, f"{sid} の理解の" + problem)
            rows[sid] = {"line": n, "progress": prog, "understanding": und,
                         "evidence": re.sub(r"[a-z_/\s]+", "", und_text)}
    check_exam_scope(path, lines, rows, rep)
    return rows


def check_exam_scope(path, lines, rows, rep):
    """出題範囲との対応表。すべての出題項目に対応か「対象外」があるか。"""
    for header, body in tables(lines):
        item_col = column(header, "出題項目")
        map_col = column(header, "対応")
        depth_col = column(header, "扱い")
        if item_col is None or map_col is None:
            continue
        for n, cells in body:
            item = cell(cells, item_col)
            if not item:
                continue
            mapped = cell(cells, map_col)
            depth = cell(cells, depth_col).strip("`")
            ids = SYLLABUS_ID_RE.findall(mapped)
            for sid in ids:
                if sid not in rows and not any(k.startswith(sid + ".") for k in rows):
                    rep.error(path, n, f"出題項目「{item}」の対応先 {sid} がシラバスの表にない")
            map_excluded = "対象外" in mapped
            depth_excluded = depth == "対象外"
            if not ids and not map_excluded and not depth_excluded:
                rep.warn(path, n, f"出題項目「{item}」に対応するTopic/Partも「対象外」もない")
            if depth_col is not None and depth not in EXAM_DEPTHS:
                rep.error(path, n, f"出題項目「{item}」の扱いが "
                          + " / ".join(EXAM_DEPTHS) + " のどれでもない")
            if ids and (map_excluded or depth_excluded):
                rep.warn(path, n, f"出題項目「{item}」は対象外なのに対応先が書かれている")
            elif depth_col is not None and map_excluded and depth in EXAM_DEPTHS and not depth_excluded:
                rep.warn(path, n, f"出題項目「{item}」は対応先が対象外なのに扱いが「{depth}」になっている")


def check_progress(course, rep, syllabus):
    path = course / "PROGRESS.md"
    if not path.exists():
        rep.error(None, None, "PROGRESS.md がない。次回どこから再開するか分からない")
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    f = fields(lines)

    state = None
    if "コース状態" in f:
        n, v = f["コース状態"]
        state, problem = enum_value(v, COURSE_STATES)
        if problem:
            rep.error(path, n, "コース状態の" + problem)
    else:
        rep.warn(path, None, "「コース状態」の行がない")

    phase = None
    if "フェーズ" in f:
        n, v = f["フェーズ"]
        phase, problem = enum_value(v, PHASES)
        if problem:
            rep.error(path, n, "フェーズの" + problem)
    else:
        rep.warn(path, None, "「フェーズ」の行がない")

    waiting = f.get("回答待ち")
    if waiting is None:
        rep.warn(path, None, "「回答待ち」の行がない。待っている問題がなければ「なし」と書く")
    else:
        n, v = waiting
        if phase == "awaiting_answer" and is_none(v):
            rep.error(path, n, "フェーズが awaiting_answer なのに回答待ちの質問が書かれていない")
        if phase and phase != "awaiting_answer" and not is_none(v):
            rep.warn(path, n, f"回答待ちの質問があるのにフェーズが {phase} になっている")

    nxt = f.get("次の一手")
    if nxt is None or is_none(nxt[1]):
        rep.error(path, nxt[0] if nxt else None, "「次の一手」が空。再開時の最初の行動を書く")

    if "更新日時" in f:
        n, v = f["更新日時"]
        if not TIMEZONE_RE.search(v):
            rep.warn(path, n, "更新日時にタイムゾーンがない")
    else:
        rep.warn(path, None, "「更新日時」の行がない")

    if syllabus and "現在" in f:
        n, v = f["現在"]
        for sid in SYLLABUS_ID_RE.findall(v):
            if sid not in syllabus and not any(k.startswith(sid + ".") for k in syllabus):
                rep.error(path, n, f"現在位置の {sid} が SYLLABUS.md にない")
    return state


def check_shortcuts(course, rep, syllabus, state):
    path = course / "SHORTCUTS.md"
    ids, open_ids = set(), []
    if not path.exists():
        return ids
    for header, body in tables(read(path, keep_inline=True)):
        id_col = column(header, "ID")
        state_col = column(header, "状態")
        when_col = column(header, "回収", "返す")
        if id_col is None or state_col is None:
            continue
        for n, cells in body:
            sid = cell(cells, id_col)
            if not sid:
                continue
            ids.add(sid)
            value, problem = enum_value(cell(cells, state_col), SHORTCUT_STATES)
            if problem:
                rep.error(path, n, f"{sid} の状態の" + problem)
            if value != "open":
                continue
            open_ids.append(sid)
            for target in SYLLABUS_ID_RE.findall(cell(cells, when_col)):
                row = syllabus.get(target)
                if row and row["progress"] == "covered":
                    rep.warn(path, n, f"{sid} は回収予定の {target} を扱い終えたのに open のまま")
    passport = course / "PASSPORT.md"
    if state == "finished" and open_ids:
        text = passport.read_text(encoding="utf-8") if passport.exists() else ""
        missing = [s for s in open_ids if s not in text]
        if missing:
            rep.warn(path, None, "コース終了時に未回収の近道 " + "、".join(missing)
                     + " が PASSPORT.md の「まだ省いていること」に書かれていない")
    return ids


def source_ids(course):
    path = course / "SOURCES.md"
    if not path.exists():
        return None
    ids = set()
    for header, body in tables(read(path, keep_inline=True)):
        id_col = column(header, "ID")
        if id_col is None:
            continue
        ids.update(cell(cells, id_col) for _, cells in body if cell(cells, id_col))
    return ids


def tutor_authored(course, path):
    rel = path.relative_to(course).as_posix()
    name = path.name
    if rel.startswith("sources/"):
        return False
    return (rel.startswith("sessions/") and name in ("PLAN.md", "SUMMARY.md")
            or name.startswith("PART-")
            or rel.startswith("notes/")
            or name in ("PASSPORT.md", "FLASHCARDS.md"))


def check_files(course, rep, sources, shortcuts, syllabus):
    reviews = ""
    for path in sorted(course.rglob("*.md")):
        rel = path.relative_to(course).as_posix()
        if rel.startswith("sources/"):
            continue
        lines = read(path)
        if path.name == "REVIEW.md":
            reviews += "\n".join(lines)
        authored = tutor_authored(course, path)

        for n, line in enumerate(lines, 1):
            for m in LINK_RE.finditer(line):
                target = m.group(1).strip().split()[0].strip("<>")
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith("#"):
                    continue
                target = unquote(target.split("#")[0].split("?")[0])
                if target and not (path.parent / target).exists():
                    rep.error(path, n, f"リンク先がない：{target}")

            if path.name != "SOURCES.md":
                for sid in SOURCE_REF_RE.findall(line):
                    if sources is None:
                        rep.error(path, n, f"資料 {sid} を参照しているが SOURCES.md がない")
                    elif sid not in sources:
                        rep.error(path, n, f"資料 {sid} が SOURCES.md にない")

            if path.name != "SHORTCUTS.md":
                for sid in SHORTCUT_REF_RE.findall(line):
                    if sid not in shortcuts:
                        rep.error(path, n, f"近道 {sid} が SHORTCUTS.md にない")

            for m in PLACEHOLDER_RE.finditer(line):
                rep.warn(path, n, f"テンプレートの山括弧が残っている：{m.group(0)[:20]}")

            if authored:
                for level, banned in (("error", BANNED_ERROR), ("warn", BANNED_WARN)):
                    for word, pattern in banned:
                        for m in re.finditer(pattern, line):
                            if outside_quotes(line, m.start()):
                                msg = f"「{word}」は使わない。その一歩を書く"
                                getattr(rep, level)(path, n, msg)

        if path.name.startswith("PART-"):
            start = next((i for i, l in enumerate(lines) if "確認問題" in l), None)
            if start is not None:
                for i in range(start + 1, len(lines)):
                    if ANSWER_RE.match(lines[i]):
                        rep.error(path, i + 1, "確認問題の答えが PART に書かれている。答えは回答後に REVIEW.md へ")
                        break

        if path.name == "FLASHCARDS.md":
            check_flashcards(path, read(path, keep_inline=True), rep)

    for sid, row in syllabus.items():
        if row["understanding"] == "demonstrated" and sid not in reviews and not row["evidence"]:
            rep.warn(course / "SYLLABUS.md", row["line"],
                     f"{sid} は demonstrated だが、REVIEW.md にも理解の列にも根拠が見当たらない")


def check_flashcards(path, lines, rep):
    counts = {}
    total = 0
    for header, body in tables(lines):
        type_col = column(header, "分類")
        answer_col = column(header, "裏", "答え")
        if type_col is None:
            continue
        for n, cells in body:
            kind = cell(cells, type_col)
            if not kind:
                continue
            total += 1
            if kind not in CARD_TYPES:
                rep.error(path, n, f"分類「{kind}」は {' / '.join(CARD_TYPES)} のどれでもない")
            counts[kind] = counts.get(kind, 0) + 1
            if YES_NO_RE.match(cell(cells, answer_col)):
                rep.warn(path, n, "はい／いいえで答えられるカードになっている")
    if total >= 6:
        kind, count = max(counts.items(), key=lambda kv: kv[1])
        if count / total > 0.6:
            rep.warn(path, None, f"分類が「{kind}」に偏っている（{count}/{total} 枚）")


def check_course(course):
    rep = Report(course)
    syllabus = check_syllabus(course, rep)
    state = check_progress(course, rep, syllabus)
    shortcuts = check_shortcuts(course, rep, syllabus, state)
    sources = source_ids(course)
    check_files(course, rep, sources, shortcuts, syllabus)
    if state == "finished":
        if not (course / "PASSPORT.md").exists():
            rep.warn(None, None, "コースは finished だが PASSPORT.md がない")
        for sid, row in syllabus.items():
            if row["progress"] != "covered" and row["understanding"] != "demonstrated":
                rep.warn(course / "SYLLABUS.md", row["line"],
                         f"コースは finished だが {sid} は {row['progress']}")
    return rep.items


def find_courses(paths):
    courses = []
    for p in paths:
        p = Path(p)
        if not p.is_dir():
            raise ValueError(f"フォルダではない：{p}")
        if (p / "PROGRESS.md").exists() or (p / "SYLLABUS.md").exists():
            courses.append(p)
            continue
        found = sorted(d for d in p.iterdir()
                       if d.is_dir() and ((d / "PROGRESS.md").exists() or (d / "SYLLABUS.md").exists()))
        if found:
            courses.extend(found)
        else:
            courses.append(p)
    return courses


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="コースまたは learning フォルダ")
    parser.add_argument("--strict", action="store_true", help="注意も終了コード1にする")
    args = parser.parse_args(argv[1:])
    errors = warnings = 0
    try:
        courses = find_courses(args.paths)
    except ValueError as exc:
        parser.error(str(exc))
    for course in courses:
        items = check_course(course)
        print(f"■ {course}")
        for level, rel, line, msg in items:
            where = rel + (f":{line}" if line else "")
            print(f"  {level}  {where + '  ' if where else ''}{msg}")
            if level == "エラー":
                errors += 1
            else:
                warnings += 1
        if not items:
            print("  問題なし")
    print(f"\n{len(courses)} コース、エラー {errors} 件、注意 {warnings} 件。")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
