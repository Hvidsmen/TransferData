/* ISOLATED document-version experiment. Production/Agent unchanged.
   First run reads the full raw table into a PRIVATE cache. Later runs fetch
   rows only for changed parent keys. An independent FULL reference and exact
   comparison run EVERY time and are EXCLUDED from CandidateMinutes.
   Uses existing document_trial marker. Read docs/COMMERCIAL_OFFER_DELTA.md.
*/
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51290,'Use a new window without a transaction.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
    THROW 51290,'This experiment is only for vm-dwh.',1;
IF OBJECT_ID(N'document_trial.MeasurementMarker',N'U') IS NULL
    THROW 51290,'Run the original document benchmark first.',1;
IF (SELECT COUNT(*) FROM document_trial.MeasurementMarker)<>1
   OR NOT EXISTS (SELECT 1 FROM document_trial.MeasurementMarker WHERE Version=1)
    THROW 51290,'Unexpected document trial marker.',1;
-- Set to 1 ONLY to deliberately rebuild an unverified/old private cache in full.
DECLARE @ForceFull bit=0;
DECLARE @lock int,@run uniqueidentifier=NEWID(),@suffix varchar(32),@parts int,@full bit,@mode varchar(20),
    @baseName sysname,@candidateName sysname,@baseTarget nvarchar(300),@candidateTarget nvarchar(300),
    @sql nvarchar(max),@worker nvarchar(max),@t datetime2,@ms bigint,
    @baseId int,@candidateId int,@missing bigint,@extra bigint,@referenceRows bigint,@started datetime2=SYSUTCDATETIME();
IF @ForceFull IS NULL THROW 51290,'ForceFull must be 0 or 1.',1;
EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.MeasureCommercialOffers',
    @LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51290,'Another document benchmark is running.',1;
SET @suffix=REPLACE(CONVERT(varchar(36),@run),'-','');
SET @baseName=N'OfferDeltaBase_'+@suffix;
SET @candidateName=N'OfferDeltaResult_'+@suffix;
SET @baseTarget=N'[document_trial].'+QUOTENAME(@baseName);
SET @candidateTarget=N'[document_trial].'+QUOTENAME(@candidateName);
BEGIN TRY
    IF OBJECT_ID(N'document_trial.OfferPublicationControl',N'U') IS NOT NULL
        EXEC sys.sp_executesql N'IF EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl WHERE Active=1) THROW 51290,''Production owns this cache. Roll back the offer job before running trials.'',1;';
    SET @parts=CASE WHEN OBJECT_ID(N'document_trial.OfferDeltaRawCache',N'U') IS NULL THEN 0 ELSE 1 END
        +CASE WHEN OBJECT_ID(N'document_trial.OfferDeltaHeaderCache',N'U') IS NULL THEN 0 ELSE 1 END
        +CASE WHEN OBJECT_ID(N'document_trial.OfferDeltaState',N'U') IS NULL THEN 0 ELSE 1 END;
    IF @parts NOT IN (0,3) THROW 51290,'Partial cache objects found; review manually.',1;
    IF @parts=3
    BEGIN
        IF (SELECT COUNT(*) FROM document_trial.OfferDeltaState)<>1
           OR NOT EXISTS (SELECT 1 FROM document_trial.OfferDeltaState WHERE Id=1 AND Protocol=1)
            THROW 51290,'Unexpected cache protocol/state.',1;
        IF @ForceFull=0 AND NOT EXISTS (SELECT 1 FROM document_trial.OfferDeltaState WHERE Id=1 AND Verified=1)
            THROW 51290,'Cache is unverified. Review failure; use ForceFull=1 for a deliberate full rebuild.',1;
    END;
    SET @full=CASE WHEN @parts=0 OR @ForceFull=1 THEN 1 ELSE 0 END;
    SET @mode=CASE WHEN @parts=0 THEN 'initial_full' WHEN @ForceFull=1 THEN 'forced_full' ELSE 'incremental' END;
    IF OBJECT_ID(N'document_trial.OfferDeltaRuns',N'U') IS NULL
        CREATE TABLE document_trial.OfferDeltaRuns (
            RunId uniqueidentifier NOT NULL PRIMARY KEY,SessionId int NOT NULL,Mode varchar(20) NOT NULL,
            StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,Status varchar(16) NOT NULL,
            BaselineTable sysname NOT NULL,CandidateTable sysname NOT NULL,
            BaselineObjectId int NULL,CandidateObjectId int NULL,
            BaselineMilliseconds bigint NULL,CandidateMilliseconds bigint NULL,
            HeaderRows bigint NULL,AddedDocs bigint NULL,ChangedDocs bigint NULL,DeletedDocs bigint NULL,
            FetchedRows bigint NULL,RemoteBatches int NULL,OutputRows bigint NULL,ReferenceRows bigint NULL,
            MissingGroups bigint NULL,ExtraGroups bigint NULL,ErrorNumber int NULL
        );
    IF OBJECT_ID(N'document_trial.OfferDeltaPhases',N'U') IS NULL
        CREATE TABLE document_trial.OfferDeltaPhases (
            RunId uniqueidentifier NOT NULL,StepNo int NOT NULL,Phase varchar(60) NOT NULL,
            StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,Status varchar(16) NOT NULL,
            RowsCopied bigint NULL,ErrorNumber int NULL,PRIMARY KEY (RunId,StepNo)
        );
    INSERT document_trial.OfferDeltaRuns (RunId,SessionId,Mode,StartedAtUTC,Status,BaselineTable,CandidateTable)
        VALUES (@run,@@SPID,@mode,@started,'running',@baseName,@candidateName);
    SELECT @run AS RunId,@@SPID AS SessionId,@mode AS Mode,@baseName AS BaselineTable,@candidateName AS CandidateTable;
    /* Full reference is deliberately separate from the incremental candidate.
       Stable ERP/dictionaries throughout the experiment remain a prerequisite. */
    RAISERROR('Offer delta: full independent reference (not candidate time).',10,1) WITH NOWAIT;
    INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
        VALUES (@run,1,'full_reference',SYSUTCDATETIME(),'running');
    SET @t=SYSUTCDATETIME();
    SET @sql=N'SELECT * INTO '+@baseTarget+N' FROM [1CUnloading].[1c].[Документы.бит_КоммерческоеПредложениеКлиентуТовары];
        SET @n=ROWCOUNT_BIG();
        CREATE NONCLUSTERED INDEX IX_Ссылка ON '+@baseTarget+N' ([Ссылка]);
        CREATE NONCLUSTERED INDEX IX_НомерСтроки ON '+@baseTarget+N' ([НомерСтроки]);';
    EXEC sys.sp_executesql @sql,N'@n bigint OUTPUT',@n=@referenceRows OUTPUT;
    SET @ms=DATEDIFF_BIG(millisecond,@t,SYSUTCDATETIME());
    UPDATE document_trial.OfferDeltaRuns SET BaselineMilliseconds=@ms,ReferenceRows=@referenceRows,
        BaselineObjectId=OBJECT_ID(@baseTarget,N'U') WHERE RunId=@run;
    UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status='success',RowsCopied=@referenceRows
        WHERE RunId=@run AND StepNo=1;
    SET @worker=N'DECLARE @rows bigint,@fetched bigint=0,@batches int=0,@tenant varchar(100),@ref binary(16),
    @refs nvarchar(max),@remote nvarchar(max),@fetchSql nvarchar(max);
