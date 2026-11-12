/* Read-only for permanent objects. Full comparison uses substantial tempdb,
   CPU and disk. Run outside the morning window against frozen source versions.
   Compares to the legacy interval FORMULA at the trial AsOf, because GETDATE
   makes the last interval differ between separate executions by design. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF OBJECT_ID(N'price_trial.State', N'U') IS NULL
    THROW 51000, 'No completed price trial found.', 1;
DECLARE @lock int;
EXEC @lock = sys.sp_getapplock @Resource = N'price_trial.Refresh',
    @LockMode = 'Shared', @LockOwner = 'Session', @LockTimeout = 0;
IF @lock < 0 THROW 51000, 'Price trial is running. Compare later.', 1;
BEGIN TRY
    DECLARE @asof datetime = (SELECT AsOf FROM price_trial.State);
    SELECT p.*, p.[Период] AS DateStart,
        LEAD(DATEADD(day, -1, p.[Период]), 1, DATEADD(year, 10, @asof))
            OVER (PARTITION BY p.[Номенклатура], p.[ВидЦены] ORDER BY p.[Период]) AS DateEND
    INTO #ExpectedIntervals
    FROM erp.[РегистрСведений.ЦеныНоменклатуры] AS p;

    CREATE TABLE #Results (Comparison varchar(30), MissingGroups bigint, ExtraGroups bigint);
    DECLARE @columns nvarchar(max), @projection nvarchar(max), @sql nvarchar(max);
    /* Binary presentation comparison also detects case/trailing-space changes.
       COUNT_BIG per tuple catches duplicate multiplicity; EXCEPT alone would not. */
    SET @columns = N'[Период],[РегистраторВид],[РегистраторСсылка],[НомерСтроки],
        [Активность],[НоменклатураСсылка],CONVERT(varbinary(max),[Номенклатура]),
        [Характеристика],[ВидЦеныСсылка],CONVERT(varbinary(max),[ВидЦены]),[Цена],
        [Упаковка],[ВалютаСсылка],CONVERT(varbinary(max),[Валюта]),[ОбластьДанныхОсновныеДанные]';
    SET @projection = REPLACE(REPLACE(REPLACE(@columns,
        N'CONVERT(varbinary(max),[Номенклатура])', N'CONVERT(varbinary(max),[Номенклатура]) AS [Номенклатура]'),
        N'CONVERT(varbinary(max),[ВидЦены])', N'CONVERT(varbinary(max),[ВидЦены]) AS [ВидЦены]'),
        N'CONVERT(varbinary(max),[Валюта])', N'CONVERT(varbinary(max),[Валюта]) AS [Валюта]');
    SET @sql = N';WITH a AS (SELECT ' + @projection + N',COUNT_BIG(*) AS copies
        FROM erp.[РегистрСведений.ЦеныНоменклатуры] GROUP BY ' + @columns + N'),
        b AS (SELECT ' + @projection + N',COUNT_BIG(*) AS copies
        FROM price_trial.Prices GROUP BY ' + @columns + N')
        INSERT #Results SELECT ''enriched_prices'',
        (SELECT COUNT_BIG(*) FROM (SELECT * FROM a EXCEPT SELECT * FROM b) d),
        (SELECT COUNT_BIG(*) FROM (SELECT * FROM b EXCEPT SELECT * FROM a) d);';
    EXEC sys.sp_executesql @sql;
    SET @columns = @columns + N',[DateStart],[DateEND]';
    SET @projection = @projection + N',[DateStart],[DateEND]';
    SET @sql = N';WITH a AS (SELECT ' + @projection + N',COUNT_BIG(*) AS copies
        FROM #ExpectedIntervals GROUP BY ' + @columns + N'),
        b AS (SELECT ' + @projection + N',COUNT_BIG(*) AS copies
        FROM price_trial.Intervals GROUP BY ' + @columns + N')
        INSERT #Results SELECT ''legacy_interval_formula'',
        (SELECT COUNT_BIG(*) FROM (SELECT * FROM a EXCEPT SELECT * FROM b) d),
        (SELECT COUNT_BIG(*) FROM (SELECT * FROM b EXCEPT SELECT * FROM a) d);';
    EXEC sys.sp_executesql @sql;
    DECLARE @ties bigint = (
        SELECT COUNT_BIG(*) FROM (
            SELECT [Номенклатура], [ВидЦены], [Период]
            FROM erp.[РегистрСведений.ЦеныНоменклатуры]
            GROUP BY [Номенклатура], [ВидЦены], [Период]
            HAVING COUNT_BIG(*) > 1
        ) AS t
    );
    SELECT * FROM #Results;
    SELECT @asof AS ComparedAsOf, @ties AS AmbiguousPeriodGroups,
        CASE WHEN @ties > 0 THEN 'REVIEW: legacy LEAD order is ambiguous'
             WHEN EXISTS (SELECT 1 FROM #Results WHERE MissingGroups <> 0 OR ExtraGroups <> 0)
             THEN 'DIFFERENCES: do not switch production'
             ELSE 'MATCH: performance and source consistency still require review' END AS Result;
    EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
END TRY
BEGIN CATCH
    EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
    THROW;
END CATCH;
