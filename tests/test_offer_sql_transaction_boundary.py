"""Guard error 266, which a syntax-only T-SQL parser cannot detect."""

import re
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / 'tools/sql/Install-CommercialOfferPublication.sql'


def test_offer_worker_finishes_its_transaction_before_returning():
    source = SOURCE.read_text(encoding='utf-8')
    match = re.search(r"SET @worker=N'((?:[^']|'')*)';\s*SET @worker=", source)
    assert match, 'Cannot locate the actual production dynamic batch'
    worker = match.group(1).replace("''", "'")
    worker = re.sub(r'/\*.*?\*/|--[^\n]*', '', worker, flags=re.DOTALL)
    assert len(re.findall(r'\bBEGIN\s+TRANSACTION\b', worker, re.I)) == 1
    assert len(re.findall(r'\bCOMMIT\s+TRANSACTION\b', worker, re.I)) == 1
    assert worker.index('BEGIN TRANSACTION') < worker.index('ReplaceOfferRows') < worker.index('COMMIT TRANSACTION')
    assert worker.index('COMMIT TRANSACTION') < worker.rindex('END TRY')
    assert 'IF XACT_STATE()<>0 ROLLBACK TRANSACTION;' in worker
    # The outer procedure must not commit a transaction opened by sp_executesql.
    after_worker = source[match.end():]
    assert not re.search(r'\bCOMMIT\s+TRANSACTION\b', after_worker, re.I)
