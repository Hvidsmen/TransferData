/* Execute on vm-dwh, DataWH. SQL Server 2016 SP1 or later.
   Creates an isolated trial only. Never changes Agent, ERP, 1CUnloading or erp.*.
   Installation does NOT read source rows. See docs/PRICE_TRIAL.md before running.
*/
USE [DataWH];
GO
IF SCHEMA_ID(N'price_trial') IS NOT NULL
   AND OBJECT_ID(N'price_trial.Installation', N'U') IS NULL
    THROW 51000, 'price_trial already exists without the trial marker. Stop.', 1;
IF SCHEMA_ID(N'price_trial') IS NULL
    EXEC(N'CREATE SCHEMA price_trial AUTHORIZATION dbo');
GO
IF OBJECT_ID(N'price_trial.Installation', N'U') IS NULL
BEGIN
    CREATE TABLE price_trial.Installation (Version int NOT NULL);
    INSERT price_trial.Installation VALUES (1);
END;
IF (SELECT COUNT(*) FROM price_trial.Installation) <> 1
   OR NOT EXISTS (SELECT 1 FROM price_trial.Installation WHERE Version = 1)
    THROW 51000, 'Unexpected price trial version.', 1;
IF OBJECT_ID(N'price_trial.Runs', N'U') IS NULL
    CREATE TABLE price_trial.Runs (
        RunId uniqueidentifier NOT NULL PRIMARY KEY,
        StartedAt datetime2 NOT NULL,
        CompletedAt datetime2 NULL,
        Status varchar(16) NOT NULL,
        SourceRows bigint NULL,
        ErrorNumber int NULL
    );
IF OBJECT_ID(N'price_trial.Phases', N'U') IS NULL
    CREATE TABLE price_trial.Phases (
        RunId uniqueidentifier NOT NULL,
        Phase varchar(40) NOT NULL,
        StartedAt datetime2 NOT NULL,
        CompletedAt datetime2 NOT NULL,
        PRIMARY KEY (RunId, Phase)
    );
