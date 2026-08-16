"""The recording-directory scanner, against the header shapes real instruments produce."""

import struct
from datetime import datetime, timezone

import pytest

from availability.config import Config
from availability.ingest.file_scan import _timestamp_from_name, _wav_duration_s
from availability.models import Quality, SourceStatus
from availability.store import Store

PCM = 1
IEEE_FLOAT = 3


def wav_bytes(
    *,
    audio_format: int = IEEE_FLOAT,
    channels: int = 1,
    rate: int = 96000,
    bits: int = 32,
    frames: int = 96000,
    extra_chunk: bytes = b"",
) -> bytes:
    block_align = channels * bits // 8
    byte_rate = rate * block_align
    audio = b"\x00" * (frames * block_align)
    fmt = struct.pack("<HHIIHH", audio_format, channels, rate, byte_rate, block_align, bits)
    body = (
        b"WAVE"
        + extra_chunk
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(audio))
        + audio
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def write_wav(directory, name: str, **kwargs):
    path = directory / name
    path.write_bytes(wav_bytes(**kwargs))
    return path


class TestWavDuration:
    def test_ieee_float_is_read(self, tmp_path):
        """The standard library's wave module refuses format 3; a VLF chain writes format 3."""
        path = write_wav(tmp_path, "a.wav", audio_format=IEEE_FLOAT, rate=96000, frames=960000)
        assert _wav_duration_s(path) == pytest.approx(10.0)

    def test_integer_pcm_is_read(self, tmp_path):
        path = write_wav(tmp_path, "b.wav", audio_format=PCM, bits=16, rate=48000, frames=24000)
        assert _wav_duration_s(path) == pytest.approx(0.5)

    def test_stereo_length_is_not_doubled(self, tmp_path):
        mono = write_wav(tmp_path, "m.wav", channels=1, rate=96000, frames=96000)
        stereo = write_wav(tmp_path, "s.wav", channels=2, rate=96000, frames=96000)
        assert _wav_duration_s(mono) == pytest.approx(_wav_duration_s(stereo))

    def test_chunks_before_fmt_are_stepped_over(self, tmp_path):
        listing = b"LIST" + struct.pack("<I", 10) + b"INFOhello!"
        path = write_wav(tmp_path, "c.wav", frames=96000, extra_chunk=listing)
        assert _wav_duration_s(path) == pytest.approx(1.0)

    def test_an_odd_sized_chunk_does_not_desynchronise_the_reader(self, tmp_path):
        odd = b"LIST" + struct.pack("<I", 5) + b"INFO!" + b"\x00"
        path = write_wav(tmp_path, "d.wav", frames=96000, extra_chunk=odd)
        assert _wav_duration_s(path) == pytest.approx(1.0)

    def test_a_non_wav_file_is_refused_by_name(self, tmp_path):
        path = tmp_path / "e.wav"
        path.write_bytes(b"this is not a RIFF file at all")
        with pytest.raises(ValueError, match="not a RIFF/WAVE file"):
            _wav_duration_s(path)

    def test_rf64_without_its_size_chunk_fails_loudly(self, tmp_path):
        """A wrapped-around length would be worse than an error."""
        body = (
            b"WAVE"
            + b"fmt "
            + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", IEEE_FLOAT, 1, 96000, 384000, 4, 32)
            + b"data"
            + struct.pack("<I", 0xFFFFFFFF)
        )
        path = tmp_path / "big.wav"
        path.write_bytes(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body)
        with pytest.raises(ValueError, match="ds64"):
            _wav_duration_s(path)

    def test_rf64_reads_its_true_size(self, tmp_path):
        ds64 = b"ds64" + struct.pack("<I", 28) + struct.pack("<QQQI", 0, 768000, 0, 0)
        body = (
            b"WAVE"
            + ds64
            + b"fmt "
            + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", IEEE_FLOAT, 1, 96000, 384000, 4, 32)
            + b"data"
            + struct.pack("<I", 0xFFFFFFFF)
        )
        path = tmp_path / "big.wav"
        path.write_bytes(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body)
        assert _wav_duration_s(path) == pytest.approx(2.0)


