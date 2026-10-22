from .connections import connect
from .extractor import source_sql, metadata, batches
from .transformer import transform_batch
from .loader import Loader

def run(job, progress):
    job.runnable()
    count = 0
    with connect(job.source_connection, job.command_timeout) as source, connect(job.target_connection, job.command_timeout) as target:
        query = source_sql(job)
        columns = metadata(source, query)
        loader = Loader(target, job.target_schema, job.target_table, columns)
        try:
            loader.prepare()
            progress(0, 'Загрузка staging')
            cursor = source.cursor()
            try:
                cursor.execute(query)
                for rows in batches(cursor, job.batch_size):
                    loader.write(transform_batch(rows))
                    count += len(rows)
                    progress(count, 'Загрузка staging')
            finally:
                cursor.close()
            progress(count, 'Атомарная публикация')
            loader.publish(count)
            progress(count, 'Данные опубликованы')
        finally:
            loader.cleanup()
    return count
