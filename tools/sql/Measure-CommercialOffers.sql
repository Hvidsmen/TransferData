/* REAL benchmark of Agent document step 41, in an isolated schema.
   Reads TWO 1CUnloading sources (table or view), copies all their columns,
   builds the SAME five non-unique indexes in the SAME order as the live step.
   No Agent, ERP, live erp.* or source data is modified. No source procedure run.
   Run on vm-dwh outside heavy ETL. See docs/DOCUMENTS_OPTIMIZATION.md.
*/
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51200,'Use a new query window without a transaction.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
    THROW 51200,'This benchmark is only for vm-dwh.',1;
IF SCHEMA_ID(N'document_trial') IS NOT NULL
   AND OBJECT_ID(N'document_trial.MeasurementMarker',N'U') IS NULL
    THROW 51200,'document_trial exists without its marker. Stop and review.',1;

DECLARE @lock int;
EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.MeasureCommercialOffers',
    @LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51200,'Another document benchmark is running.',1;
DECLARE @run uniqueidentifier=NEWID(),@suffix varchar(32),@headers sysname,@lines sysname,
    @headerTarget nvarchar(300),@lineTarget nvarchar(300),@sql nvarchar(max),
    @phase varchar(60),@ordinal int=0,@rows bigint,@started datetime2=SYSUTCDATETIME();
