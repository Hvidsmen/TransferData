"""SQL syntax constraints not enforced by the offline T-SQL parser."""

import re
from pathlib import Path

import pytest


SQL_FILES = sorted((Path(__file__).resolve().parents[1] / "tools" / "sql").glob("*.sql"))


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda path: path.name)
def test_lock_timeout_uses_an_integer_literal(path):
    # SQL Server rejects SET LOCK_TIMEOUT @variable with error 102; sqlfluff's
    # T-SQL grammar accepts it. Keep this check alongside parser validation.
    source = path.read_text(encoding="utf-8-sig")
    source = re.sub(r"/\*.*?\*/|--[^\n]*", "", source, flags=re.DOTALL)
    for match in re.finditer(r"\bSET\s+LOCK_TIMEOUT\s+([^;\n]+)", source, re.IGNORECASE):
        assert re.fullmatch(r"-?\d+", match.group(1).strip()), (
            f"{path.name}: SET LOCK_TIMEOUT requires a literal integer"
        )
