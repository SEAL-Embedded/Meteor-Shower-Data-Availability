"""The command line.

Argument order caught every documented command out once: ``--config`` was accepted only before the
subcommand, so ``availability publish --config x`` -- the form written in the README, the operator
notes and a checklist -- failed. These tests pin both orders, because a CLI that only works when
written one way makes its own documentation wrong.
"""

import pytest

from availability.cli import main

CONFIG_TOML = """
[output]
directory = "out"

[[instruments]]
id = "sphere-vlf-seattle"
name = "Sphere VLF"
kind = "vlf"
system = "sphere"

[[sources]]
id = "record"
adapter = "csv_coverage"
kind = "coverage"
name = "Coverage"
path = "coverage.csv"
"""

COVERAGE_CSV = """instrument_id,start,end,quality,note
sphere-vlf-seattle,2024-07-29T00:00:00Z,2024-07-29T06:00:00Z,good,
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "coverage.csv").write_text(COVERAGE_CSV, encoding="utf-8")
    (tmp_path / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestArgumentOrder:
    def test_config_before_the_command(self, project, capsys):
        assert main(["--config", "config.toml", "check"]) == 0
        assert "1 interval(s)" in capsys.readouterr().out

    def test_config_after_the_command(self, project, capsys):
        assert main(["check", "--config", "config.toml"]) == 0
        assert "1 interval(s)" in capsys.readouterr().out

    def test_both_orders_agree(self, project, capsys):
        main(["--config", "config.toml", "check"])
        before = capsys.readouterr().out
        main(["check", "--config", "config.toml"])
        after = capsys.readouterr().out
        assert before.splitlines()[1:] == after.splitlines()[1:]  # line 0 is the timestamp

    def test_it_works_with_a_flag_after_the_command(self, project):
        assert main(["--config", "config.toml", "publish", "--strict"]) == 0
        assert (project / "out" / "index.json").is_file()

    def test_the_flag_order_does_not_matter_for_publish(self, project):
        assert main(["publish", "--config", "config.toml", "--strict"]) == 0
        assert (project / "out" / "index.json").is_file()


class TestDefaults:
    def test_it_falls_back_to_config_toml_in_the_working_directory(self, project, capsys):
        assert main(["check"]) == 0
        assert "instruments  1" in capsys.readouterr().out

    def test_a_missing_config_is_a_clear_failure(self, project, capsys):
        assert main(["--config", "nope.toml", "check"]) == 2
        assert "no configuration file" in capsys.readouterr().err

    def test_no_command_prints_help(self, project, capsys):
        assert main([]) == 2
        assert "publish" in capsys.readouterr().out
