"""워크트리의 학습 기록을 기본 작업 폴더에 통합한다.

--all: 빠진 결정만 가져온다. 기존 기록과 진행 중인 코드에는 손대지 않는다.
--records L001 L002: 현재 워크트리의 지정된 결정과 검증 결과를 갱신한다.
번호가 겹쳐도 날짜·제목으로 결정을 구분하고 본문의 기록 참조를 변환한다.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


HEADING = re.compile(
    r"^## (L\d+) · ([^·\r\n]+) · ([^\r\n]+)\r?\n(.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
REFERENCE = re.compile(r"(?<![0-9A-Za-z_])L\d+(?![0-9A-Za-z_])")


@dataclass(frozen=True)
class Record:
    number: str
    date: str
    title: str
    body: str

    @property
    def key(self) -> tuple[str, str]:
        return self.date, self.title


def parse_document(text: str) -> tuple[str, list[Record]]:
    matches = list(HEADING.finditer(text))
    records = [Record(m[1], m[2].strip(), m[3].strip(), m[4].strip()) for m in matches]
    if len({r.number for r in records}) != len(records):
        raise ValueError("기록 번호가 중복됩니다. 통합 전에 원본을 확인하세요.")
    if len({r.key for r in records}) != len(records):
        raise ValueError("같은 날짜·제목의 결정이 중복됩니다. 원본을 확인하세요.")
    prefix = text[: matches[0].start()] if matches else text
    return prefix.rstrip(), records


def render_document(prefix: str, records: list[Record]) -> str:
    parts = [prefix.rstrip()]
    parts.extend(f"## {r.number} · {r.date} · {r.title}\n\n{r.body}" for r in records)
    return "\n\n".join(parts).rstrip() + "\n"


def render_changes(original: str, records: list[Record]) -> str:
    """기록 바깥의 인터뷰 메모·양식·소제목도 원래 위치에 보존한다."""
    replacements = {r.key: r for r in records}
    existing_keys = set()
    parts = []
    cursor = 0
    for match in HEADING.finditer(original):
        before = Record(match[1], match[2].strip(), match[3].strip(), match[4].strip())
        existing_keys.add(before.key)
        after = replacements[before.key]
        parts.append(original[cursor : match.start()])
        parts.append(match[0] if before == after else render_document("", [after]).lstrip() + "\n")
        cursor = match.end()
    parts.append(original[cursor:])
    result = "".join(parts)
    new_records = [r for r in records if r.key not in existing_keys]
    if new_records:
        result = result.rstrip() + "\n\n" + render_document("", new_records).lstrip()
    return result


def combine(
    target: str, sources: list[tuple[str, str]], selected: set[str] | None = None
) -> tuple[str, int, int]:
    """기본 동작은 추가만 한다. 지정한 번호만 기존 기록 갱신을 허용한다."""
    _, records = parse_document(target)
    indices = {r.key: i for i, r in enumerate(records)}
    next_number = max((int(r.number[1:]) for r in records), default=0) + 1
    added = updated = 0
    for source_name, source_text in sources:
        _, incoming = parse_document(source_text)
        chosen = [r for r in incoming if selected is None or r.number in selected]
        if selected is not None and selected - {r.number for r in incoming}:
            raise ValueError("요청한 기록 번호가 원본에 없습니다.")
        mapping = {r.number: records[indices[r.key]].number for r in incoming if r.key in indices}
        for r in chosen:
            if r.key not in indices:
                mapping[r.number] = f"L{next_number:03d}"
                next_number += 1
        for r in chosen:
            if r.key in indices and selected is None:
                continue
            references = set(REFERENCE.findall(r.body))
            unresolved = references & ({item.number for item in incoming} - mapping.keys())
            if unresolved:
                raise ValueError("참조한 이전 결정이 기본 폴더에 없습니다. 먼저 --all로 통합하세요.")
            # 범위 표현(L042~L044)도 양 끝의 참조를 각각 변환한다.
            body = REFERENCE.sub(lambda m: mapping.get(m[0], m[0]), r.body)
            body = re.sub(r"\n- \*\*기록 출처:\*\*[^\n]*", "", body)
            body += f"\n- **기록 출처:** 워크트리 `{source_name}`, 원래 기록 `{r.number}`."
            converted = Record(mapping[r.number], r.date, r.title, body)
            if r.key in indices:
                index = indices[r.key]
                if records[index] != converted:
                    records[index] = converted
                    updated += 1
            else:
                indices[r.key] = len(records)
                records.append(converted)
                added += 1
    # 변경이 없으면 줄바꿈을 포함한 원본을 그대로 유지한다.
    return (render_changes(target, records) if added or updated else target), added, updated


@contextmanager
def writer_lock(path: Path):
    """동시에 실행된 기록 통합을 직렬로 처리한다. 종료 시 OS 잠금이 풀린다."""
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + 20
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("다른 통합 작업이 끝나지 않았습니다. 다시 실행하세요.")
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="등록된 모든 워크트리의 누락 기록 추가")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="기록 원본 워크트리")
    parser.add_argument("--records", nargs="+", help="갱신할 원본 기록 번호")
    args = parser.parse_args()
    if args.all and args.records:
        parser.error("--all과 --records를 동시에 사용할 수 없습니다.")
    common = Path(git_output("rev-parse", "--path-format=absolute", "--git-common-dir"))
    primary = common.parent
    roots = [Path(line[9:]).resolve() for line in git_output("worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
    source_root = args.source.resolve()
    if source_root not in roots:
        parser.error("등록된 프로젝트 워크트리만 원본으로 사용할 수 있습니다.")
    chosen_roots = roots if args.all else [source_root]
    # 기본 폴더는 대상이다. 자신의 본문을 다시 가져오지 않는다.
    chosen_roots = [r for r in chosen_roots if r != primary]
    target_path = primary / "Learning.md"
    with writer_lock(common / "learning-sync.lock"):
        for attempt in range(3):
            before = target_path.read_text(encoding="utf-8-sig")
            sources = [(r.parent.name, (r / "Learning.md").read_text(encoding="utf-8-sig")) for r in chosen_roots if (r / "Learning.md").is_file()]
            result, added, updated = combine(before, sources, set(args.records) if args.records else None)
            if result == before:
                print("추가 0건 / 갱신 0건: " + str(target_path))
                return
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=primary, prefix=".Learning-sync-", suffix=".tmp", delete=False) as stream:
                    temporary_path = Path(stream.name)
                    stream.write(result)
                if target_path.read_text(encoding="utf-8-sig") != before:
                    continue
                os.replace(temporary_path, target_path)
                temporary_path = None
                print(f"추가 {added}건 / 갱신 {updated}건: {target_path}")
                return
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        raise RuntimeError("기본 기록이 계속 변경되고 있습니다. 다른 변경을 덮어쓰지 않았으니 다시 실행하세요.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
