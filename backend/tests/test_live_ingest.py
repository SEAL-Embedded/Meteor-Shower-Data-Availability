"""Ingest for the two live instruments, against their real conventions.

NimbusTrace writes raw sample CSVs named ``data-2026-08-13T03-34-48-474405.csv``. SuperSID writes a
folder per capture, ``SuperSID-0813T12-03-00/``, holding a ``.wav`` and a ``.pkf``. Both run
continuously in ten-second captures, so days of recording mean tens of thousands of files.
"""

import struct
from datetime import datetime, timezone

import pytest

from availability.config import Config
from availability.models import Quality
from availability.store import Store

NIMBUS_TOML = """
[[instruments]]
id = "nimbustrace-seattle"
name = "NimbusTrace"
kind = "vlf"
system = "nimbustrace"

[[sources]]
id = "captures"
adapter = "csv_timeseries"
kind = "coverage"
name = "NimbusTrace captures"
instrument_id = "nimbustrace-seattle"
path = "captures"
duration_s = 10
gap_tolerance_s = 30
folder_quality = {{ "_completely_saved" = "good", "_break_saved" = "degraded" }}
"""

SUPERSID_TOML = """
[[instruments]]
id = "supersid-seattle"
name = "SuperSID"
kind = "vlf"
system = "supersid"

[[sources]]
id = "audio"
adapter = "file_scan"
kind = "coverage"
name = "SuperSID recordings"
instrument_id = "supersid-seattle"
path = "audio"
glob = "**/*.wav"
timestamp_source = "folder"
year = 2026
duration_source = "fixed"
duration_s = 10
gap_tolerance_s = 30
{extra}
"""


