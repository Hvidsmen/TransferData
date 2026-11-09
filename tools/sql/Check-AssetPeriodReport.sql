-- Диагностика источников report.sp_ВедомостьАрмотизацииОСПериод.
-- Выполнять на сервере подключения цепочки, под той же SQL/Windows-учётной записью.
-- Отчётная процедура не выполняется; DROP/CREATE/SELECT INTO рабочих таблиц нет.
-- Проверяются только доступность объектов и столбцов (TOP 0).
-- Пришлите итоговую таблицу с ошибками; пароли и строки данных не выводятся.
USE [1С];
SET NOCOUNT ON;

SELECT @@SERVERNAME AS Сервер, DB_NAME() AS База,
       SUSER_SNAME() AS SQLВход, ORIGINAL_LOGIN() AS ИсходныйВход;

DECLARE @checks TABLE (Position int, ObjectName nvarchar(512), Probe nvarchar(max));
INSERT @checks (Position, ObjectName, Probe) VALUES
(1, N'Счета ОС · [1С].[dbo].[РегистрСведений_СчетаБухгалтерскогоУчетаОС]', N'SELECT TOP (0) [Период], [Организация], [ОсновноеСредство], [СчетУчета], [СчетНачисленияАмортизации] FROM [1С].[dbo].[РегистрСведений_СчетаБухгалтерскогоУчетаОС];'),
(2, N'Местонахождение ОС · [1С].[dbo].[РегистрСведений_МестонахождениеОСБухгалтерскийУчет_Срез]', N'SELECT TOP (0) [ОсновноеСредство], [Период], [ПериодLast], [МестонахождениеНаименование] FROM [1С].[dbo].[РегистрСведений_МестонахождениеОСБухгалтерскийУчет_Срез];'),
(3, N'Срез счетов ОС · [1С].[dbo].[РегистрСведений_СчетаБухгалтерскогоУчетаОС_Срез]', N'SELECT TOP (0) [ОсновноеСредство], [Период], [ПериодLast], [СчетУчетаКод] FROM [1С].[dbo].[РегистрСведений_СчетаБухгалтерскогоУчетаОС_Срез];'),
(4, N'Бухгалтерские остатки · [1С].[dbo].[vРегистрБухгалтерии.ХозрасчетныйОстатки]', N'SELECT TOP (0) [Субконта1Наименование], [Субконта1Ссылка], [СчетНаименование], [Счет], [СчетСсылка], [ПодразделениеНаименование], [ПодразделениеСсылка], [Период], [Сумма] FROM [1С].[dbo].[vРегистрБухгалтерии.ХозрасчетныйОстатки];'),
(5, N'Календарь DataWH · [DataWH].[dbo].[Calendar]', N'SELECT TOP (0) [Date_] FROM [DataWH].[dbo].[Calendar];'),
(6, N'Состояния ТС · [1С].[uat].[РегистрСведений_уатСостояниеТС_Срез]', N'SELECT TOP (0) [ТС], [Период], [ПериодLast], [СостояниеНаименование] FROM [1С].[uat].[РегистрСведений_уатСостояниеТС_Срез];'),
(7, N'Местонахождение ТС · [1С].[uat].[РегистрСведений_уатМестонахождениеТС_Срез]', N'SELECT TOP (0) [ТС], [Период], [ПериодLast], [ПодразделениеНаименование] FROM [1С].[uat].[РегистрСведений_уатМестонахождениеТС_Срез];'),
(8, N'Основные средства · [1С].[dbo].[Справочник_ОсновныеСредства]', N'SELECT TOP (0) [СсылкаСсылка], [ГруппыОС], [ЗаводскойНомер], [Код], [Наименование] FROM [1С].[dbo].[Справочник_ОсновныеСредства];'),
(9, N'Классификация ОС DataWH · [DataWH].[dbo].[OSRefExcel]', N'SELECT TOP (0) [Инвентарный номер], [Группа 1], [Группа 2], [Марка] FROM [DataWH].[dbo].[OSRefExcel];'),
(10, N'Справочник ТС · [1С].[uat].[Справочник_уатТС]', N'SELECT TOP (0) [ОсновноеСредствоНаименование], [СсылкаСсылка], [ГосударственныйНомер], [ВидМоделиТСНаименование], [ТипТСНаименование], [КолоннаНаименование] FROM [1С].[uat].[Справочник_уатТС];'),
(11, N'Вагон-дома · [1С].[dbo].[vТСВагонДомаГруппы]', N'SELECT TOP (0) [СсылкаСсылка], [Родитель2Наименование], [Группа2] FROM [1С].[dbo].[vТСВагонДомаГруппы];');

DECLARE @results TABLE (
    Position int, ObjectName nvarchar(512), Result nvarchar(20),
    ErrorNumber int NULL, ErrorLine int NULL, ErrorMessage nvarchar(4000) NULL);
DECLARE @position int, @object nvarchar(512), @sql nvarchar(max);
DECLARE probes CURSOR LOCAL FAST_FORWARD FOR
    SELECT Position, ObjectName, Probe FROM @checks ORDER BY Position;
OPEN probes;
FETCH NEXT FROM probes INTO @position, @object, @sql;
WHILE @@FETCH_STATUS = 0
BEGIN
    BEGIN TRY
        EXEC sys.sp_executesql @sql;
        INSERT @results VALUES (@position, @object, N'OK', NULL, NULL, NULL);
    END TRY
    BEGIN CATCH
        INSERT @results VALUES (@position, @object, N'Ошибка', ERROR_NUMBER(), ERROR_LINE(), ERROR_MESSAGE());
    END CATCH;
    FETCH NEXT FROM probes INTO @position, @object, @sql;
END;
CLOSE probes;
DEALLOCATE probes;

SELECT Position AS Номер, ObjectName AS Объект, Result AS Результат,
       ErrorNumber AS КодОшибки, ErrorLine AS Строка, ErrorMessage AS Сообщение
FROM @results ORDER BY Position;

-- Прямые права текущего пользователя; цепочка владения/EXECUTE AS внутри процедуры
-- может предоставлять иной контекст. Ноль здесь сам по себе не доказывает причину.
SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE') AS ПрямоеПравоСоздаватьТаблицы,
       HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'ALTER') AS ПрямоеПравоИзменятьСхемуReport,
       HAS_PERMS_BY_NAME(N'[report].[sp_ВедомостьАрмотизацииОСПериод]', 'OBJECT', 'EXECUTE') AS ПрямоеПравоЗапускаПроцедуры;