class TestTimestampFromName:
    @pytest.mark.parametrize(
        "name",
        [
            "data-2026-08-13T03-34-48-474405.wav",
            "data-2026-08-13T03-34-48.wav",
            "20260813_033448.wav",
            "20260813T033448.wav",
            "20260813-033448.wav",
            "capture20260813033448.wav",
        ],
    )
    def test_common_conventions_need_no_configuration(self, name):
        found = _timestamp_from_name(name, None, None)
        assert found.replace(microsecond=0) == datetime(
            2026, 8, 13, 3, 34, 48, tzinfo=timezone.utc
        )

    def test_sub_second_precision_survives(self):
        found = _timestamp_from_name("data-2026-08-13T03-34-48-474405.wav", None, None)
        assert found.microsecond == 474405

    def test_an_unrecognisable_name_is_reported(self):
        with pytest.raises(ValueError, match="no recognisable timestamp"):
            _timestamp_from_name("Example wav file.wav", None, None)

    def test_a_custom_regex_needs_a_format(self):
        import re

        with pytest.raises(ValueError, match="without a timestamp_format"):
            _timestamp_from_name("x.wav", re.compile(r"(?P<ts>\d+)"), None)


CONFIG_TOML = """
[[instruments]]
id = "nimbustrace-seattle"
name = "NimbusTrace"
kind = "vlf"
system = "nimbustrace"

[[sources]]
id = "recordings"
adapter = "file_scan"
kind = "coverage"
name = "NimbusTrace recordings"
instrument_id = "nimbustrace-seattle"
path = "recordings"
gap_tolerance_s = {gap}
"""


@pytest.fixture
def scan_config(tmp_path):
    def build(gap: float = 60.0) -> Config:
        (tmp_path / "config.toml").write_text(CONFIG_TOML.format(gap=gap), encoding="utf-8")
        return Config.load(tmp_path / "config.toml")

    (tmp_path / "recordings").mkdir()
    return build


class TestAdapter:
    def test_consecutive_files_become_one_interval(self, tmp_path, scan_config):
        recordings = tmp_path / "recordings"
        write_wav(recordings, "data-2026-08-13T03-00-00-000000.wav", frames=96000 * 60)
        write_wav(recordings, "data-2026-08-13T03-01-00-000000.wav", frames=96000 * 60)
        store = Store.build(scan_config())
        assert len(store.coverage) == 1
        assert store.coverage[0].end.minute == 2

    def test_a_real_gap_splits_the_record(self, tmp_path, scan_config):
        recordings = tmp_path / "recordings"
        write_wav(recordings, "data-2026-08-13T03-00-00-000000.wav", frames=96000 * 60)
        write_wav(recordings, "data-2026-08-13T05-00-00-000000.wav", frames=96000 * 60)
        store = Store.build(scan_config())
        assert len(store.coverage) == 2

    def test_the_characterised_range_spans_the_whole_scan(self, tmp_path, scan_config):
        recordings = tmp_path / "recordings"
        write_wav(recordings, "data-2026-08-13T03-00-00-000000.wav", frames=96000 * 60)
        write_wav(recordings, "data-2026-08-13T05-00-00-000000.wav", frames=96000 * 60)
        known = Store.build(scan_config()).instrument_map["nimbustrace-seattle"].known_range
        assert known.start.hour == 3 and known.end.hour == 5
        assert known.end.minute == 1

    def test_an_unreadable_file_is_skipped_and_named(self, tmp_path, scan_config):
        recordings = tmp_path / "recordings"
        write_wav(recordings, "data-2026-08-13T03-00-00-000000.wav", frames=96000 * 60)
        (recordings / "data-2026-08-13T04-00-00-000000.wav").write_bytes(b"not a wav")
        store = Store.build(scan_config())
        source = store.sources[0]
        assert source.status is SourceStatus.STALE
        assert "04-00-00" in source.detail
        assert len(store.coverage) == 1

    def test_an_empty_directory_reports_rather_than_crashes(self, scan_config):
        store = Store.build(scan_config())
        assert store.sources[0].status is SourceStatus.STALE
        assert store.coverage == []

    def test_scanned_coverage_is_marked_good(self, tmp_path, scan_config):
        write_wav(tmp_path / "recordings", "data-2026-08-13T03-00-00-000000.wav", frames=9600)
        store = Store.build(scan_config())
        assert store.coverage[0].quality is Quality.GOOD
