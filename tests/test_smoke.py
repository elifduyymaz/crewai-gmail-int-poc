import subprocess
import sys

import mail_ingestor
from mail_ingestor.main import build_parser


def test_version_is_set():
    assert isinstance(mail_ingestor.__version__, str)
    assert mail_ingestor.__version__ == "0.1.0"


def test_parser_builds():
    parser = build_parser()
    args = parser.parse_args(["--label", "poc/reports"])
    assert args.label == "poc/reports"


def test_main_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "mail_ingestor.main", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
