from .transformer import Column, sql_type
from .exceptions import ETLError
from .validators import identifier, table_name, read_query

def source_sql(job):
    return f'SELECT * FROM {table_name(job.source_table)}' if job.source_type == 'TABLE' else read_query(job.sql_query)

def metadata(conn, query):
    cursor = conn.cursor()
    try:
        cursor.execute('EXEC sys.sp_describe_first_result_set @tsql = ?, @params = NULL, @browse_information_mode = 0', query)
        keys = [d[0] for d in cursor.description]
        columns = []
        for raw in cursor.fetchall():
            item = dict(zip(keys, raw))
            if item.get('is_hidden'):
                continue
            if item.get('error_number') or not item.get('name') or item.get('is_nullable') is None:
                raise ETLError('Не удалось однозначно определить структуру SELECT.')
            identifier(item['name'])
            columns.append(Column(item['name'], sql_type(item['system_type_name']), bool(item['is_nullable'])))
        if not columns or len({c.name.casefold() for c in columns}) != len(columns):
            raise ETLError('Пустые или повторяющиеся имена колонок; задайте уникальные SQL aliases.')
        return columns
    finally:
        cursor.close()

def batches(cursor, size):
    while True:
        batch = cursor.fetchmany(size)
        if not batch:
            return
        yield batch
