"""Tests for Clover XML coverage parser."""

from pathlib import Path
from textwrap import dedent

import pytest

from coderecon.testing.coverage.models import CoverageParseError
from coderecon.testing.coverage.parsers.clover import CloverParser


@pytest.fixture
def parser() -> CloverParser:
    return CloverParser()


MINIMAL_CLOVER = dedent("""\
    <?xml version="1.0"?>
    <coverage generated="1234" clover="4.0">
      <project timestamp="1234">
        <package name="app">
          <file name="Foo.php" path="/src/Foo.php">
            <line num="1" type="stmt" count="1"/>
            <line num="5" type="stmt" count="0"/>
            <line num="10" type="method" name="bar" count="3"/>
            <line num="15" type="cond" count="1" truecount="1" falsecount="0"/>
          </file>
        </package>
      </project>
    </coverage>
""")


class TestFormatId:
    def test_returns_clover(self, parser: CloverParser) -> None:
        assert parser.format_id == "clover"


class TestCanParse:
    def test_file_with_clover_in_name(self, parser: CloverParser, tmp_path: Path) -> None:
        f = tmp_path / "clover.xml"
        f.write_text("<coverage/>")
        assert parser.can_parse(f) is True

    def test_directory_with_clover_xml(self, parser: CloverParser, tmp_path: Path) -> None:
        (tmp_path / "clover.xml").write_text("<coverage/>")
        assert parser.can_parse(tmp_path) is True

    def test_directory_without_clover_xml(self, parser: CloverParser, tmp_path: Path) -> None:
        assert parser.can_parse(tmp_path) is False

    def test_file_with_clover_content(self, parser: CloverParser, tmp_path: Path) -> None:
        f = tmp_path / "report.xml"
        f.write_text('<coverage generated="123" clover="4.0"><project/></coverage>')
        assert parser.can_parse(f) is True

    def test_file_without_clover_content(self, parser: CloverParser, tmp_path: Path) -> None:
        f = tmp_path / "report.xml"
        f.write_text("<html><body>not coverage</body></html>")
        assert parser.can_parse(f) is False

    def test_nonexistent_path(self, parser: CloverParser, tmp_path: Path) -> None:
        assert parser.can_parse(tmp_path / "missing.xml") is False

    def test_directory_with_coverage_clover_xml(self, parser: CloverParser, tmp_path: Path) -> None:
        (tmp_path / "coverage-clover.xml").write_text("<coverage/>")
        assert parser.can_parse(tmp_path) is True


class TestParse:
    def test_parse_minimal(self, parser: CloverParser, tmp_path: Path) -> None:
        f = tmp_path / "clover.xml"
        f.write_text(MINIMAL_CLOVER)
        report = parser.parse(f)

        assert report.source_format == "clover"
        assert len(report.files) == 1

        fc = report.files["/src/Foo.php"]
        # Statement lines
        assert fc.lines[1] == 1
        assert fc.lines[5] == 0
        # Method line
        assert fc.lines[10] == 3
        assert "bar" in fc.functions
        assert fc.functions["bar"].hits == 3
        assert fc.functions["bar"].start_line == 10
        # Conditional line
        assert fc.lines[15] == 1
        assert len(fc.branches) == 2
        assert fc.branches[0].hits == 1  # true branch
        assert fc.branches[1].hits == 0  # false branch

    def test_parse_directory(self, parser: CloverParser, tmp_path: Path) -> None:
        (tmp_path / "clover.xml").write_text(MINIMAL_CLOVER)
        report = parser.parse(tmp_path)
        assert len(report.files) == 1

    def test_parse_with_base_path(self, parser: CloverParser, tmp_path: Path) -> None:
        f = tmp_path / "clover.xml"
        f.write_text(MINIMAL_CLOVER)
        report = parser.parse(f, base_path=Path("/src"))
        assert "Foo.php" in report.files

    def test_parse_missing_file_raises(self, parser: CloverParser, tmp_path: Path) -> None:
        with pytest.raises(CoverageParseError, match="not found"):
            parser.parse(tmp_path / "missing.xml")

    def test_parse_invalid_xml_raises(self, parser: CloverParser, tmp_path: Path) -> None:
        f = tmp_path / "clover.xml"
        f.write_text("not xml at all {{{")
        with pytest.raises(CoverageParseError, match="Invalid"):
            parser.parse(f)

    def test_parse_empty_file_element(self, parser: CloverParser, tmp_path: Path) -> None:
        xml = dedent("""\
            <?xml version="1.0"?>
            <coverage generated="1" clover="4.0">
              <project>
                <package name="p">
                  <file name="" path="">
                    <line num="1" type="stmt" count="1"/>
                  </file>
                </package>
              </project>
            </coverage>
        """)
        f = tmp_path / "clover.xml"
        f.write_text(xml)
        report = parser.parse(f)
        assert len(report.files) == 0

    def test_parse_skips_invalid_line_num(self, parser: CloverParser, tmp_path: Path) -> None:
        xml = dedent("""\
            <?xml version="1.0"?>
            <coverage generated="1" clover="4.0">
              <project>
                <package name="p">
                  <file name="a.php" path="/a.php">
                    <line num="0" type="stmt" count="1"/>
                    <line num="-1" type="stmt" count="1"/>
                    <line num="5" type="stmt" count="2"/>
                  </file>
                </package>
              </project>
            </coverage>
        """)
        f = tmp_path / "clover.xml"
        f.write_text(xml)
        report = parser.parse(f)
        fc = report.files["/a.php"]
        assert list(fc.lines.keys()) == [5]
        assert fc.lines[5] == 2

    def test_parse_multiple_files(self, parser: CloverParser, tmp_path: Path) -> None:
        xml = dedent("""\
            <?xml version="1.0"?>
            <coverage generated="1" clover="4.0">
              <project>
                <package name="p1">
                  <file name="A.php" path="/src/A.php">
                    <line num="1" type="stmt" count="1"/>
                  </file>
                </package>
                <package name="p2">
                  <file name="B.php" path="/src/B.php">
                    <line num="1" type="stmt" count="0"/>
                  </file>
                </package>
              </project>
            </coverage>
        """)
        f = tmp_path / "clover.xml"
        f.write_text(xml)
        report = parser.parse(f)
        assert len(report.files) == 2
        assert "/src/A.php" in report.files
        assert "/src/B.php" in report.files

    def test_parse_directory_no_xml_raises(self, parser: CloverParser, tmp_path: Path) -> None:
        with pytest.raises(CoverageParseError, match="No Clover XML"):
            parser.parse(tmp_path)
