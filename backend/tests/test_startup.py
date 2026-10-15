"""What every process checks before it serves or works (app/startup.py) that no other module's
tests cover: a temporary directory to write uploads in."""

import os
import tempfile

import pytest

from app.config import get_settings
from app.startup import check_temporary_directory, prepare

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root writes in any directory")


def test_no_temporary_directory_to_write_in_stops_the_start(monkeypatch, tmp_path, services):
    # A read-only filesystem without a tmpfs: the directory Python settled on cannot be written.
    locked = tmp_path / "tmp"
    locked.mkdir(mode=0o500)
    monkeypatch.setattr(tempfile, "tempdir", str(locked))
    with pytest.raises(RuntimeError, match="Mount a tmpfs at /tmp, or set TMPDIR") as refusal:
        prepare(get_settings(), services)
    assert "Permission denied" in str(refusal.value)

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    check_temporary_directory()


def test_a_tmpdir_python_passed_over_is_named(monkeypatch, tmp_path, caplog):
    locked = tmp_path / "encrypted"
    locked.mkdir(mode=0o500)
    monkeypatch.setenv("TMPDIR", str(locked))
    # Python chooses again, as a process that starts does.
    monkeypatch.setattr(tempfile, "tempdir", None)
    with caplog.at_level("WARNING", logger="app.startup"):
        check_temporary_directory()
    chosen = tempfile.gettempdir()
    assert chosen != str(locked)
    assert f"TMPDIR names {locked}, which this process cannot write in" in caplog.text
    assert f"go to {chosen} instead" in caplog.text

    caplog.clear()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    with caplog.at_level("WARNING", logger="app.startup"):
        check_temporary_directory()
    assert tempfile.gettempdir() == str(tmp_path)
    assert caplog.text == ""