BEGIN TRY
RAISERROR(''Offer delta: read_header_versions'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,10,''read_header_versions'',SYSUTCDATETIME(),''running'');
SELECT [_Fld2951],[_IDRRef],CONVERT(binary(8),[_Version]) AS VersionBytes
INTO #Headers FROM [erp-db-02.erp.lcl].[ErpBackUp2].[dbo].[_Document80440];
SET @rows=ROWCOUNT_BIG();
IF @rows=0 THROW 51290,''Empty source headers refused.'',1;
CREATE UNIQUE CLUSTERED INDEX HeaderKey ON #Headers ([_Fld2951],[_IDRRef]);
UPDATE document_trial.OfferDeltaRuns SET HeaderRows=@rows WHERE RunId=@run;
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows
WHERE RunId=@run AND StepNo=10;
RAISERROR(''Offer delta: find_changed_documents'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,20,''find_changed_documents'',SYSUTCDATETIME(),''running'');
SELECT TOP (0) [_Fld2951],[_IDRRef],CAST(''N'' AS char(1)) AS ChangeKind INTO #Docs FROM #Headers;
IF @full=1
    INSERT #Docs SELECT [_Fld2951],[_IDRRef],''N'' FROM #Headers;
ELSE
BEGIN
    INSERT #Docs
    SELECT n.[_Fld2951],n.[_IDRRef],CASE WHEN o.[_IDRRef] IS NULL THEN ''N'' ELSE ''U'' END
    FROM #Headers AS n LEFT JOIN document_trial.OfferDeltaHeaderCache AS o
      ON o.[_Fld2951]=n.[_Fld2951] AND o.[_IDRRef]=n.[_IDRRef]
    WHERE o.[_IDRRef] IS NULL OR n.VersionBytes<>o.VersionBytes;
    INSERT #Docs
    SELECT o.[_Fld2951],o.[_IDRRef],''D''
    FROM document_trial.OfferDeltaHeaderCache AS o LEFT JOIN #Headers AS n
      ON n.[_Fld2951]=o.[_Fld2951] AND n.[_IDRRef]=o.[_IDRRef]
    WHERE n.[_IDRRef] IS NULL;
END;
CREATE UNIQUE CLUSTERED INDEX ChangedDocKey ON #Docs ([_Fld2951],[_IDRRef]);
UPDATE document_trial.OfferDeltaRuns SET
    AddedDocs=(SELECT COUNT_BIG(*) FROM #Docs WHERE ChangeKind=''N''),
    ChangedDocs=(SELECT COUNT_BIG(*) FROM #Docs WHERE ChangeKind=''U''),
    DeletedDocs=(SELECT COUNT_BIG(*) FROM #Docs WHERE ChangeKind=''D'')
WHERE RunId=@run;
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=NULL
WHERE RunId=@run AND StepNo=20;
RAISERROR(''Offer delta: read_document_rows'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,30,''read_document_rows'',SYSUTCDATETIME(),''running'');
/* TOP(0) supplies the exact native types, including _KeyField, without a
   remote full data read. This table survives all batches within this worker. */
SELECT TOP (0)
    [_LineNo80489],
    [_Fld80490],
    [_Fld80491],
    [_Fld80492],
    [_Fld80493],
    [_Fld80494],
    [_Fld80495],
    [_Fld80496RRef],
    [_Fld80497RRef],
    [_Fld80498],
    [_Fld80499RRef],
    [_Fld80500],
    [_Fld80501],
    [_Fld80502RRef],
    [_Fld80503],
    [_Fld80504],
    [_Fld80505],
    [_Fld80506],
    [_Fld80507],
    [_Fld80508],
    [_Fld80509],
    [_Fld80510],
    [_Fld80511_TYPE],
    [_Fld80511_N],
    [_Fld80511_T],
    [_Fld80512],
    [_Fld80513_TYPE],
    [_Fld80513_N],
    [_Fld80513_T],
    [_Fld80514RRef],
    [_Fld80515],
    [_Fld80516],
    [_Fld80517],
    [_Fld80518RRef],
    [_Fld80519],
    [_Fld80520],
    [_Fld80521],
    [_Fld80522RRef],
    [_Fld80523RRef],
    [_Fld80524],
    [_Fld80525],
    [_Fld80598],
    [_Fld80599RRef],
    [_Fld80836],
    [_Fld80837],
    [_Fld81309],
    [_Fld81310],
    [_Fld81828],
    [_Fld82001],
    [_Fld82539RRef],
    [_Fld82863],
    [_Fld2951],
    [_Document80440_IDRRef],
    [_KeyField]
INTO #DeltaRows FROM [erp-db-02.erp.lcl].[ErpBackUp2].[dbo].[_Document80440_VT80488] AS source_rows
CROSS JOIN (SELECT 1 AS __no_identity) AS metadata_only;
IF @full=1
BEGIN
    INSERT #DeltaRows ([_LineNo80489], [_Fld80490], [_Fld80491], [_Fld80492], [_Fld80493], [_Fld80494], [_Fld80495], [_Fld80496RRef], [_Fld80497RRef], [_Fld80498], [_Fld80499RRef], [_Fld80500], [_Fld80501], [_Fld80502RRef], [_Fld80503], [_Fld80504], [_Fld80505], [_Fld80506], [_Fld80507], [_Fld80508], [_Fld80509], [_Fld80510], [_Fld80511_TYPE], [_Fld80511_N], [_Fld80511_T], [_Fld80512], [_Fld80513_TYPE], [_Fld80513_N], [_Fld80513_T], [_Fld80514RRef], [_Fld80515], [_Fld80516], [_Fld80517], [_Fld80518RRef], [_Fld80519], [_Fld80520], [_Fld80521], [_Fld80522RRef], [_Fld80523RRef], [_Fld80524], [_Fld80525], [_Fld80598], [_Fld80599RRef], [_Fld80836], [_Fld80837], [_Fld81309], [_Fld81310], [_Fld81828], [_Fld82001], [_Fld82539RRef], [_Fld82863], [_Fld2951], [_Document80440_IDRRef], [_KeyField])
    SELECT [_LineNo80489], [_Fld80490], [_Fld80491], [_Fld80492], [_Fld80493], [_Fld80494], [_Fld80495], [_Fld80496RRef], [_Fld80497RRef], [_Fld80498], [_Fld80499RRef], [_Fld80500], [_Fld80501], [_Fld80502RRef], [_Fld80503], [_Fld80504], [_Fld80505], [_Fld80506], [_Fld80507], [_Fld80508], [_Fld80509], [_Fld80510], [_Fld80511_TYPE], [_Fld80511_N], [_Fld80511_T], [_Fld80512], [_Fld80513_TYPE], [_Fld80513_N], [_Fld80513_T], [_Fld80514RRef], [_Fld80515], [_Fld80516], [_Fld80517], [_Fld80518RRef], [_Fld80519], [_Fld80520], [_Fld80521], [_Fld80522RRef], [_Fld80523RRef], [_Fld80524], [_Fld80525], [_Fld80598], [_Fld80599RRef], [_Fld80836], [_Fld80837], [_Fld81309], [_Fld81310], [_Fld81828], [_Fld82001], [_Fld82539RRef], [_Fld82863], [_Fld2951], [_Document80440_IDRRef], [_KeyField]
    FROM [erp-db-02.erp.lcl].[ErpBackUp2].[dbo].[_Document80440_VT80488];
    SET @fetched=ROWCOUNT_BIG();
END
ELSE
BEGIN
    SELECT [_Fld2951],[_IDRRef] INTO #Todo FROM #Docs;
    CREATE UNIQUE CLUSTERED INDEX TodoKey ON #Todo ([_Fld2951],[_IDRRef]);
    SELECT TOP (0) * INTO #Batch FROM #Todo;
    WHILE EXISTS (SELECT 1 FROM #Todo)
    BEGIN
        SELECT TOP (1) @tenant=CONVERT(varchar(100),[_Fld2951]) FROM #Todo ORDER BY [_Fld2951];
        DELETE FROM #Batch;
        /* Given numeric storage length 5, precision/scale are at most 9.
           decimal(38,9) preserves that value, including fractional separators. */
        INSERT #Batch SELECT TOP (100) * FROM #Todo
        WHERE [_Fld2951]=CONVERT(decimal(38,9),@tenant) ORDER BY [_IDRRef];
        SET @refs=N'''';
        DECLARE document_keys CURSOR LOCAL FAST_FORWARD FOR SELECT [_IDRRef] FROM #Batch ORDER BY [_IDRRef];
        OPEN document_keys;
        FETCH NEXT FROM document_keys INTO @ref;
        WHILE @@FETCH_STATUS=0
        BEGIN
            SET @refs=@refs+CASE WHEN LEN(@refs)>0 THEN N'','' ELSE N'''' END+CONVERT(varchar(34),@ref,1);
            FETCH NEXT FROM document_keys INTO @ref;
        END;
        CLOSE document_keys;
        DEALLOCATE document_keys;
        /* Physical field names, decimal literal, binary hex: ASCII only.
           The owner+tenant predicates run ON ERP, not as a local 7M-row join. */
        SET @remote=N''SELECT [_LineNo80489], [_Fld80490], [_Fld80491], [_Fld80492], [_Fld80493], [_Fld80494], [_Fld80495], [_Fld80496RRef], [_Fld80497RRef], [_Fld80498], [_Fld80499RRef], [_Fld80500], [_Fld80501], [_Fld80502RRef], [_Fld80503], [_Fld80504], [_Fld80505], [_Fld80506], [_Fld80507], [_Fld80508], [_Fld80509], [_Fld80510], [_Fld80511_TYPE], [_Fld80511_N], [_Fld80511_T], [_Fld80512], [_Fld80513_TYPE], [_Fld80513_N], [_Fld80513_T], [_Fld80514RRef], [_Fld80515], [_Fld80516], [_Fld80517], [_Fld80518RRef], [_Fld80519], [_Fld80520], [_Fld80521], [_Fld80522RRef], [_Fld80523RRef], [_Fld80524], [_Fld80525], [_Fld80598], [_Fld80599RRef], [_Fld80836], [_Fld80837], [_Fld81309], [_Fld81310], [_Fld81828], [_Fld82001], [_Fld82539RRef], [_Fld82863], [_Fld2951], [_Document80440_IDRRef], [_KeyField] FROM [ErpBackUp2].[dbo].[_Document80440_VT80488] WHERE [_Fld2951]=''
            +CONVERT(nvarchar(100),@tenant)+N'' AND [_Document80440_IDRRef] IN (''+@refs+N'')'';
        IF DATALENGTH(CONVERT(varchar(max),@remote))>8000
            THROW 51290,''OPENQUERY payload exceeded 8000 bytes.'',1;
        SET @fetchSql=N''INSERT #DeltaRows ([_LineNo80489], [_Fld80490], [_Fld80491], [_Fld80492], [_Fld80493], [_Fld80494], [_Fld80495], [_Fld80496RRef], [_Fld80497RRef], [_Fld80498], [_Fld80499RRef], [_Fld80500], [_Fld80501], [_Fld80502RRef], [_Fld80503], [_Fld80504], [_Fld80505], [_Fld80506], [_Fld80507], [_Fld80508], [_Fld80509], [_Fld80510], [_Fld80511_TYPE], [_Fld80511_N], [_Fld80511_T], [_Fld80512], [_Fld80513_TYPE], [_Fld80513_N], [_Fld80513_T], [_Fld80514RRef], [_Fld80515], [_Fld80516], [_Fld80517], [_Fld80518RRef], [_Fld80519], [_Fld80520], [_Fld80521], [_Fld80522RRef], [_Fld80523RRef], [_Fld80524], [_Fld80525], [_Fld80598], [_Fld80599RRef], [_Fld80836], [_Fld80837], [_Fld81309], [_Fld81310], [_Fld81828], [_Fld82001], [_Fld82539RRef], [_Fld82863], [_Fld2951], [_Document80440_IDRRef], [_KeyField]) SELECT [_LineNo80489], [_Fld80490], [_Fld80491], [_Fld80492], [_Fld80493], [_Fld80494], [_Fld80495], [_Fld80496RRef], [_Fld80497RRef], [_Fld80498], [_Fld80499RRef], [_Fld80500], [_Fld80501], [_Fld80502RRef], [_Fld80503], [_Fld80504], [_Fld80505], [_Fld80506], [_Fld80507], [_Fld80508], [_Fld80509], [_Fld80510], [_Fld80511_TYPE], [_Fld80511_N], [_Fld80511_T], [_Fld80512], [_Fld80513_TYPE], [_Fld80513_N], [_Fld80513_T], [_Fld80514RRef], [_Fld80515], [_Fld80516], [_Fld80517], [_Fld80518RRef], [_Fld80519], [_Fld80520], [_Fld80521], [_Fld80522RRef], [_Fld80523RRef], [_Fld80524], [_Fld80525], [_Fld80598], [_Fld80599RRef], [_Fld80836], [_Fld80837], [_Fld81309], [_Fld81310], [_Fld81828], [_Fld82001], [_Fld82539RRef], [_Fld82863], [_Fld2951], [_Document80440_IDRRef], [_KeyField]
            FROM OPENQUERY([erp-db-02.erp.lcl],''+NCHAR(39)+REPLACE(@remote,NCHAR(39),NCHAR(39)+NCHAR(39))+NCHAR(39)+N''); SET @n=ROWCOUNT_BIG();'';
        EXEC sys.sp_executesql @fetchSql,N''@n bigint OUTPUT'',@n=@rows OUTPUT;
        SET @fetched=@fetched+@rows;
        SET @batches=@batches+1;
        UPDATE document_trial.OfferDeltaRuns SET FetchedRows=@fetched,RemoteBatches=@batches WHERE RunId=@run;
        UPDATE document_trial.OfferDeltaPhases SET RowsCopied=@fetched WHERE RunId=@run AND StepNo=30;
        IF @batches%100=0 RAISERROR(''Offer delta: %d remote batches read'',10,1,@batches) WITH NOWAIT;
        DELETE t FROM #Todo AS t JOIN #Batch AS b ON b.[_Fld2951]=t.[_Fld2951] AND b.[_IDRRef]=t.[_IDRRef];
    END;
END;
IF EXISTS (SELECT 1 FROM #DeltaRows AS r LEFT JOIN #Headers AS h
    ON h.[_Fld2951]=r.[_Fld2951] AND h.[_IDRRef]=r.[_Document80440_IDRRef]
    WHERE h.[_IDRRef] IS NULL)
    THROW 51290,''Rows without a parent header cannot be tracked by its version.'',1;
SET @rows=@fetched;
UPDATE document_trial.OfferDeltaRuns SET FetchedRows=@fetched,RemoteBatches=@batches WHERE RunId=@run;
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows
WHERE RunId=@run AND StepNo=30;
RAISERROR(''Offer delta: read_dictionaries'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,40,''read_dictionaries'',SYSUTCDATETIME(),''running'');
SELECT [Ссылка],[Наименование] INTO #OfferNomen FROM [erp-db-02.erp.lcl].[exch_biview2].[dbo].[Справочники.Номенклатура];
SELECT [Ссылка],[Наименование] INTO #OfferKinds FROM [erp-db-02.erp.lcl].[exch_biview2].[dbo].[Справочники.ВидыЦен];
SELECT [Ссылка],[Наименование] INTO #OfferCurrencies FROM [erp-db-02.erp.lcl].[exch_biview2].[dbo].[Справочники.Валюты];
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=NULL
WHERE RunId=@run AND StepNo=40;
RAISERROR(''Offer delta: index_dictionaries'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,50,''index_dictionaries'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_Reference ON #OfferNomen ([Ссылка]) INCLUDE ([Наименование]);
CREATE NONCLUSTERED INDEX IX_Reference ON #OfferKinds ([Ссылка]) INCLUDE ([Наименование]);
CREATE NONCLUSTERED INDEX IX_Reference ON #OfferCurrencies ([Ссылка]) INCLUDE ([Наименование]);
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=NULL
WHERE RunId=@run AND StepNo=50;
RAISERROR(''Offer delta: apply_private_cache'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,60,''apply_private_cache'',SYSUTCDATETIME(),''running'');
/* All linked-server reads finished BEFORE this local cache transaction.
   No RPC OUT, distributed writes, source index or remote setting changes. */
BEGIN TRANSACTION;
IF OBJECT_ID(N''document_trial.OfferDeltaRawCache'',N''U'') IS NULL
BEGIN
    SELECT TOP (0) * INTO document_trial.OfferDeltaRawCache FROM #DeltaRows;
    CREATE UNIQUE CLUSTERED INDEX NativeLineKey ON document_trial.OfferDeltaRawCache
        ([_Fld2951],[_Document80440_IDRRef],[_KeyField]);
    SELECT TOP (0) * INTO document_trial.OfferDeltaHeaderCache FROM #Headers;
    CREATE UNIQUE CLUSTERED INDEX NativeDocKey ON document_trial.OfferDeltaHeaderCache ([_Fld2951],[_IDRRef]);
    CREATE TABLE document_trial.OfferDeltaState (
        Id int NOT NULL PRIMARY KEY CHECK (Id=1),Protocol int NOT NULL,
        Verified bit NOT NULL,PreparedRunId uniqueidentifier NOT NULL,
        PreparedAtUTC datetime2 NOT NULL,LastVerifiedRunId uniqueidentifier NULL,
        LastVerifiedAtUTC datetime2 NULL
    );
    INSERT document_trial.OfferDeltaState (Id,Protocol,Verified,PreparedRunId,PreparedAtUTC)
        VALUES (1,1,0,@run,SYSUTCDATETIME());
END;
IF @full=1
BEGIN
    TRUNCATE TABLE document_trial.OfferDeltaRawCache;
    TRUNCATE TABLE document_trial.OfferDeltaHeaderCache;
    INSERT document_trial.OfferDeltaRawCache SELECT * FROM #DeltaRows;
    INSERT document_trial.OfferDeltaHeaderCache SELECT * FROM #Headers;
END
ELSE
BEGIN
    DELETE r FROM document_trial.OfferDeltaRawCache AS r JOIN #Docs AS d
      ON d.[_Fld2951]=r.[_Fld2951] AND d.[_IDRRef]=r.[_Document80440_IDRRef];
    INSERT document_trial.OfferDeltaRawCache SELECT * FROM #DeltaRows;
    DELETE h FROM document_trial.OfferDeltaHeaderCache AS h JOIN #Docs AS d
      ON d.[_Fld2951]=h.[_Fld2951] AND d.[_IDRRef]=h.[_IDRRef];
    INSERT document_trial.OfferDeltaHeaderCache
    SELECT h.* FROM #Headers AS h JOIN #Docs AS d ON d.[_Fld2951]=h.[_Fld2951] AND d.[_IDRRef]=h.[_IDRRef];
END;
/* Checkpoint is explicitly UNVERIFIED until independent output comparison.
   A failed/cancelled validation can never become the next incremental baseline. */
UPDATE document_trial.OfferDeltaState SET Verified=0,PreparedRunId=@run,PreparedAtUTC=SYSUTCDATETIME() WHERE Id=1;
COMMIT TRANSACTION;
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=NULL
WHERE RunId=@run AND StepNo=60;
RAISERROR(''Offer delta: enrich_all_cached_rows'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,70,''enrich_all_cached_rows'',SYSUTCDATETIME(),''running'');
SELECT
    r.[_LineNo80489] AS [НомерСтроки],
    r.[_Fld80490] AS [ИдентификаторСтрокиЗапроса],
    r.[_Fld80491] AS [ИдентификаторДляСопоставленияНоменклатуры],
    r.[_Fld80492] AS [НоменклатураПокупателяИдентификатор],
    r.[_Fld80493] AS [НоменклатураПокупателяПредставление],
    r.[_Fld80494] AS [НоменклатураВСервисеИдентификатор],
    r.[_Fld80495] AS [ХарактеристикаВСервисеИдентификатор],
    r.[_Fld80496RRef] AS [Номенклатура],
    r.[_Fld80497RRef] AS [Характеристика],
    r.[_Fld80498] AS [Количество],
    r.[_Fld80499RRef] AS [ЕдиницаИзмерения],
    r.[_Fld80500] AS [Цена],
    r.[_Fld80501] AS [Сумма],
    r.[_Fld80502RRef] AS [СтавкаНДС],
    r.[_Fld80503] AS [СуммаНДС],
    r.[_Fld80504] AS [СуммаСНДС],
    r.[_Fld80505] AS [ПроцентРучнойСкидки],
    r.[_Fld80506] AS [СуммаРучнойСкидки],
    r.[_Fld80507] AS [ПроцентАвтоматическойСкидки],
    r.[_Fld80508] AS [СуммаАвтоматическойСкидки],
    r.[_Fld80509] AS [ЗапросКоличество],
    r.[_Fld80510] AS [ЗапросЕдиницаИзмерения],
    r.[_Fld80511_TYPE] AS [СрокПоставкиТип],
    r.[_Fld80511_N] AS [СрокПоставкиВид],
    CASE WHEN YEAR(r.[_Fld80511_T])<3753 THEN r.[_Fld80511_T] ELSE DATEADD(year,-2000,r.[_Fld80511_T]) END AS [СрокПоставкиДата],
    r.[_Fld80512] AS [ЗапросЦена],
    r.[_Fld80513_TYPE] AS [ЗапросСрокПоставкиТип],
    r.[_Fld80513_N] AS [ЗапросСрокПоставкиВид],
    CASE WHEN YEAR(r.[_Fld80513_T])<3753 THEN r.[_Fld80513_T] ELSE DATEADD(year,-2000,r.[_Fld80513_T]) END AS [ЗапросСрокПоставкиДата],
    r.[_Fld80514RRef] AS [ВидЦены],
    r.[_Fld80515] AS [КомментарийДляПокупателя],
    r.[_Fld80516] AS [КомментарийВнутренний],
    r.[_Fld80517] AS [УдалитьКоличествоУпаковок],
    r.[_Fld80518RRef] AS [УдалитьУпаковка],
    r.[_Fld80519] AS [УдалитьАктивность],
    r.[_Fld80520] AS [УдалитьТекстовоеОписание],
    r.[_Fld80521] AS [КлючСвязи],
    r.[_Fld80522RRef] AS [УдалитьНоменклатураНабора],
    r.[_Fld80523RRef] AS [УдалитьХарактеристикаНабора],
    r.[_Fld80524] AS [ХешСуммаСтрокиЗапроса],
    r.[_Fld80525] AS [УдалитьКоличество],
    r.[_Fld80598] AS [ТехническоеНаименование],
    r.[_Fld80599RRef] AS [Валюта],
    r.[_Fld80836] AS [ЭтоЗаголовок],
    r.[_Fld80837] AS [ТекстЗаголовка],
    r.[_Fld81309] AS [СрокПоставкиБИТ],
    r.[_Fld81310] AS [СрокиПоставкиДляПечати],
    r.[_Fld81828] AS [Даичи_ЦенаСоСкидкой],
    r.[_Fld82001] AS [КодСтроки],
    r.[_Fld82539RRef] AS [БИТ_СтатусСтока],
    r.[_Fld82863] AS [Группировать],
    r.[_Fld2951] AS [ОбластьДанныхОсновныеДанные],
    r.[_Document80440_IDRRef] AS [Ссылка],
    n.[Наименование] AS [Номенклатура_Наименование],
    k.[Наименование] AS [ВидЦены_Наименование],
    c.[Наименование] AS [Наименование]
INTO __DELTA_TARGET__
FROM document_trial.OfferDeltaRawCache AS r
LEFT JOIN #OfferNomen AS n ON n.[Ссылка]=r.[_Fld80496RRef]
LEFT JOIN #OfferKinds AS k ON k.[Ссылка]=r.[_Fld80514RRef]
LEFT JOIN #OfferCurrencies AS c ON c.[Ссылка]=r.[_Fld80599RRef];
SET @rows=ROWCOUNT_BIG();
UPDATE document_trial.OfferDeltaRuns SET OutputRows=@rows WHERE RunId=@run;
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows
WHERE RunId=@run AND StepNo=70;
RAISERROR(''Offer delta: index_reference'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,80,''index_reference'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_Ссылка ON __DELTA_TARGET__ ([Ссылка]);
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=NULL
WHERE RunId=@run AND StepNo=80;
RAISERROR(''Offer delta: index_linenumber'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
    VALUES (@run,90,''index_linenumber'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_НомерСтроки ON __DELTA_TARGET__ ([НомерСтроки]);
UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=NULL
WHERE RunId=@run AND StepNo=90;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;';
    SET @worker=REPLACE(@worker,N'__DELTA_TARGET__',@candidateTarget);
    SET @t=SYSUTCDATETIME();
    EXEC sys.sp_executesql @worker,N'@run uniqueidentifier,@full bit',@run=@run,@full=@full;
    SET @ms=DATEDIFF_BIG(millisecond,@t,SYSUTCDATETIME());
    UPDATE document_trial.OfferDeltaRuns SET CandidateMilliseconds=@ms,CandidateObjectId=OBJECT_ID(@candidateTarget,N'U') WHERE RunId=@run;
    SET @baseId=OBJECT_ID(@baseTarget,N'U');
    SET @candidateId=OBJECT_ID(@candidateTarget,N'U');
    INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
        VALUES (@run,300,'compare_schema',SYSUTCDATETIME(),'running');
    IF EXISTS (
        SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name
        FROM sys.columns WHERE object_id=@baseId
        EXCEPT
        SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name
        FROM sys.columns WHERE object_id=@candidateId
    ) OR EXISTS (
        SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name
        FROM sys.columns WHERE object_id=@candidateId
        EXCEPT
        SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name
        FROM sys.columns WHERE object_id=@baseId
    )
    BEGIN
        SELECT OBJECT_NAME(object_id) AS TrialTable,column_id,name,
            TYPE_NAME(system_type_id) AS DataType,max_length,precision,scale,collation_name
        FROM sys.columns WHERE object_id IN (@baseId,@candidateId) ORDER BY column_id,object_id;
        THROW 51290,'Column schemas differ. Keep copies for review; production unchanged.',1;
    END;
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id=@baseId AND system_type_id IN (98,240))
        THROW 51290,'SQL variant or CLR column requires a reviewed comparison.',1;
    UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status='success'
        WHERE RunId=@run AND StepNo=300;

    RAISERROR('Offer delta: full validation (excluded from candidate time).',10,1) WITH NOWAIT;
    INSERT document_trial.OfferDeltaPhases (RunId,StepNo,Phase,StartedAtUTC,Status)
        VALUES (@run,301,'compare_values',SYSUTCDATETIME(),'running');
    DECLARE @projection nvarchar(max)=N'',@groupColumns nvarchar(max)=N'',
        @name sysname,@type int,@columnId int,@expression nvarchar(max);
    DECLARE columns_to_compare CURSOR LOCAL FAST_FORWARD FOR
        SELECT column_id,name,system_type_id FROM sys.columns WHERE object_id=@baseId ORDER BY column_id;
    OPEN columns_to_compare;
    FETCH NEXT FROM columns_to_compare INTO @columnId,@name,@type;
    WHILE @@FETCH_STATUS=0
    BEGIN
        SET @expression=CASE WHEN @type=241
            THEN N'CONVERT(varbinary(max),CONVERT(nvarchar(max),'+QUOTENAME(@name)+N'))'
            WHEN @type=35 THEN N'CONVERT(varbinary(max),CONVERT(varchar(max),'+QUOTENAME(@name)+N'))'
            WHEN @type=99 THEN N'CONVERT(varbinary(max),CONVERT(nvarchar(max),'+QUOTENAME(@name)+N'))'
            WHEN @type IN (34,35,99,165,167,173,175,231,239)
            THEN N'CONVERT(varbinary(max),'+QUOTENAME(@name)+N')'
            ELSE QUOTENAME(@name) END;
        SET @projection=@projection+CASE WHEN LEN(@projection)>0 THEN N',' ELSE N'' END
            +@expression+N' AS '+QUOTENAME(@name);
        SET @groupColumns=@groupColumns+CASE WHEN LEN(@groupColumns)>0 THEN N',' ELSE N'' END+@expression;
        IF @type IN (34,35,99,165,167,173,175,231,239)
        BEGIN
            SET @projection=@projection+N',DATALENGTH('+QUOTENAME(@name)+N') AS '
                +QUOTENAME(N'__etl_length_'+CONVERT(nvarchar(10),@columnId));
            SET @groupColumns=@groupColumns+N',DATALENGTH('+QUOTENAME(@name)+N')';
        END;
        FETCH NEXT FROM columns_to_compare INTO @columnId,@name,@type;
    END;
    CLOSE columns_to_compare;
    DEALLOCATE columns_to_compare;
    SET @sql=N';WITH a AS (SELECT '+@projection+N',COUNT_BIG(*) AS copies FROM '+@baseTarget+N' GROUP BY '+@groupColumns+N'),
        b AS (SELECT '+@projection+N',COUNT_BIG(*) AS copies FROM '+@candidateTarget+N' GROUP BY '+@groupColumns+N')
        SELECT @missing=(SELECT COUNT_BIG(*) FROM (SELECT * FROM a EXCEPT SELECT * FROM b) d),
               @extra=(SELECT COUNT_BIG(*) FROM (SELECT * FROM b EXCEPT SELECT * FROM a) d);';
    EXEC sys.sp_executesql @sql,N'@missing bigint OUTPUT,@extra bigint OUTPUT',@missing=@missing OUTPUT,@extra=@extra OUTPUT;
    UPDATE document_trial.OfferDeltaPhases SET CompletedAtUTC=SYSUTCDATETIME(),Status='success'
        WHERE RunId=@run AND StepNo=301;
    IF @missing=0 AND @extra=0
    BEGIN
        UPDATE document_trial.OfferDeltaState SET Verified=1,LastVerifiedRunId=@run,LastVerifiedAtUTC=SYSUTCDATETIME()
        WHERE Id=1 AND Protocol=1 AND PreparedRunId=@run;
        IF @@ROWCOUNT<>1 THROW 51290,'Prepared cache no longer matches this run.',1;
    END;
    UPDATE document_trial.OfferDeltaRuns SET Status=CASE WHEN @missing=0 AND @extra=0 THEN 'success' ELSE 'different' END,
        CompletedAtUTC=SYSUTCDATETIME(),MissingGroups=@missing,ExtraGroups=@extra WHERE RunId=@run;
    EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
    SELECT Phase,Status,RowsCopied,
        CAST(DATEDIFF_BIG(millisecond,StartedAtUTC,CompletedAtUTC)/60000.0 AS decimal(14,3)) AS Minutes
    FROM document_trial.OfferDeltaPhases WHERE RunId=@run ORDER BY StartedAtUTC,StepNo;
    SELECT RunId,Mode,Status,
        CASE WHEN MissingGroups=0 AND ExtraGroups=0 THEN 'MATCH' ELSE 'DIFFERENT' END AS ValidationResult,
        HeaderRows,AddedDocs,ChangedDocs,DeletedDocs,FetchedRows,RemoteBatches,OutputRows,ReferenceRows,
        CAST(BaselineMilliseconds/60000.0 AS decimal(14,3)) AS BaselineMinutes,
        CAST(CandidateMilliseconds/60000.0 AS decimal(14,3)) AS CandidateMinutes,
        MissingGroups,ExtraGroups,BaselineTable,CandidateTable,
        CAST(DATEDIFF_BIG(millisecond,StartedAtUTC,CompletedAtUTC)/60000.0 AS decimal(14,3)) AS IncludingValidationMinutes
    FROM document_trial.OfferDeltaRuns WHERE RunId=@run;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    IF OBJECT_ID(N'document_trial.OfferDeltaRuns',N'U') IS NOT NULL
        UPDATE document_trial.OfferDeltaRuns SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),ErrorNumber=ERROR_NUMBER(),
            BaselineObjectId=OBJECT_ID(@baseTarget,N'U'),CandidateObjectId=OBJECT_ID(@candidateTarget,N'U') WHERE RunId=@run;
    IF OBJECT_ID(N'document_trial.OfferDeltaPhases',N'U') IS NOT NULL
        UPDATE document_trial.OfferDeltaPhases SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),ErrorNumber=ERROR_NUMBER()
        WHERE RunId=@run AND Status='running';
    EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
    THROW;
END CATCH;
