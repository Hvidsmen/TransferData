-- Read-only source checks. Open as UTF-8 in SSMS and execute the entire file.
-- No report procedures, no DROP/CREATE/INSERT and no data rows are read.
-- GO is an SSMS batch separator. Use the Messages tab for any SQL error numbers.
USE [1С];
GO
SELECT @@SERVERNAME AS ServerName, DB_NAME() AS DatabaseName, SUSER_SNAME() AS LoginName;
GO

PRINT 'Probe 1';
GO
SELECT TOP (0) [Период], [Организация], [ОсновноеСредство], [СчетУчета], [СчетНачисленияАмортизации] FROM [1С].[dbo].[РегистрСведений_СчетаБухгалтерскогоУчетаОС];
GO

PRINT 'Probe 2';
GO
SELECT TOP (0) [ОсновноеСредство], [Период], [ПериодLast], [МестонахождениеНаименование] FROM [1С].[dbo].[РегистрСведений_МестонахождениеОСБухгалтерскийУчет_Срез];
GO

PRINT 'Probe 3';
GO
SELECT TOP (0) [ОсновноеСредство], [Период], [ПериодLast], [СчетУчетаКод] FROM [1С].[dbo].[РегистрСведений_СчетаБухгалтерскогоУчетаОС_Срез];
GO

PRINT 'Probe 4';
GO
SELECT TOP (0) [Субконта1Наименование], [Субконта1Ссылка], [СчетНаименование], [Счет], [СчетСсылка], [ПодразделениеНаименование], [ПодразделениеСсылка], [Период], [Сумма] FROM [1С].[dbo].[vРегистрБухгалтерии.ХозрасчетныйОстатки];
GO

PRINT 'Probe 5';
GO
SELECT TOP (0) [Date_] FROM [DataWH].[dbo].[Calendar];
GO

PRINT 'Probe 6';
GO
SELECT TOP (0) [ТС], [Период], [ПериодLast], [СостояниеНаименование] FROM [1С].[uat].[РегистрСведений_уатСостояниеТС_Срез];
GO

PRINT 'Probe 7';
GO
SELECT TOP (0) [ТС], [Период], [ПериодLast], [ПодразделениеНаименование] FROM [1С].[uat].[РегистрСведений_уатМестонахождениеТС_Срез];
GO

PRINT 'Probe 8';
GO
SELECT TOP (0) [СсылкаСсылка], [ГруппыОС], [ЗаводскойНомер], [Код], [Наименование] FROM [1С].[dbo].[Справочник_ОсновныеСредства];
GO

PRINT 'Probe 9';
GO
SELECT TOP (0) [Инвентарный номер], [Группа 1], [Группа 2], [Марка] FROM [DataWH].[dbo].[OSRefExcel];
GO

PRINT 'Probe 10';
GO
SELECT TOP (0) [ОсновноеСредствоНаименование], [СсылкаСсылка], [ГосударственныйНомер], [ВидМоделиТСНаименование], [ТипТСНаименование], [КолоннаНаименование] FROM [1С].[uat].[Справочник_уатТС];
GO

PRINT 'Probe 11';
GO
SELECT TOP (0) [СсылкаСсылка], [Родитель2Наименование], [Группа2] FROM [1С].[dbo].[vТСВагонДомаГруппы];
GO

PRINT 'Direct permissions (ownership chaining may differ inside a procedure)';
GO
SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE') AS CanCreateTable,
       HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'ALTER') AS CanAlterReportSchema,
       HAS_PERMS_BY_NAME(N'[report].[sp_ВедомостьАрмотизацииОСПериод]', 'OBJECT', 'EXECUTE') AS CanExecuteReport;
GO
