"""파일명 규칙과 변경 이력. 이미지·영상 내용은 수정하지 않습니다."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

from filename_bench.common import file_hash


def tag_key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value)).casefold()


def make_name(original, tags):
    """확장자와 원래 이름을 보존합니다. 사용자가 검토할 수 있도록 잘라내지 않습니다."""
    if len(tags) != 5 or any(not isinstance(t, str) or not t.strip() for t in tags):
        raise ValueError("비어 있지 않은 태그 5개를 입력하세요.")
    tags = [unicodedata.normalize("NFC", t.strip()) for t in tags]
    if len({tag_key(t) for t in tags}) != 5:
        raise ValueError("중복 태그를 다른 표현으로 수정하세요.")
    if any(re.search(r'[<>:"/\\|?*\x00-\x1f_]', t) for t in tags):
        raise ValueError(
            "태그에 경로 문자, 밑줄 또는 Windows 금지 문자를 넣을 수 없습니다."
        )
    source = Path(original)
    name = source.stem + "__" + "_".join(tags) + source.suffix
    # macOS/Linux의 파일명 byte 제한과 Windows UTF-16 제한을 함께 지킵니다.
    if len(name.encode("utf-8")) > 240 or len(name.encode("utf-16-le")) // 2 > 240:
        raise ValueError("파일명이 너무 깁니다. 태그를 짧게 수정하세요.")
    return name


def tagged_name(name):
    """이력이 없는 다른 폴더에서도 동일한 5태그 이름 규칙은 재분석에서 제외합니다."""
    stem = Path(name).stem
    return (
        "__" in stem
        and len(stem.rsplit("__", 1)[1].split("_")) == 5
        and all(stem.rsplit("__", 1)[1].split("_"))
    )


def rename_no_replace(source, target):
    """대상이 생기면 실패하는 OS rename을 사용합니다. 사전 exists 검사만 믿지 않습니다."""
    if sys.platform == "win32":
        os.rename(source, target)  # Windows는 기존 대상이 있으면 FileExistsError.
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        func = libc.renamex_np
        func.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        func.restype = ctypes.c_int
        if func(os.fsencode(source), os.fsencode(target), 4):  # RENAME_EXCL
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        func = libc.renameat2
        func.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        func.restype = ctypes.c_int
        if func(-100, os.fsencode(source), -100, os.fsencode(target), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    else:
        raise RuntimeError("이 운영체제의 안전한 파일명 변경을 지원하지 않습니다.")


class FolderLock:
    """프로세스 종료 시 OS가 잠금을 해제합니다. 잠금 파일이 남아도 재시작할 수 있습니다."""

    def __init__(self, root):
        self.directory = Path(root) / ".filename"
        self.file = None

    def __enter__(self):
        self.directory.mkdir(exist_ok=True)
        self.file = (self.directory / "folder.lock").open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError(
                "다른 filename 프로그램이 이 폴더를 처리 중입니다."
            ) from exc
        return self

    def __exit__(self, *_):
        self.file.close()


class History:
    """rename 전에 pending을 저장하고, 완료 후 done으로 바꿉니다."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.db = sqlite3.connect(self.root / ".filename" / "history.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
            sha256 TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        self.db.commit()
        self.recover()

    def close(self):
        self.db.close()

    def path(self, relative):
        path = self.root / relative
        # 심볼릭 링크나 폴더 밖 경로를 따라 이름을 변경하지 않습니다.
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("폴더 밖 경로와 심볼릭 링크는 처리할 수 없습니다.")
        return path

    def matches(self, path, sha):
        return path.is_file() and not path.is_symlink() and file_hash(path) == sha

    def status(self, row_id, value):
        self.db.execute("UPDATE changes SET status=? WHERE id=?", (value, row_id))
        self.db.commit()

    def recover(self):
        for row in self.db.execute(
            "SELECT * FROM changes WHERE status IN ('pending','undo_pending')"
        ).fetchall():
            source, target = self.path(row["source"]), self.path(row["target"])
            forward = row["status"] == "pending"
            before, after = (source, target) if forward else (target, source)
            if not before.exists() and self.matches(after, row["sha256"]):
                self.status(row["id"], "done" if forward else "undone")
            elif self.matches(before, row["sha256"]) and not after.exists():
                self.status(row["id"], "failed" if forward else "done")
            else:
                raise RuntimeError(
                    "중단된 이름 변경의 파일 상태가 다릅니다. .filename 이력과 원본을 확인하세요."
                )

    def done_paths(self):
        return {
            r["target"]: r["sha256"]
            for r in self.db.execute("SELECT * FROM changes WHERE status='done'")
        }

    def rename(self, source, target, sha):
        old, new = self.path(source), self.path(target)
        if old.parent != new.parent:
            raise ValueError("파일명만 변경할 수 있습니다.")
        if not self.matches(old, sha):
            raise ValueError(
                "분석 후 원본이 변경되었거나 없어졌습니다. 다시 분석하세요."
            )
        # 대소문자 구분 파일시스템에서도 Windows로 옮길 수 있게 충돌을 검사합니다.
        if any(
            unicodedata.normalize("NFC", p.name).casefold()
            == unicodedata.normalize("NFC", new.name).casefold()
            for p in old.parent.iterdir()
        ):
            raise FileExistsError(f"이미 같은 이름의 파일이 있습니다: {new.name}")
        cursor = self.db.execute(
            "INSERT INTO changes(source,target,sha256,status) VALUES(?,?,?,'pending')",
            (source, target, sha),
        )
        self.db.commit()
        row_id = cursor.lastrowid
        try:
            rename_no_replace(old, new)
        except OSError:
            self.status(row_id, "failed")
            raise
        self.status(row_id, "done")
        return row_id

    def undo(self):
        """최근 변경부터 복원합니다. 충돌·내용 변경이 있으면 그 파일은 보존하고 보고합니다."""
        results = []
        for row in self.db.execute(
            "SELECT * FROM changes WHERE status='done' ORDER BY id DESC"
        ).fetchall():
            try:
                source, target = self.path(row["source"]), self.path(row["target"])
                if not self.matches(target, row["sha256"]):
                    raise ValueError("변경 후 파일 내용이 바뀌었거나 없어졌습니다.")
                if any(
                    p.name.casefold() == source.name.casefold()
                    for p in source.parent.iterdir()
                ):
                    raise FileExistsError("원래 이름의 파일이 이미 있습니다.")
                self.status(row["id"], "undo_pending")
                rename_no_replace(target, source)
                self.status(row["id"], "undone")
                results.append({"name": row["source"], "ok": True})
            except (OSError, ValueError) as exc:
                self.recover()
                results.append({"name": row["source"], "ok": False, "error": str(exc)})
        return results
