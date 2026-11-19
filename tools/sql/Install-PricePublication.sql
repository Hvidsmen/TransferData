/* vm-dwh / DataWH. Install after the updated Install-PriceTrial.sql.
   Installation does not publish data, change Agent or read the ERP register.
   Read docs/PRICE_CUTOVER.md. ApplyStage must contain the pending-delta hook. */
USE [DataWH];
GO
IF OBJECT_ID(N'price_trial.State', N'U') IS NULL
    THROW 51100, 'A completed price trial is required.', 1;
IF OBJECT_DEFINITION(OBJECT_ID(N'price_trial.ApplyStage')) NOT LIKE N'%price_trial.PendingKeys%'
   OR OBJECT_DEFINITION(OBJECT_ID(N'price_trial.ApplyStage')) IS NULL
    THROW 51100, 'Install the updated Install-PriceTrial.sql first.', 1;
IF OBJECT_ID(N'price_trial.PublicationControl', N'U') IS NULL
BEGIN
    CREATE TABLE price_trial.PublicationControl (
        Id int NOT NULL PRIMARY KEY CHECK (Id = 1),
        Active bit NOT NULL,
        JobId uniqueidentifier NULL,
        StepId int NULL,
        OriginalCommand nvarchar(max) NULL,
        NewCommand nvarchar(max) NULL,
        OriginalEnabled bit NULL,
        CurrentCutoverId uniqueidentifier NULL,
        SourceObjectId int NULL,
        PricesObjectId int NULL,
        IntervalsObjectId int NULL,
        LastPublishedAsOf datetime NULL,
        SwitchedAt datetime2 NULL,
        RolledBackAt datetime2 NULL
    );
    INSERT price_trial.PublicationControl (Id, Active) VALUES (1, 0);
END;
IF (SELECT COUNT(*) FROM price_trial.PublicationControl WHERE Id=1)<>1
    THROW 51100,'Publication control row is missing; review before installation.',1;
IF OBJECT_ID(N'price_trial.PendingKeys', N'U') IS NULL
BEGIN
    SELECT TOP (0) _Fld2951, _RecorderTRef, _RecorderRRef, _LineNo
    INTO price_trial.PendingKeys FROM price_trial.Raw;
    CREATE UNIQUE CLUSTERED INDEX PendingPriceKey ON price_trial.PendingKeys
        (_Fld2951, _RecorderTRef, _RecorderRRef, _LineNo);
END;
IF OBJECT_ID(N'price_trial.PendingGroups', N'U') IS NULL
    SELECT TOP (0) [Номенклатура], [ВидЦены]
    INTO price_trial.PendingGroups FROM price_trial.Prices;
IF OBJECT_ID(N'price_trial.Publications', N'U') IS NULL
    CREATE TABLE price_trial.Publications (
        PublicationId uniqueidentifier NOT NULL PRIMARY KEY,
        StartedAt datetime2 NOT NULL, CompletedAt datetime2 NOT NULL,
        PublishedAsOf datetime NOT NULL, PendingKeys bigint NOT NULL,
        PendingGroups bigint NOT NULL
    );
IF OBJECT_ID(N'price_trial.JobStepBackup', N'U') IS NULL
    SELECT TOP (0) CAST(NULL AS uniqueidentifier) AS CutoverId,
        CAST(NULL AS bit) AS EnabledBefore, CAST(NULL AS datetime2) AS SavedAt, s.*
    INTO price_trial.JobStepBackup FROM msdb.dbo.sysjobsteps AS s;
GO
/* Validate exact schemas and refuse side-effecting targets. Index creation is
   opt-in and only used by the switch, never by ordinary installation/refresh. */
CREATE OR ALTER PROCEDURE price_trial.CheckPublicationTargets
    @CreateIndexes bit = 0
