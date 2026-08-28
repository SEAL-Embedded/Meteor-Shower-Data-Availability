"""Configuration loading, and the parts of it that have to survive moving machines."""

from pathlib import Path

from availability.config import Config


class TestPortablePaths:
    """A configured path has to survive being carried to another machine.

    The AMS cache is deliberately outside the repository, so it is named absolutely -- and an
    absolute path with a username in it misses silently on any other account: the adapter finds no
    cache, makes an empty one, and re-fetches thousands of pages instead of failing.
    """

    def _config(self, tmp_path):
        return Config(
            title="t", instruments=[], sources=[], base_dir=tmp_path, output_dir=Path("out")
        )

    def test_a_home_relative_path_is_expanded(self, tmp_path):
        resolved = self._config(tmp_path).resolve("~/meteor-availability/cache/ams")
        assert "~" not in str(resolved)
        assert resolved.is_absolute()

    def test_an_environment_variable_is_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MSDA_CACHE_ROOT", str(tmp_path / "elsewhere"))
        resolved = self._config(tmp_path).resolve("$MSDA_CACHE_ROOT/ams")
        assert resolved == tmp_path / "elsewhere" / "ams"

    def test_a_relative_path_still_resolves_against_the_config(self, tmp_path):
        assert self._config(tmp_path).resolve("records/x.csv") == tmp_path / "records" / "x.csv"

    def test_an_absolute_path_is_left_alone(self, tmp_path):
        target = tmp_path / "absolute" / "x.csv"
        assert self._config(tmp_path).resolve(target) == target
