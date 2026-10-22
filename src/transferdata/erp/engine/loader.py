import uuid
from .validators import identifier
from .extractor import metadata
from .exceptions import ETLError

class Loader:
    def __init__(self, conn, schema, table, columns):
        self.conn, self.columns = conn, columns
        self.target = f'{identifier(schema)}.{identifier(table)}'
        self.stage = f'{identifier(schema)}.{identifier("_etl_" + uuid.uuid4().hex)}'
        self.resource = 'etl:' + schema.casefold() + '.' + table.casefold()
        self.created = False
        self.locked = False
    def prepare(self):
        cursor = self.conn.cursor()
        try:
            cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=?, @LockMode='Exclusive', @LockOwner='Session', @LockTimeout=0; SELECT @r", self.resource)
            if cursor.fetchone()[0] < 0:
                raise ETLError('Целевая таблица занята другим переносом.')
            self.locked = True
            cursor.execute(f'CREATE TABLE {self.stage} (' + ','.join(c.definition() for c in self.columns) + ')')
            self.conn.commit()
            self.created = True
        finally:
            cursor.close()
    def write(self, rows):
        cursor = self.conn.cursor()
        try:
            cursor.fast_executemany = True
            names = ','.join(identifier(c.name) for c in self.columns)
            cursor.executemany(f'INSERT INTO {self.stage} ({names}) VALUES (' + ','.join('?' for _ in self.columns) + ')', rows)
            self.conn.commit()  # only staging data; working target remains unchanged
        finally:
            cursor.close()
    def publish(self, expected):
        cursor = self.conn.cursor()
        try:
            cursor.execute('SET XACT_ABORT ON')
            cursor.execute(f'SELECT COUNT_BIG(*) FROM {self.stage}')
            if cursor.fetchone()[0] != expected:
                raise ETLError('Количество строк staging не совпадает с извлечённым.')
            cursor.execute('SELECT OBJECT_ID(?, ?)', self.target, 'U')
            exists = cursor.fetchone()[0] is not None
            names = ','.join(identifier(c.name) for c in self.columns)
            if exists:
                existing = metadata(self.conn, f'SELECT * FROM {self.target}')
                if existing != self.columns:
                    raise ETLError('Схема целевой таблицы несовместима; автоматическое изменение запрещено.')
                # Reject special storage semantics that cannot safely accept a plain insert.
                cursor.execute('SELECT COUNT(*) FROM sys.columns WHERE object_id=OBJECT_ID(?) AND (is_identity=1 OR is_computed=1 OR generated_always_type<>0)', self.target)
                if cursor.fetchone()[0]:
                    raise ETLError('Identity/computed/temporal колонки назначения не поддерживаются.')
                cursor.execute(f'DELETE FROM {self.target}')
                cursor.execute(f'INSERT INTO {self.target} ({names}) SELECT {names} FROM {self.stage}')
            else:
                cursor.execute(f'SELECT {names} INTO {self.target} FROM {self.stage}')
            cursor.execute(f'SELECT COUNT_BIG(*) FROM {self.target}')
            if cursor.fetchone()[0] != expected:
                raise ETLError('Проверка количества строк назначения не пройдена.')
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        finally:
            cursor.close()
    def cleanup(self):
        self.conn.rollback()
        cursor = self.conn.cursor()
        try:
            if self.created:
                cursor.execute(f'DROP TABLE {self.stage}')
                self.conn.commit()
            if self.locked:
                cursor.execute("EXEC sys.sp_releaseapplock @Resource=?, @LockOwner='Session'", self.resource)
                self.conn.commit()
        finally:
            cursor.close()