GO
/* Internal application procedure. The caller supplies session-local #Raw,
   #Nomen, #Currencies, #Kinds. This seam also permits a local fixture test.
   Use Refresh for the real source. Only SELECT is performed outside price_trial.
*/
CREATE OR ALTER PROCEDURE price_trial.ApplyStage
    @AsOf datetime,
    @RunId uniqueidentifier = NULL,
    @AllowEmpty bit = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @@TRANCOUNT <> 0
        THROW 51000, 'Run ApplyStage outside an existing transaction.', 1;
    IF @AsOf IS NULL
        THROW 51000, 'AsOf must be supplied.', 1;
    DECLARE @lock int, @phase datetime2 = SYSDATETIME();
    EXEC @lock = sys.sp_getapplock @Resource = N'price_trial.Refresh',
        @LockMode = 'Exclusive', @LockOwner = 'Session', @LockTimeout = 0;
    IF @lock < 0 THROW 51000, 'Another price trial is running.', 1;
    BEGIN TRY
        IF ISNULL(@AllowEmpty, 0) = 0 AND NOT EXISTS (SELECT 1 FROM #Raw)
            THROW 51000, 'Empty source rejected; previous trial state preserved.', 1;
        IF EXISTS (SELECT 1 FROM #Raw WHERE _Period IS NULL
            OR _Fld2951 IS NULL OR _RecorderTRef IS NULL
            OR _RecorderRRef IS NULL OR _LineNo IS NULL)
            THROW 51000, 'Null source period or key rejected.', 1;
        /* Original joins use reference alone. Do not guess a tenant join and
           silently change results when a dictionary reference is ambiguous. */
        IF EXISTS (SELECT 1 FROM #Nomen GROUP BY [Ссылка] HAVING COUNT_BIG(*) > 1)
           OR EXISTS (SELECT 1 FROM #Currencies GROUP BY [Ссылка] HAVING COUNT_BIG(*) > 1)
           OR EXISTS (SELECT 1 FROM #Kinds GROUP BY [Ссылка] HAVING COUNT_BIG(*) > 1)
            THROW 51000, 'Dictionary reference is not unique; review legacy joins before trial.', 1;
        IF NOT EXISTS (SELECT 1 FROM tempdb.sys.indexes
            WHERE object_id = OBJECT_ID(N'tempdb..#Raw') AND name = N'TrialRawKey')
        CREATE UNIQUE CLUSTERED INDEX TrialRawKey ON #Raw
            (_Fld2951, _RecorderTRef, _RecorderRRef, _LineNo);
        IF NOT EXISTS (SELECT 1 FROM tempdb.sys.indexes
            WHERE object_id = OBJECT_ID(N'tempdb..#Nomen') AND name = N'TrialNomenKey')
            CREATE UNIQUE CLUSTERED INDEX TrialNomenKey ON #Nomen ([Ссылка]);
        IF NOT EXISTS (SELECT 1 FROM tempdb.sys.indexes
            WHERE object_id = OBJECT_ID(N'tempdb..#Currencies') AND name = N'TrialCurrencyKey')
            CREATE UNIQUE CLUSTERED INDEX TrialCurrencyKey ON #Currencies ([Ссылка]);
        IF NOT EXISTS (SELECT 1 FROM tempdb.sys.indexes
            WHERE object_id = OBJECT_ID(N'tempdb..#Kinds') AND name = N'TrialKindKey')
            CREATE UNIQUE CLUSTERED INDEX TrialKindKey ON #Kinds ([Ссылка]);

        BEGIN TRANSACTION;
        IF OBJECT_ID(N'price_trial.Raw', N'U') IS NULL
        BEGIN
            SELECT TOP (0) * INTO price_trial.Raw FROM #Raw;
            CREATE UNIQUE CLUSTERED INDEX RawKey ON price_trial.Raw
                (_Fld2951, _RecorderTRef, _RecorderRRef, _LineNo);
            SELECT TOP (0) * INTO price_trial.Nomen FROM #Nomen;
            SELECT TOP (0) * INTO price_trial.Currencies FROM #Currencies;
            SELECT TOP (0) * INTO price_trial.Kinds FROM #Kinds;
        END;
        /* Exact comparison, no hash collision risk. Both sides detect deletes.
           The first run treats every source record as new. */
        SELECT _Fld2951, _RecorderTRef, _RecorderRRef, _LineNo
        INTO #Changed
        FROM (
            SELECT * FROM #Raw
            EXCEPT SELECT * FROM price_trial.Raw
        ) AS added_or_updated
        UNION
        SELECT _Fld2951, _RecorderTRef, _RecorderRRef, _LineNo
        FROM (
            SELECT * FROM price_trial.Raw
            EXCEPT SELECT * FROM #Raw
        ) AS deleted_or_updated;

        SELECT [Ссылка] INTO #ChangedNomen FROM (
            SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) AS NameBytes FROM #Nomen
            EXCEPT SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) FROM price_trial.Nomen
        ) AS n
        UNION SELECT [Ссылка] FROM (
            SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) AS NameBytes FROM price_trial.Nomen
            EXCEPT SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) FROM #Nomen
        ) AS n;
        SELECT [Ссылка] INTO #ChangedCurrencies FROM (
            SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) AS NameBytes FROM #Currencies
            EXCEPT SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) FROM price_trial.Currencies
        ) AS n
        UNION SELECT [Ссылка] FROM (
            SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) AS NameBytes FROM price_trial.Currencies
            EXCEPT SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) FROM #Currencies
        ) AS n;
        SELECT [Ссылка] INTO #ChangedKinds FROM (
            SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) AS NameBytes FROM #Kinds
            EXCEPT SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) FROM price_trial.Kinds
        ) AS n
        UNION SELECT [Ссылка] FROM (
            SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) AS NameBytes FROM price_trial.Kinds
            EXCEPT SELECT [Ссылка], CONVERT(varbinary(max), [Наименование]) FROM #Kinds
        ) AS n;

        /* Re-enrich records whose dictionary presentation changed as well. */
        SELECT _Fld2951, _RecorderTRef, _RecorderRRef, _LineNo INTO #Keys
        FROM #Changed
        UNION
        SELECT r._Fld2951, r._RecorderTRef, r._RecorderRRef, r._LineNo
        FROM #Raw AS r
        WHERE EXISTS (SELECT 1 FROM #ChangedNomen AS n WHERE n.[Ссылка] = r._Fld48936RRef)
           OR EXISTS (SELECT 1 FROM #ChangedCurrencies AS n WHERE n.[Ссылка] = r._Fld48941RRef)
           OR EXISTS (SELECT 1 FROM #ChangedKinds AS n WHERE n.[Ссылка] = r._Fld48938RRef);
        CREATE UNIQUE CLUSTERED INDEX ChangedKey ON #Keys
            (_Fld2951, _RecorderTRef, _RecorderRRef, _LineNo);

        SELECT
            CASE WHEN YEAR(r._Period) < 3753 THEN r._Period
                 ELSE DATEADD(year, -2000, r._Period) END AS [Период],
            r._RecorderTRef AS [РегистраторВид],
            r._RecorderRRef AS [РегистраторСсылка],
            r._LineNo AS [НомерСтроки], r._Active AS [Активность],
            r._Fld48936RRef AS [НоменклатураСсылка], n.[Наименование] AS [Номенклатура],
            r._Fld48937RRef AS [Характеристика],
            r._Fld48938RRef AS [ВидЦеныСсылка], k.[Наименование] AS [ВидЦены],
            r._Fld48939 AS [Цена], r._Fld48940RRef AS [Упаковка],
            r._Fld48941RRef AS [ВалютаСсылка], c.[Наименование] AS [Валюта],
            r._Fld2951 AS [ОбластьДанныхОсновныеДанные]
        INTO #NewPrices
        FROM #Raw AS r
        JOIN #Keys AS x ON x._Fld2951 = r._Fld2951
            AND x._RecorderTRef = r._RecorderTRef
            AND x._RecorderRRef = r._RecorderRRef AND x._LineNo = r._LineNo
        LEFT JOIN #Nomen AS n ON n.[Ссылка] = r._Fld48936RRef
        LEFT JOIN #Currencies AS c ON c.[Ссылка] = r._Fld48941RRef
        LEFT JOIN #Kinds AS k ON k.[Ссылка] = r._Fld48938RRef;

        IF OBJECT_ID(N'price_trial.Prices', N'U') IS NULL
        BEGIN
            SELECT TOP (0) * INTO price_trial.Prices FROM #NewPrices;
            CREATE UNIQUE CLUSTERED INDEX PriceKey ON price_trial.Prices
                ([ОбластьДанныхОсновныеДанные], [РегистраторВид], [РегистраторСсылка], [НомерСтроки]);
            SELECT TOP (0) p.*, p.[Период] AS DateStart,
                LEAD(DATEADD(day, -1, p.[Период]), 1, DATEADD(year, 10, @AsOf))
                    OVER (PARTITION BY p.[Номенклатура], p.[ВидЦены] ORDER BY p.[Период]) AS DateEND,
                CAST(0 AS bit) AS __etl_terminal
            INTO price_trial.Intervals FROM price_trial.Prices AS p;
            CREATE INDEX TerminalRows ON price_trial.Intervals (__etl_terminal)
                WHERE __etl_terminal = 1;
        END;
        SELECT p.[Номенклатура], p.[ВидЦены] INTO #Groups
        FROM price_trial.Prices AS p
        JOIN #Keys AS x ON x._Fld2951 = p.[ОбластьДанныхОсновныеДанные]
            AND x._RecorderTRef = p.[РегистраторВид]
            AND x._RecorderRRef = p.[РегистраторСсылка] AND x._LineNo = p.[НомерСтроки]
        UNION SELECT [Номенклатура], [ВидЦены] FROM #NewPrices;

        DELETE p FROM price_trial.Prices AS p
        JOIN #Keys AS x ON x._Fld2951 = p.[ОбластьДанныхОсновныеДанные]
            AND x._RecorderTRef = p.[РегистраторВид]
            AND x._RecorderRRef = p.[РегистраторСсылка] AND x._LineNo = p.[НомерСтроки];
        INSERT price_trial.Prices SELECT * FROM #NewPrices;

        IF @RunId IS NOT NULL
            INSERT price_trial.Phases VALUES (@RunId, 'compare_and_enrich', @phase, SYSDATETIME());
        SET @phase = SYSDATETIME();
        DELETE p FROM price_trial.Intervals AS p
        WHERE EXISTS (SELECT 1 FROM #Groups AS g
            WHERE (g.[Номенклатура] = p.[Номенклатура]
                OR (g.[Номенклатура] IS NULL AND p.[Номенклатура] IS NULL))
              AND (g.[ВидЦены] = p.[ВидЦены]
                OR (g.[ВидЦены] IS NULL AND p.[ВидЦены] IS NULL)));
        /* Preserve the ORIGINAL partitioning and ordering. Equal periods are
           ambiguous in the original too; do not invent a new price priority. */
        ;WITH w AS (
            SELECT p.*, LEAD(p.[Период]) OVER (
                PARTITION BY p.[Номенклатура], p.[ВидЦены] ORDER BY p.[Период]
            ) AS NextPeriod
            FROM price_trial.Prices AS p
            WHERE EXISTS (SELECT 1 FROM #Groups AS g
                WHERE (g.[Номенклатура] = p.[Номенклатура]
                    OR (g.[Номенклатура] IS NULL AND p.[Номенклатура] IS NULL))
                  AND (g.[ВидЦены] = p.[ВидЦены]
                    OR (g.[ВидЦены] IS NULL AND p.[ВидЦены] IS NULL)))
        )
        INSERT price_trial.Intervals
        SELECT [Период], [РегистраторВид], [РегистраторСсылка], [НомерСтроки],
            [Активность], [НоменклатураСсылка], [Номенклатура], [Характеристика],
            [ВидЦеныСсылка], [ВидЦены], [Цена], [Упаковка], [ВалютаСсылка], [Валюта],
            [ОбластьДанныхОсновныеДанные], [Период],
            CASE WHEN NextPeriod IS NULL THEN DATEADD(year, 10, @AsOf)
                 ELSE DATEADD(day, -1, NextPeriod) END,
            CAST(CASE WHEN NextPeriod IS NULL THEN 1 ELSE 0 END AS bit)
        FROM w;
        /* Legacy refresh changes this moving horizon even in unchanged groups. */
        UPDATE price_trial.Intervals SET DateEND = DATEADD(year, 10, @AsOf)
        WHERE __etl_terminal = 1;
        IF @RunId IS NOT NULL
            INSERT price_trial.Phases VALUES (@RunId, 'rebuild_intervals', @phase, SYSDATETIME());
        SET @phase = SYSDATETIME();

        DELETE r FROM price_trial.Raw AS r
        JOIN #Changed AS x ON x._Fld2951 = r._Fld2951
            AND x._RecorderTRef = r._RecorderTRef
            AND x._RecorderRRef = r._RecorderRRef AND x._LineNo = r._LineNo;
        INSERT price_trial.Raw
        SELECT r.* FROM #Raw AS r
        JOIN #Changed AS x ON x._Fld2951 = r._Fld2951
            AND x._RecorderTRef = r._RecorderTRef
            AND x._RecorderRRef = r._RecorderRRef AND x._LineNo = r._LineNo;
        DELETE FROM price_trial.Nomen;
        INSERT price_trial.Nomen SELECT * FROM #Nomen;
        DELETE FROM price_trial.Currencies;
        INSERT price_trial.Currencies SELECT * FROM #Currencies;
        DELETE FROM price_trial.Kinds;
        INSERT price_trial.Kinds SELECT * FROM #Kinds;
        IF OBJECT_ID(N'price_trial.State', N'U') IS NULL
            CREATE TABLE price_trial.State (AsOf datetime NOT NULL);
        DELETE FROM price_trial.State;
        INSERT price_trial.State VALUES (@AsOf);
        IF @RunId IS NOT NULL
            INSERT price_trial.Phases VALUES (@RunId, 'save_state', @phase, SYSDATETIME());
        COMMIT TRANSACTION;
        SELECT (SELECT COUNT_BIG(*) FROM #Raw) AS SourceRows,
            (SELECT COUNT_BIG(*) FROM #Changed) AS RawChangedKeys,
            (SELECT COUNT_BIG(*) FROM #Keys) AS EnrichedChangedKeys,
            (SELECT COUNT_BIG(*) FROM #Groups) AS RebuiltGroups;
        EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE price_trial.Refresh
    @AllowFullSourceRead bit = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF ISNULL(@AllowFullSourceRead, 0) <> 1
        THROW 51000, 'Explicit AllowFullSourceRead=1 is required. Read PRICE_TRIAL.md.', 1;
    IF @@TRANCOUNT <> 0
        THROW 51000, 'Run Refresh outside an existing transaction.', 1;
    DECLARE @lock int, @run uniqueidentifier = NEWID(), @phase datetime2 = SYSDATETIME(),
        @asof datetime = GETDATE();
    EXEC @lock = sys.sp_getapplock @Resource = N'price_trial.Refresh',
        @LockMode = 'Exclusive', @LockOwner = 'Session', @LockTimeout = 0;
    IF @lock < 0 THROW 51000, 'Another price trial is running.', 1;
    BEGIN TRY
        INSERT price_trial.Runs (RunId, StartedAt, Status) VALUES (@run, @phase, 'running');
        SELECT _Period, _RecorderTRef, _RecorderRRef, _LineNo, _Active,
            _Fld48936RRef, _Fld48937RRef, _Fld48938RRef,
            _Fld48939, _Fld48940RRef, _Fld48941RRef, _Fld2951
        INTO #Raw FROM [erp-db-02.erp.lcl].[ERPBAckUp2].dbo._InfoRg48935;
        INSERT price_trial.Phases VALUES (@run, 'read_raw', @phase, SYSDATETIME());
        UPDATE price_trial.Runs SET SourceRows = (SELECT COUNT_BIG(*) FROM #Raw) WHERE RunId = @run;
        SET @phase = SYSDATETIME();
        SELECT [Ссылка], [Наименование] INTO #Nomen
        FROM [1CUnloading].[1c].[vСправочники.Номенклатура];
        SELECT [Ссылка], [Наименование] INTO #Currencies
        FROM [erp-db-02.erp.lcl].[exch_biview2].dbo.[Справочники.Валюты];
        SELECT [Ссылка], [Наименование] INTO #Kinds
        FROM [1CUnloading].[1c].[Справочники.ВидыЦен];
        INSERT price_trial.Phases VALUES (@run, 'read_dictionaries', @phase, SYSDATETIME());
        EXEC price_trial.ApplyStage @AsOf = @asof, @RunId = @run;
        UPDATE price_trial.Runs SET Status = 'success', CompletedAt = SYSDATETIME() WHERE RunId = @run;
        EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
        SELECT @run AS RunId;
        SELECT Phase, DATEDIFF_BIG(millisecond, StartedAt, CompletedAt) / 60000.0 AS Minutes
        FROM price_trial.Phases WHERE RunId = @run ORDER BY StartedAt;
    END TRY
    BEGIN CATCH
        UPDATE price_trial.Runs SET Status = 'failed', CompletedAt = SYSDATETIME(),
            ErrorNumber = ERROR_NUMBER() WHERE RunId = @run;
        EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
        THROW;
    END CATCH;
END;
GO
PRINT 'Price trial installed. No source data read. No Agent jobs changed.';