def wav_bytes(seconds=10, rate=96000):
    frames = seconds * rate
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    body = (
        b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"data" + struct.pack("<I", frames * 4) + b"\x00" * 16
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def capture_csv(rows=3, step=1e-5, start=93600.0):
    lines = ["Time,Channel 0,Channel 1"]
    for i in range(rows):
        lines.append(f"{start + i * step},0.001,-0.001")
    return "\n".join(lines) + "\n"


def build(tmp_path, toml, **fmt):
    (tmp_path / "config.toml").write_text(toml.format(**fmt), encoding="utf-8")
    return Store.build(Config.load(tmp_path / "config.toml"))


COMPLETE = "Data-2026-08-13T03-34-00_completely_saved"
BROKEN = "Data-2026-08-13T05-34-38_break_saved"


class TestNimbusTrace:
    @pytest.fixture
    def captures(self, tmp_path):
        folder = tmp_path / "captures"
        folder.mkdir()
        return folder

    def session(self, captures, folder_name, stamps):
        folder = captures / folder_name
        folder.mkdir(exist_ok=True)
        for stamp in stamps:
            (folder / f"data-2026-08-13T{stamp}-000000.csv").write_text(
                capture_csv(), encoding="utf-8"
            )
        return folder

    def test_continuous_captures_become_one_interval(self, tmp_path, captures):
        self.session(captures, COMPLETE, ["03-34-00", "03-34-10", "03-34-20", "03-34-30"])
        store = build(tmp_path, NIMBUS_TOML)
        assert len(store.coverage) == 1
        record = store.coverage[0]
        assert record.start == datetime(2026, 8, 13, 3, 34, 0, tzinfo=timezone.utc)
        assert (record.end - record.start).total_seconds() == 40  # four ten-second captures

    def test_a_real_stop_splits_the_record(self, tmp_path, captures):
        self.session(captures, COMPLETE, ["03-34-00", "03-34-10", "04-00-00"])
        assert len(build(tmp_path, NIMBUS_TOML).coverage) == 2

    def test_a_fixed_length_means_the_files_are_never_opened(self, tmp_path, captures):
        """The whole point at ten-second cadence: names carry everything needed."""
        folder = self.session(captures, COMPLETE, [])
        (folder / "data-2026-08-13T03-34-00-000000.csv").write_bytes(b"\x00\x01 not csv at all")
        store = build(tmp_path, NIMBUS_TOML)
        assert len(store.coverage) == 1
        assert (store.coverage[0].end - store.coverage[0].start).total_seconds() == 10

    def test_without_a_fixed_length_the_time_column_is_read(self, tmp_path, captures):
        folder = self.session(captures, COMPLETE, [])
        (folder / "data-2026-08-13T03-34-00-000000.csv").write_text(
            capture_csv(rows=1001), encoding="utf-8"
        )
        toml = NIMBUS_TOML.replace("duration_s = 10\n", "")
        store = build(tmp_path, toml)
        # 1001 rows at 10 us, plus one sample period for the final row.
        assert (store.coverage[0].end - store.coverage[0].start).total_seconds() == pytest.approx(
            0.01001, abs=1e-6
        )


class TestSessionCompleteness:
    """The writer records how a session ended; that is a fact about the data, not decoration."""

    @pytest.fixture
    def captures(self, tmp_path):
        folder = tmp_path / "captures"
        folder.mkdir()
        return folder

    def session(self, captures, folder_name, stamps):
        folder = captures / folder_name
        folder.mkdir(exist_ok=True)
        for stamp in stamps:
            (folder / f"data-2026-08-13T{stamp}-000000.csv").write_text(
                capture_csv(), encoding="utf-8"
            )

    def test_a_complete_session_is_good(self, tmp_path, captures):
        self.session(captures, COMPLETE, ["03-34-00", "03-34-10"])
        assert build(tmp_path, NIMBUS_TOML).coverage[0].quality is Quality.GOOD

    def test_an_interrupted_session_is_degraded_not_discarded(self, tmp_path, captures):
        self.session(captures, BROKEN, ["05-34-40", "05-34-50"])
        store = build(tmp_path, NIMBUS_TOML)
        assert len(store.coverage) == 1
        assert store.coverage[0].quality is Quality.DEGRADED

    def test_a_broken_session_never_merges_into_a_clean_one(self, tmp_path, captures):
        """Adjacent in time, different in kind: merging would launder the interruption away."""
        self.session(captures, COMPLETE, ["03-34-00", "03-34-10"])
        self.session(captures, BROKEN, ["03-34-20", "03-34-30"])
        store = build(tmp_path, NIMBUS_TOML)
        assert [record.quality for record in store.coverage] == [
            Quality.GOOD,
            Quality.DEGRADED,
        ]

    def test_an_unrecognised_folder_is_flagged_rather_than_taken_as_clean(self, tmp_path, captures):
        self.session(captures, "Data-2026-08-13T09-00-00_something_new", ["09-00-00"])
        store = build(tmp_path, NIMBUS_TOML)
        assert "no completeness marker" in store.sources[0].detail
        assert "something_new" in store.sources[0].detail


class TestCandidateDrives:
    """The data drive's letter is not guaranteed, so a source may name several places."""

    def toml(self, paths):
        listed = ", ".join(f'"{p}"' for p in paths)
        return NIMBUS_TOML.replace('path = "captures"', f"path = [{listed}]")

    def test_the_first_existing_directory_wins(self, tmp_path):
        real = tmp_path / "second-drive"
        real.mkdir()
        folder = real / COMPLETE
        folder.mkdir()
        (folder / "data-2026-08-13T03-34-00-000000.csv").write_text(
            capture_csv(), encoding="utf-8"
        )
        store = build(tmp_path, self.toml(["missing-drive", "second-drive"]))
        assert len(store.coverage) == 1

    def test_when_none_exist_every_path_tried_is_named(self, tmp_path):
        store = build(tmp_path, self.toml(["nowhere-1", "nowhere-2"]))
        detail = store.sources[0].detail
        assert "nowhere-1" in detail and "nowhere-2" in detail

    def test_a_single_path_still_works(self, tmp_path):
        real = tmp_path / "captures"
        real.mkdir()
        folder = real / COMPLETE
        folder.mkdir()
        (folder / "data-2026-08-13T03-34-00-000000.csv").write_text(
            capture_csv(), encoding="utf-8"
        )
        assert len(build(tmp_path, NIMBUS_TOML).coverage) == 1


class TestSuperSid:
    @pytest.fixture
    def audio(self, tmp_path):
        folder = tmp_path / "audio"
        folder.mkdir()
        return folder

    def capture(self, audio, folder_name):
        folder = audio / folder_name
        folder.mkdir()
        (folder / "recording.wav").write_bytes(wav_bytes())
        (folder / "recording.pkf").write_bytes(b"peak cache, not data")
        return folder

    def test_the_time_comes_from_the_folder_name(self, tmp_path, audio):
        self.capture(audio, "SuperSID-0813T12-03-00")
        store = build(tmp_path, SUPERSID_TOML, extra="")
        assert len(store.coverage) == 1
        assert store.coverage[0].start == datetime(2026, 8, 13, 12, 3, 0, tzinfo=timezone.utc)

    def test_the_configured_year_is_applied(self, tmp_path, audio):
        """The folder gives month and day only; which August has to be stated."""
        self.capture(audio, "SuperSID-0813T12-03-00")
        toml = SUPERSID_TOML.replace("year = 2026", "year = 2024")
        assert build(tmp_path, toml, extra="").coverage[0].start.year == 2024

    def test_the_peak_file_is_not_counted_as_a_recording(self, tmp_path, audio):
        self.capture(audio, "SuperSID-0813T12-03-00")
        store = build(tmp_path, SUPERSID_TOML, extra="")
        assert len(store.coverage) == 1  # one capture, not two

    def test_consecutive_captures_join(self, tmp_path, audio):
        for name in ("SuperSID-0813T12-03-00", "SuperSID-0813T12-03-10", "SuperSID-0813T12-03-20"):
            self.capture(audio, name)
        store = build(tmp_path, SUPERSID_TOML, extra="")
        assert len(store.coverage) == 1
        assert (store.coverage[0].end - store.coverage[0].start).total_seconds() == 30

    def test_a_gap_splits_the_record(self, tmp_path, audio):
        for name in ("SuperSID-0813T12-03-00", "SuperSID-0813T14-00-00"):
            self.capture(audio, name)
        assert len(build(tmp_path, SUPERSID_TOML, extra="").coverage) == 2

    def test_an_unreadable_folder_name_is_reported_not_guessed(self, tmp_path, audio):
        self.capture(audio, "SuperSID-0813T12-03-00")
        self.capture(audio, "loose-recordings")
        store = build(tmp_path, SUPERSID_TOML, extra="")
        assert len(store.coverage) == 1
        assert "loose-recordings" in store.sources[0].detail

    def test_a_day_of_captures_is_stitched_into_one_run(self, tmp_path, audio):
        """8,640 captures a day is the real scale; the result should be one interval."""
        for i in range(600):
            minute, second = divmod(i * 10, 60)
            self.capture(audio, f"SuperSID-0813T{12 + minute // 60:02d}-{minute % 60:02d}-{second:02d}")
        store = build(tmp_path, SUPERSID_TOML, extra="")
        assert len(store.coverage) == 1
        assert (store.coverage[0].end - store.coverage[0].start).total_seconds() == 6000