AS
BEGIN
    SET NOCOUNT ON;
    IF @CreateIndexes=1 AND (@@TRANCOUNT=0 OR
        ISNULL(APPLOCK_MODE(N'public',N'price_trial.Refresh',N'Session'),N'NoLock')<>N'Exclusive')
        THROW 51100,'Index creation is restricted to the locked cutover transaction.',1;
    DECLARE @db sysname,@schema sysname,@table sysname,@expected sysname,
        @qualified nvarchar(400),@sql nvarchar(max);
    DECLARE targets CURSOR LOCAL FAST_FORWARD FOR
        SELECT * FROM (VALUES
            (N'1CUnloading',N'1c',N'РегистрСведений.ЦеныНоменклатуры',N'Prices'),
            (N'DataWH',N'erp',N'РегистрСведений.ЦеныНоменклатуры',N'Prices'),
            (N'DataWH',N'erp',N'PriceERP',N'Intervals')
        ) AS t(dbname,schemaname,tablename,expected);
    OPEN targets;
    FETCH NEXT FROM targets INTO @db,@schema,@table,@expected;
    WHILE @@FETCH_STATUS=0
    BEGIN
        SET @qualified=QUOTENAME(@db)+N'.'+QUOTENAME(@schema)+N'.'+QUOTENAME(@table);
        SET @sql=N'USE '+QUOTENAME(@db)+N';
            DECLARE @oid int=OBJECT_ID(@name,N''U'');
            IF @oid IS NULL THROW 51100,''A production price table is missing.'',1;
            IF EXISTS (SELECT 1 FROM sys.triggers WHERE parent_id=@oid AND is_disabled=0)
               OR EXISTS (SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=@oid OR referenced_object_id=@oid)
               OR EXISTS (SELECT 1 FROM sys.security_predicates WHERE target_object_id=@oid)
               OR EXISTS (SELECT 1 FROM sys.tables WHERE object_id=@oid
                    AND (temporal_type<>0 OR is_tracked_by_cdc=1 OR is_replicated=1))
               OR EXISTS (SELECT 1 FROM sys.change_tracking_tables WHERE object_id=@oid)
                THROW 51100,''Price table has triggers, FK, RLS, temporal, CDC, CT or replication; review first.'',1;
            SELECT column_id,name COLLATE DATABASE_DEFAULT AS name,system_type_id,max_length,
                precision,scale,collation_name COLLATE DATABASE_DEFAULT AS collation_name,is_computed,is_identity
            INTO #actual FROM sys.columns WHERE object_id=@oid;
            SELECT column_id,name COLLATE DATABASE_DEFAULT AS name,system_type_id,max_length,
                precision,scale,collation_name COLLATE DATABASE_DEFAULT AS collation_name,is_computed,is_identity
            INTO #expected FROM DataWH.sys.columns
            WHERE object_id=OBJECT_ID(N''DataWH.price_trial.''+QUOTENAME(@expected))
              AND name<>N''__etl_terminal'';
            IF NOT EXISTS (SELECT 1 FROM #expected)
               OR EXISTS (SELECT * FROM #actual EXCEPT SELECT * FROM #expected)
               OR EXISTS (SELECT * FROM #expected EXCEPT SELECT * FROM #actual)
                THROW 51100,''Production/trial column schemas differ.'',1;
            IF @create=1
            BEGIN
                IF EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=@oid AND name=N''IX_ETLPricePublicationKey'')
                   AND NOT EXISTS (
                    SELECT 1 FROM sys.indexes AS i WHERE i.object_id=@oid
                      AND i.name=N''IX_ETLPricePublicationKey'' AND i.is_unique=1
                      AND i.is_disabled=0 AND i.has_filter=0
                      AND (SELECT COUNT(*) FROM sys.index_columns WHERE object_id=@oid
                           AND index_id=i.index_id AND key_ordinal>0)=4
                      AND NOT EXISTS (
                        SELECT 1 FROM sys.index_columns AS ic JOIN sys.columns AS c
                          ON c.object_id=ic.object_id AND c.column_id=ic.column_id
                        WHERE ic.object_id=@oid AND ic.index_id=i.index_id AND ic.key_ordinal>0
                          AND c.name<>CASE ic.key_ordinal WHEN 1 THEN N''ОбластьДанныхОсновныеДанные''
                            WHEN 2 THEN N''РегистраторВид'' WHEN 3 THEN N''РегистраторСсылка''
                            WHEN 4 THEN N''НомерСтроки'' END))
                    THROW 51100,''Publication key index has an unexpected definition.'',1;
                IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=@oid AND name=N''IX_ETLPricePublicationKey'')
                    CREATE UNIQUE NONCLUSTERED INDEX IX_ETLPricePublicationKey ON '+@qualified+N'
                        ([ОбластьДанныхОсновныеДанные],[РегистраторВид],[РегистраторСсылка],[НомерСтроки]);
            END;';
        EXEC sys.sp_executesql @sql,N'@name nvarchar(400),@expected sysname,@create bit',
            @name=@qualified,@expected=@expected,@create=@CreateIndexes;
        FETCH NEXT FROM targets INTO @db,@schema,@table,@expected;
    END;
    CLOSE targets;
    DEALLOCATE targets;
END;
GO
/* Called inside the switch transaction while its applock and shared table locks
   freeze all four local versions. Full multiset comparison; no ERP read. */
CREATE OR ALTER PROCEDURE price_trial.VerifyForPublication
    @VerifiedAsOf datetime OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    IF @@TRANCOUNT=0 THROW 51100,'Verification requires a caller transaction.',1;
    IF ISNULL(APPLOCK_MODE(N'public',N'price_trial.Refresh',N'Session'),N'NoLock')<>N'Exclusive'
        THROW 51100,'Verification requires the cutover lock.',1;
    SET @VerifiedAsOf=(SELECT AsOf FROM price_trial.State);
    IF @VerifiedAsOf IS NULL THROW 51100,'No trial state.',1;
    DECLARE @dummy binary(16);
    SELECT TOP (1) @dummy=[РегистраторСсылка]
        FROM [1CUnloading].[1c].[РегистрСведений.ЦеныНоменклатуры] WITH (TABLOCK,HOLDLOCK);
    SELECT TOP (1) @dummy=[РегистраторСсылка]
        FROM erp.[РегистрСведений.ЦеныНоменклатуры] WITH (TABLOCK,HOLDLOCK);
    SELECT TOP (1) @dummy=[РегистраторСсылка] FROM erp.PriceERP WITH (TABLOCK,HOLDLOCK);
    SELECT [ОбластьДанныхОсновныеДанные],[РегистраторВид],[РегистраторСсылка],
        [НомерСтроки],DateEND INTO #terminals FROM price_trial.Intervals WHERE __etl_terminal=1;
    CREATE UNIQUE CLUSTERED INDEX TerminalKey ON #terminals
        ([ОбластьДанныхОсновныеДанные],[РегистраторВид],[РегистраторСсылка],[НомерСтроки]);
    /* Only the moving last endpoint is canonicalized. Nonterminal endpoints,
       DateStart, values and duplicate multiplicities must match exactly. */
    IF EXISTS (SELECT 1 FROM erp.PriceERP AS p JOIN #terminals AS t
        ON t.[ОбластьДанныхОсновныеДанные]=p.[ОбластьДанныхОсновныеДанные]
       AND t.[РегистраторВид]=p.[РегистраторВид] AND t.[РегистраторСсылка]=p.[РегистраторСсылка]
       AND t.[НомерСтроки]=p.[НомерСтроки]
        WHERE p.DateEND IS NULL OR p.DateEND<DATEADD(year,9,@VerifiedAsOf)
            OR p.DateEND>DATEADD(year,11,@VerifiedAsOf))
        THROW 51100,'Unexpected terminal endpoint in production.',1;
    SELECT p.[Период],p.[РегистраторВид],p.[РегистраторСсылка],p.[НомерСтроки],
        p.[Активность],p.[НоменклатураСсылка],p.[Номенклатура],p.[Характеристика],
        p.[ВидЦеныСсылка],p.[ВидЦены],p.[Цена],p.[Упаковка],p.[ВалютаСсылка],p.[Валюта],
        p.[ОбластьДанныхОсновныеДанные],p.DateStart,
        CASE WHEN t.[РегистраторСсылка] IS NULL THEN p.DateEND ELSE t.DateEND END AS DateEND
    INTO #canonical FROM erp.PriceERP AS p LEFT JOIN #terminals AS t
        ON t.[ОбластьДанныхОсновныеДанные]=p.[ОбластьДанныхОсновныеДанные]
       AND t.[РегистраторВид]=p.[РегистраторВид] AND t.[РегистраторСсылка]=p.[РегистраторСсылка]
       AND t.[НомерСтроки]=p.[НомерСтроки];
    CREATE TABLE #differences (TargetName nvarchar(200),MissingGroups bigint,ExtraGroups bigint);
    DECLARE @columns nvarchar(max)=N'[Период],[РегистраторВид],[РегистраторСсылка],[НомерСтроки],
        [Активность],[НоменклатураСсылка],CONVERT(varbinary(max),[Номенклатура]),
        [Характеристика],[ВидЦеныСсылка],CONVERT(varbinary(max),[ВидЦены]),[Цена],
        [Упаковка],[ВалютаСсылка],CONVERT(varbinary(max),[Валюта]),[ОбластьДанныхОсновныеДанные]',
        @projection nvarchar(max),@sql nvarchar(max),@target nvarchar(300),@expected nvarchar(300);
    SET @projection=REPLACE(REPLACE(REPLACE(@columns,
        N'CONVERT(varbinary(max),[Номенклатура])',N'CONVERT(varbinary(max),[Номенклатура]) AS [Номенклатура]'),
        N'CONVERT(varbinary(max),[ВидЦены])',N'CONVERT(varbinary(max),[ВидЦены]) AS [ВидЦены]'),
        N'CONVERT(varbinary(max),[Валюта])',N'CONVERT(varbinary(max),[Валюта]) AS [Валюта]');
    DECLARE targets CURSOR LOCAL FAST_FORWARD FOR SELECT * FROM (VALUES
        (N'[1CUnloading].[1c].[РегистрСведений.ЦеныНоменклатуры]',N'price_trial.Prices'),
        (N'erp.[РегистрСведений.ЦеныНоменклатуры]',N'price_trial.Prices'),
        (N'#canonical',N'price_trial.Intervals')) AS t(actual,expected)
        ORDER BY CASE WHEN actual=N'#canonical' THEN 1 ELSE 0 END;
    OPEN targets;
    FETCH NEXT FROM targets INTO @target,@expected;
    WHILE @@FETCH_STATUS=0
    BEGIN
        IF @target=N'#canonical'
        BEGIN
            SET @columns=@columns+N',[DateStart],[DateEND]';
            SET @projection=@projection+N',[DateStart],[DateEND]';
        END;
        SET @sql=N';WITH a AS (SELECT '+@projection+N',COUNT_BIG(*) AS copies FROM '+@target+N' GROUP BY '+@columns+N'),
            b AS (SELECT '+@projection+N',COUNT_BIG(*) AS copies FROM '+@expected+N' GROUP BY '+@columns+N')
            INSERT #differences SELECT @label,
            (SELECT COUNT_BIG(*) FROM (SELECT * FROM a EXCEPT SELECT * FROM b) d),
            (SELECT COUNT_BIG(*) FROM (SELECT * FROM b EXCEPT SELECT * FROM a) d);';
        EXEC sys.sp_executesql @sql,N'@label nvarchar(200)',@label=@target;
        FETCH NEXT FROM targets INTO @target,@expected;
    END;
    CLOSE targets;
    DEALLOCATE targets;
    SELECT * FROM #differences;
    IF EXISTS (SELECT 1 FROM #differences WHERE MissingGroups<>0 OR ExtraGroups<>0)
        THROW 51100,'Publication baseline differs; job command was not switched.',1;
    IF EXISTS (SELECT [Номенклатура],[ВидЦены],[Период] FROM price_trial.Prices
        GROUP BY [Номенклатура],[ВидЦены],[Период] HAVING COUNT_BIG(*)>1)
        THROW 51100,'Ambiguous periods; job command was not switched.',1;
END;
GO
/* Shared DML engine. Reads caller-owned #Publish* staging and requires a caller
   transaction. Fixture=1 ONLY targets session-local #Target* tables, so the same
   real DML can be exercised without changing production or the live trial. */
CREATE OR ALTER PROCEDURE price_trial.PublishBatch
    @Fixture bit = 0,
    @InjectFixtureFailure bit = 0
AS
BEGIN
    SET NOCOUNT ON;
    IF @@TRANCOUNT = 0 THROW 51100, 'PublishBatch requires a transaction.', 1;
    IF ISNULL(@Fixture, 0) = 0 AND ISNULL(@InjectFixtureFailure, 0) <> 0
        THROW 51100, 'Failure injection is fixture-only.', 1;
    IF ISNULL(@Fixture,0)=0 AND NOT EXISTS (
        SELECT 1 FROM price_trial.PublicationControl WHERE Id=1 AND Active=1)
        THROW 51100,'Publication is not active.',1;
    IF ISNULL(@Fixture,0)=0
       AND ISNULL(APPLOCK_MODE(N'public',N'price_trial.Refresh',N'Session'),N'NoLock')<>N'Exclusive'
        THROW 51100,'Publication requires the refresh lock.',1;
    DECLARE @source nvarchar(300), @prices nvarchar(300), @intervals nvarchar(300),
        @cols nvarchar(max), @sql nvarchar(max);
    IF @Fixture = 1
        SELECT @source = N'#TargetSource', @prices = N'#TargetPrices', @intervals = N'#TargetIntervals';
    ELSE
        SELECT @source = N'[1CUnloading].[1c].[РегистрСведений.ЦеныНоменклатуры]',
            @prices = N'[DataWH].[erp].[РегистрСведений.ЦеныНоменклатуры]',
            @intervals = N'[DataWH].[erp].[PriceERP]';
    IF ISNULL(@Fixture,0)=0
    BEGIN
        /* Hold all targets through commit, then re-check their identity. This
           also catches a legacy DROP/SELECT INTO between staging and writing. */
        DECLARE @dummy binary(16);
        SELECT TOP (1) @dummy=[РегистраторСсылка]
            FROM [1CUnloading].[1c].[РегистрСведений.ЦеныНоменклатуры] WITH (TABLOCK,HOLDLOCK);
        SELECT TOP (1) @dummy=[РегистраторСсылка]
            FROM erp.[РегистрСведений.ЦеныНоменклатуры] WITH (TABLOCK,HOLDLOCK);
        SELECT TOP (1) @dummy=[РегистраторСсылка] FROM erp.PriceERP WITH (TABLOCK,HOLDLOCK);
        IF NOT EXISTS (SELECT 1 FROM price_trial.PublicationControl
            WHERE SourceObjectId=OBJECT_ID(N'[1CUnloading].[1c].[РегистрСведений.ЦеныНоменклатуры]',N'U')
              AND PricesObjectId=OBJECT_ID(N'erp.[РегистрСведений.ЦеныНоменклатуры]',N'U')
              AND IntervalsObjectId=OBJECT_ID(N'erp.PriceERP',N'U'))
            THROW 51100,'Production target identity changed before publication.',1;
    END;
    SET @cols = N'[Период],[РегистраторВид],[РегистраторСсылка],[НомерСтроки],
        [Активность],[НоменклатураСсылка],[Номенклатура],[Характеристика],
        [ВидЦеныСсылка],[ВидЦены],[Цена],[Упаковка],[ВалютаСсылка],[Валюта],
        [ОбластьДанныхОсновныеДанные]';
    IF EXISTS (SELECT 1 FROM #PublishKeys)
    BEGIN
        SET @sql = N'DELETE p FROM ' + @source + N' AS p JOIN #PublishKeys AS k
            ON k._Fld2951=p.[ОбластьДанныхОсновныеДанные]
            AND k._RecorderTRef=p.[РегистраторВид] AND k._RecorderRRef=p.[РегистраторСсылка]
            AND k._LineNo=p.[НомерСтроки];
            INSERT ' + @source + N' (' + @cols + N') SELECT ' + @cols + N' FROM #PublishPrices;';
        EXEC sys.sp_executesql @sql;
        IF @Fixture = 1 AND @InjectFixtureFailure = 1
            THROW 51101, 'Injected publication failure.', 1;
        SET @sql = N'DELETE p FROM ' + @prices + N' AS p JOIN #PublishKeys AS k
            ON k._Fld2951=p.[ОбластьДанныхОсновныеДанные]
            AND k._RecorderTRef=p.[РегистраторВид] AND k._RecorderRRef=p.[РегистраторСсылка]
            AND k._LineNo=p.[НомерСтроки];
            INSERT ' + @prices + N' (' + @cols + N') SELECT ' + @cols + N' FROM #PublishPrices;';
        EXEC sys.sp_executesql @sql;
    END;
    IF EXISTS (SELECT 1 FROM #PublishGroups)
    BEGIN
        SET @sql = N'DELETE p FROM ' + @intervals + N' AS p
            WHERE EXISTS (SELECT 1 FROM #PublishGroups AS g
                WHERE (g.[Номенклатура]=p.[Номенклатура] OR (g.[Номенклатура] IS NULL AND p.[Номенклатура] IS NULL))
                  AND (g.[ВидЦены]=p.[ВидЦены] OR (g.[ВидЦены] IS NULL AND p.[ВидЦены] IS NULL)));
            INSERT ' + @intervals + N' (' + @cols + N',[DateStart],[DateEND])
            SELECT ' + @cols + N',[DateStart],[DateEND] FROM #PublishIntervals;';
        EXEC sys.sp_executesql @sql;
    END;
    /* Refresh's horizon advances even with zero deltas. Never expose the
       internal __etl_terminal flag as a new production column. */
    SET @sql = N'UPDATE p SET DateEND=t.DateEND FROM ' + @intervals + N' AS p
        JOIN #PublishTerminals AS t
          ON t.[ОбластьДанныхОсновныеДанные]=p.[ОбластьДанныхОсновныеДанные]
         AND t.[РегистраторВид]=p.[РегистраторВид]
         AND t.[РегистраторСсылка]=p.[РегистраторСсылка] AND t.[НомерСтроки]=p.[НомерСтроки]
        WHERE p.DateEND <> t.DateEND OR p.DateEND IS NULL;';
    EXEC sys.sp_executesql @sql;
END;
GO
CREATE OR ALTER PROCEDURE price_trial.PublishPending
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @@TRANCOUNT <> 0 THROW 51100, 'Run PublishPending outside a transaction.', 1;
    DECLARE @lock int, @started datetime2 = SYSDATETIME(), @asof datetime;
    EXEC @lock = sys.sp_getapplock @Resource=N'price_trial.Refresh',
        @LockMode='Exclusive', @LockOwner='Session', @LockTimeout=0;
    IF @lock < 0 THROW 51100, 'Another refresh/publication is running.', 1;
    BEGIN TRY
        IF NOT EXISTS (SELECT 1 FROM price_trial.PublicationControl WHERE Id=1 AND Active=1)
            THROW 51100, 'Publication is not active. Run tools/sql/архив/Switch-PriceJob.sql first.', 1;
        IF NOT EXISTS (SELECT 1 FROM price_trial.PublicationControl
            WHERE SourceObjectId=OBJECT_ID(N'[1CUnloading].[1c].[РегистрСведений.ЦеныНоменклатуры]', N'U')
              AND PricesObjectId=OBJECT_ID(N'erp.[РегистрСведений.ЦеныНоменклатуры]', N'U')
              AND IntervalsObjectId=OBJECT_ID(N'erp.PriceERP', N'U'))
            THROW 51100, 'Production table identity changed; revalidation is required.', 1;
        SET @asof = (SELECT AsOf FROM price_trial.State);
        IF @asof IS NULL THROW 51100, 'No completed trial state.', 1;
        SELECT * INTO #PublishKeys FROM price_trial.PendingKeys;
        SELECT DISTINCT [Номенклатура], [ВидЦены] INTO #PublishGroups FROM price_trial.PendingGroups;
        SELECT p.* INTO #PublishPrices FROM price_trial.Prices AS p
        JOIN #PublishKeys AS k ON k._Fld2951=p.[ОбластьДанныхОсновныеДанные]
            AND k._RecorderTRef=p.[РегистраторВид] AND k._RecorderRRef=p.[РегистраторСсылка]
            AND k._LineNo=p.[НомерСтроки];
        SELECT TOP (0) p.[Период],p.[РегистраторВид],p.[РегистраторСсылка],p.[НомерСтроки],
            p.[Активность],p.[НоменклатураСсылка],p.[Номенклатура],p.[Характеристика],
            p.[ВидЦеныСсылка],p.[ВидЦены],p.[Цена],p.[Упаковка],p.[ВалютаСсылка],p.[Валюта],
            p.[ОбластьДанныхОсновныеДанные],p.DateStart,p.DateEND
        INTO #PublishIntervals FROM price_trial.Intervals AS p;
        IF EXISTS (SELECT 1 FROM #PublishGroups)
        BEGIN
            IF EXISTS (
                SELECT p.[Номенклатура],p.[ВидЦены],p.[Период]
                FROM price_trial.Prices AS p
                WHERE EXISTS (SELECT 1 FROM #PublishGroups AS g
                    WHERE (g.[Номенклатура]=p.[Номенклатура] OR (g.[Номенклатура] IS NULL AND p.[Номенклатура] IS NULL))
                      AND (g.[ВидЦены]=p.[ВидЦены] OR (g.[ВидЦены] IS NULL AND p.[ВидЦены] IS NULL)))
                GROUP BY p.[Номенклатура],p.[ВидЦены],p.[Период] HAVING COUNT_BIG(*)>1
            ) THROW 51100, 'Ambiguous price periods; pending changes retained for review.', 1;
            INSERT #PublishIntervals
            SELECT p.[Период],p.[РегистраторВид],p.[РегистраторСсылка],p.[НомерСтроки],
                p.[Активность],p.[НоменклатураСсылка],p.[Номенклатура],p.[Характеристика],
                p.[ВидЦеныСсылка],p.[ВидЦены],p.[Цена],p.[Упаковка],p.[ВалютаСсылка],p.[Валюта],
                p.[ОбластьДанныхОсновныеДанные],p.DateStart,p.DateEND
            FROM price_trial.Intervals AS p
            WHERE EXISTS (SELECT 1 FROM #PublishGroups AS g
                WHERE (g.[Номенклатура]=p.[Номенклатура] OR (g.[Номенклатура] IS NULL AND p.[Номенклатура] IS NULL))
                  AND (g.[ВидЦены]=p.[ВидЦены] OR (g.[ВидЦены] IS NULL AND p.[ВидЦены] IS NULL)));
        END;
        SELECT [ОбластьДанныхОсновныеДанные],[РегистраторВид],[РегистраторСсылка],
            [НомерСтроки],DateEND INTO #PublishTerminals FROM price_trial.Intervals
        WHERE __etl_terminal=1;
        CREATE UNIQUE CLUSTERED INDEX TerminalKey ON #PublishTerminals
            ([ОбластьДанныхОсновныеДанные],[РегистраторВид],[РегистраторСсылка],[НомерСтроки]);
        BEGIN TRANSACTION;
        EXEC price_trial.PublishBatch;
        DELETE FROM price_trial.PendingKeys;
        DELETE FROM price_trial.PendingGroups;
        UPDATE price_trial.PublicationControl SET LastPublishedAsOf=@asof WHERE Id=1;
        INSERT price_trial.Publications VALUES (NEWID(),@started,SYSDATETIME(),@asof,
            (SELECT COUNT_BIG(*) FROM #PublishKeys),(SELECT COUNT_BIG(*) FROM #PublishGroups));
        COMMIT TRANSACTION;
        SELECT @asof AS PublishedAsOf,
            DATEDIFF_BIG(millisecond,@started,SYSDATETIME())/60000.0 AS PublishMinutes,
            (SELECT COUNT_BIG(*) FROM #PublishKeys) AS PublishedKeys,
            (SELECT COUNT_BIG(*) FROM #PublishGroups) AS PublishedGroups;
        EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh', @LockOwner='Session';
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
        EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh', @LockOwner='Session';
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE price_trial.RefreshAndPublish
AS
BEGIN
    SET NOCOUNT ON;
    IF @@TRANCOUNT<>0 THROW 51100, 'Run outside a transaction.', 1;
    DECLARE @lock int;
    EXEC @lock=sys.sp_getapplock @Resource=N'price_trial.Refresh',
        @LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
    IF @lock<0 THROW 51100, 'Another price operation is running.', 1;
    BEGIN TRY
        IF NOT EXISTS (SELECT 1 FROM price_trial.PublicationControl WHERE Id=1 AND Active=1)
            THROW 51100, 'Publication is not active.', 1;
        IF OBJECT_DEFINITION(OBJECT_ID(N'price_trial.ApplyStage')) IS NULL
           OR OBJECT_DEFINITION(OBJECT_ID(N'price_trial.ApplyStage')) NOT LIKE N'%price_trial.PendingKeys%'
            THROW 51100,'The pending-delta hook is missing; refresh refused.',1;
        EXEC price_trial.Refresh @AllowFullSourceRead=1;
        EXEC price_trial.PublishPending;
        EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh',@LockOwner='Session';
    END TRY
    BEGIN CATCH
        EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh',@LockOwner='Session';
        THROW;
    END CATCH;
END;
GO
PRINT 'Publication procedures installed; Agent and production data unchanged.';
