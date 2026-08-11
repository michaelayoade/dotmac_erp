"""A recurring import must be able to say "I have seen this content before".

`BatchOperation.source_file` records which files a run CLAIMED to read. For a
monthly bank statement that is not the operational question — the question is
whether this is the same content already imported, and a filename cannot answer
it. UBA's statements arrive as `101xxxxx96.xlsx` and `101xxxxx96 (1).xlsx`
every month with the same names and different contents, so the filename is
actively misleading.

`source_checksum` has existed as a column since January 2026 with no writer.
`file_manifest_digest` is the writer.
"""

from __future__ import annotations

from pathlib import Path

from app.services.batch_operation import file_digest, file_manifest_digest


def _write(directory: Path, name: str, content: bytes) -> Path:
    path = directory / name
    path.write_bytes(content)
    return path


def test_the_same_content_digests_the_same(tmp_path: Path) -> None:
    """The load-bearing property: re-importing identical files is detectable."""
    first = _write(tmp_path, "a.xlsx", b"statement-a")
    second = _write(tmp_path, "b.xlsx", b"statement-b")

    before, _ = file_manifest_digest([first, second])
    after, _ = file_manifest_digest([first, second])

    assert before == after


def test_order_does_not_change_the_digest(tmp_path: Path) -> None:
    """Two runs over the same statements must agree even if the caller listed
    them differently — a config reordering is not new content."""
    first = _write(tmp_path, "a.xlsx", b"statement-a")
    second = _write(tmp_path, "b.xlsx", b"statement-b")

    assert (
        file_manifest_digest([first, second])[0]
        == (file_manifest_digest([second, first])[0])
    )


def test_changed_content_changes_the_digest(tmp_path: Path) -> None:
    """Sensitivity: the whole point is that next month's file is not last
    month's, even under the identical filename the bank keeps reusing."""
    path = _write(tmp_path, "statement.xlsx", b"january")
    january, _ = file_manifest_digest([path])

    path.write_bytes(b"february")
    february, _ = file_manifest_digest([path])

    assert january != february


def test_a_renamed_file_is_not_the_same_manifest(tmp_path: Path) -> None:
    """Specificity in the other direction: the manifest identifies the SET, so
    the same bytes under a different name is a different input set. Which file
    supplied which content is part of what is being identified."""
    original = _write(tmp_path, "a.xlsx", b"same-bytes")
    first, _ = file_manifest_digest([original])

    renamed = _write(tmp_path, "b.xlsx", b"same-bytes")
    second, _ = file_manifest_digest([renamed])

    assert first != second
    assert file_digest(original) == file_digest(renamed)


def test_a_missing_file_is_omitted_rather_than_fatal(tmp_path: Path) -> None:
    """A run over a partial set must still be identifiable as exactly that.

    Raising here would make the digest unavailable precisely when a run went
    wrong, which is when the record matters most. The caller decides whether a
    missing input is fatal; this only reports what was actually read.
    """
    present = _write(tmp_path, "a.xlsx", b"statement-a")

    digest, per_file = file_manifest_digest([present, tmp_path / "absent.xlsx"])

    assert per_file == {"a.xlsx": file_digest(present)}
    assert digest == file_manifest_digest([present])[0]


def test_the_per_file_map_records_which_file_supplied_what(tmp_path: Path) -> None:
    """When a digest stops matching, "which file changed" is the next question
    and the answer should already be on the record, not require the files."""
    first = _write(tmp_path, "a.xlsx", b"statement-a")
    second = _write(tmp_path, "b.xlsx", b"statement-b")

    _, per_file = file_manifest_digest([first, second])

    assert per_file == {
        "a.xlsx": file_digest(first),
        "b.xlsx": file_digest(second),
    }