SET @suffix=REPLACE(CONVERT(varchar(36),@run),'-','');
SET @headers=N'OfferHeaders_'+@suffix;
SET @lines=N'OfferLines_'+@suffix;
SET @headerTarget=N'[document_trial].'+QUOTENAME(@headers);
SET @lineTarget=N'[document_trial].'+QUOTENAME(@lines);
BEGIN TRY
    IF SCHEMA_ID(N'document_trial') IS NULL
        EXEC(N'CREATE SCHEMA document_trial AUTHORIZATION dbo');
    IF OBJECT_ID(N'document_trial.MeasurementMarker',N'U') IS NULL
    BEGIN
        CREATE TABLE document_trial.MeasurementMarker (Version int NOT NULL);
        INSERT document_trial.MeasurementMarker VALUES (1);
    END;
    IF (SELECT COUNT(*) FROM document_trial.MeasurementMarker)<>1
       OR NOT EXISTS (SELECT 1 FROM document_trial.MeasurementMarker WHERE Version=1)
        THROW 51200,'Unexpected document benchmark version.',1;
    IF OBJECT_ID(N'document_trial.MeasureRuns',N'U') IS NULL
        CREATE TABLE document_trial.MeasureRuns (
            RunId uniqueidentifier NOT NULL PRIMARY KEY,
            StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,
            Status varchar(16) NOT NULL,SessionId int NOT NULL,OptionsFlags int NOT NULL,
            HeaderTable sysname NOT NULL,LineTable sysname NOT NULL,
            HeaderObjectId int NULL,LineObjectId int NULL,ErrorNumber int NULL
        );
    IF OBJECT_ID(N'document_trial.MeasurePhases',N'U') IS NULL
        CREATE TABLE document_trial.MeasurePhases (
            RunId uniqueidentifier NOT NULL,Ordinal int NOT NULL,Phase varchar(60) NOT NULL,
            StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,
            Status varchar(16) NOT NULL,RowsCopied bigint NULL,ErrorNumber int NULL,
            PRIMARY KEY (RunId,Ordinal)
        );
    IF OBJECT_ID(N'[1CUnloading].[1c].[Документ.бит_КоммерческоеПредложениеКлиенту]') IS NULL
       OR OBJECT_ID(N'[1CUnloading].[1c].[Документы.бит_КоммерческоеПредложениеКлиентуТовары]') IS NULL
        THROW 51200,'Source object missing or metadata permission denied.',1;
    INSERT document_trial.MeasureRuns
        (RunId,StartedAtUTC,Status,SessionId,OptionsFlags,HeaderTable,LineTable)
    VALUES (@run,@started,'running',@@SPID,@@OPTIONS,@headers,@lines);

    /* Missing earlier metadata may simply mean the source is a VIEW. Actual
       rows are captured by ROWCOUNT_BIG after each copy; no extra COUNT scan. */
    SELECT s.name AS SourceSchema,o.name AS SourceObject,o.type_desc
    FROM [1CUnloading].sys.objects AS o JOIN [1CUnloading].sys.schemas AS s ON s.schema_id=o.schema_id
    WHERE s.name=N'1c' AND o.name IN (
        N'Документ.бит_КоммерческоеПредложениеКлиенту',
        N'Документы.бит_КоммерческоеПредложениеКлиентуТовары');
    SELECT @run AS RunId,@@SPID AS SessionId,@headers AS HeaderTable,@lines AS LineTable;

    DECLARE @steps TABLE (Ordinal int PRIMARY KEY,Phase varchar(60),SqlText nvarchar(max));
    INSERT @steps VALUES
        (1,'copy_headers',N'SELECT * INTO '+@headerTarget+N' FROM [1CUnloading].[1c].[Документ.бит_КоммерческоеПредложениеКлиенту]; SET @n=ROWCOUNT_BIG();'),
        (2,'copy_lines',N'SELECT * INTO '+@lineTarget+N' FROM [1CUnloading].[1c].[Документы.бит_КоммерческоеПредложениеКлиентуТовары]; SET @n=ROWCOUNT_BIG();'),
        (3,'index_headers_reference',N'CREATE NONCLUSTERED INDEX IX_Ссылка ON '+@headerTarget+N' ([Ссылка]);'),
        (4,'index_headers_documentkey',N'CREATE NONCLUSTERED INDEX IX_DocumentKey ON '+@headerTarget+N' ([DocumentKey]);'),
        (5,'index_headers_number',N'CREATE NONCLUSTERED INDEX IX_ДатНомерДата ON '+@headerTarget+N' ([Номер]);'),
        (6,'index_lines_reference',N'CREATE NONCLUSTERED INDEX IX_Ссылка ON '+@lineTarget+N' ([Ссылка]);'),
        (7,'index_lines_linenumber',N'CREATE NONCLUSTERED INDEX IX_НомерСтроки ON '+@lineTarget+N' ([НомерСтроки]);');
    WHILE @ordinal<7
    BEGIN
        SET @ordinal=@ordinal+1;
        SELECT @phase=Phase,@sql=SqlText FROM @steps WHERE Ordinal=@ordinal;
        RAISERROR('Document step 41 benchmark: %s',10,1,@phase) WITH NOWAIT;
        INSERT document_trial.MeasurePhases (RunId,Ordinal,Phase,StartedAtUTC,Status)
            VALUES (@run,@ordinal,@phase,SYSUTCDATETIME(),'running');
        SET @rows=NULL;
        EXEC sys.sp_executesql @sql,N'@n bigint OUTPUT',@n=@rows OUTPUT;
        UPDATE document_trial.MeasurePhases
        SET CompletedAtUTC=SYSUTCDATETIME(),Status='success',RowsCopied=@rows
        WHERE RunId=@run AND Ordinal=@ordinal;
        IF @ordinal<=2
            UPDATE document_trial.MeasureRuns
            SET HeaderObjectId=OBJECT_ID(@headerTarget,N'U'),LineObjectId=OBJECT_ID(@lineTarget,N'U')
            WHERE RunId=@run;
    END;
    UPDATE document_trial.MeasureRuns SET Status='success',CompletedAtUTC=SYSUTCDATETIME() WHERE RunId=@run;
    EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
    SELECT Phase,RowsCopied,
        CAST(DATEDIFF_BIG(millisecond,StartedAtUTC,CompletedAtUTC)/60000.0 AS decimal(14,3)) AS Minutes
    FROM document_trial.MeasurePhases WHERE RunId=@run ORDER BY Ordinal;
    SELECT @run AS RunId,Status,
        CAST(DATEDIFF_BIG(millisecond,StartedAtUTC,CompletedAtUTC)/60000.0 AS decimal(14,3)) AS TotalMinutes,
        HeaderTable,LineTable FROM document_trial.MeasureRuns WHERE RunId=@run;
    SELECT @run AS RunId,t.name AS TrialTable,
        SUM(CASE WHEN p.index_id IN (0,1) THEN p.row_count ELSE 0 END) AS ApproxRows,
        CAST(SUM(p.reserved_page_count)/128.0 AS decimal(18,1)) AS ReservedMB
    FROM sys.tables AS t JOIN sys.dm_db_partition_stats AS p ON p.object_id=t.object_id
    WHERE t.object_id IN (OBJECT_ID(@headerTarget,N'U'),OBJECT_ID(@lineTarget,N'U'))
    GROUP BY t.name;
END TRY
BEGIN CATCH
    /* Autocommitted completed phases stay available for diagnosis. This script
       never drops trial or live tables, including after a failed copy. */
    IF OBJECT_ID(N'document_trial.MeasureRuns',N'U') IS NOT NULL
        UPDATE document_trial.MeasureRuns SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),
            HeaderObjectId=OBJECT_ID(@headerTarget,N'U'),LineObjectId=OBJECT_ID(@lineTarget,N'U'),
            ErrorNumber=ERROR_NUMBER() WHERE RunId=@run;
    IF OBJECT_ID(N'document_trial.MeasurePhases',N'U') IS NOT NULL
        UPDATE document_trial.MeasurePhases SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),
            ErrorNumber=ERROR_NUMBER() WHERE RunId=@run AND Ordinal=@ordinal AND Status='running';
    EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
    THROW;
END CATCH;
