import re
from dataclasses import dataclass
from .exceptions import ETLError
from .validators import identifier

@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    nullable: bool

    def definition(self):
        return f'{identifier(self.name)} {self.sql_type} ' + ('NULL' if self.nullable else 'NOT NULL')

def sql_type(value):
    value = value.lower()
    allowed = r'(int|bigint|smallint|tinyint|bit|uniqueidentifier|date|datetime|smalldatetime|real|money|smallmoney|float(?:\((?:[1-9]|[1-4][0-9]|5[0-3])\))?|datetime2\([0-7]\)|datetimeoffset\([0-7]\)|time\([0-7]\)|(?:decimal|numeric)\([0-9]{1,2},[0-9]{1,2}\)|(?:nvarchar|varchar|varbinary)\((?:max|[1-9][0-9]*)\)|(?:nchar|char|binary)\([1-9][0-9]*\))'
    if not re.fullmatch(allowed, value):
        raise ETLError('Неподдерживаемый SQL-тип результата. Используйте явный CAST в SELECT.')
    return value

# Values are passed directly through pyodbc: Decimal, bytes, datetime and None
# keep their native representation. No lossy string/float conversion.
def transform_batch(rows):
    return rows
