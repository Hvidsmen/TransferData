import os
from contextlib import contextmanager
import pyodbc
from .exceptions import ETLError

def escaped(value):
    return '{' + str(value).replace('}', '}}') + '}'

@contextmanager
def connect(config, timeout=None):
    config.full_clean()
    parts = {'DRIVER': config.driver, 'SERVER': f'{config.server},{config.port}', 'DATABASE': config.database, 'Encrypt': 'yes', 'TrustServerCertificate': 'no', 'APP': 'ETL Control Center'}
    if config.authentication_type == 'WINDOWS':
        parts['Trusted_Connection'] = 'yes'
    else:
        secret = os.environ.get(config.credential_reference)
        if not secret:
            raise ETLError('Не задана переменная окружения пароля подключения.')
        parts.update(UID=config.username, PWD=secret)
    conn = None
    try:
        conn = pyodbc.connect(';'.join(f'{k}={escaped(v)}' for k, v in parts.items()), timeout=config.connection_timeout, autocommit=False)
        conn.timeout = timeout or config.query_timeout
        yield conn
    finally:
        if conn is not None:
            conn.close()

def test_connection(config):
    try:
        with connect(config, timeout=min(config.query_timeout, 10)) as conn:
            row = conn.cursor().execute('SELECT 1').fetchone()
            return bool(row and row[0] == 1)
    except Exception:
        # ODBC exceptions can echo passwords and SQL. Never send their text to UI/logs.
        raise ETLError('Подключение не удалось. Проверьте сервер, TLS, ODBC, права и секрет в окружении.') from None
