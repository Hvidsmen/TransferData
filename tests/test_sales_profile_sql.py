"""Runtime string limits that the SQL syntax parser cannot enforce."""

import re
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / 'tools/sql/Profile-SalesDocuments.sql'


def test_large_final_statement_is_not_used_as_replace_search_pattern():
    source = SOURCE.read_text(encoding='utf-8')
    # SQL Server error 8152: REPLACE's second argument is page-size limited,
    # regardless of the nvarchar(max) type of the input/variables.
    assert not re.search(r'\bREPLACE\s*\([^,]+,\s*@finalOriginal\b', source, re.I)
    # Cutting the suffix must include trailing spaces to preserve exact SQL.
    assert '@finalChars bigint=DATALENGTH(@finalOriginal)/2' in source


def test_temp_read_replace_pattern_has_an_explicit_byte_limit():
    source = SOURCE.read_text(encoding='utf-8')
    assert source.index('IF DATALENGTH(@oldRead)>8000') < source.index('REPLACE(@builtBase,@oldRead')
