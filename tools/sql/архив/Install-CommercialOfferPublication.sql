/* vm-dwh: install only. No Agent or live data changes.
   Read tools/sql/архив/COMMERCIAL_OFFER_CUTOVER.md. Requires completed delta trial. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 30000;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
 THROW 51300,'This installation is only for vm-dwh.',1;
IF OBJECT_ID(N'document_trial.OfferDeltaState',N'U') IS NULL
 THROW 51300,'Complete the delta trial first.',1;
IF OBJECT_ID(N'document_trial.OfferPublicationControl',N'U') IS NULL
BEGIN
 CREATE TABLE document_trial.OfferPublicationControl (
  Id int NOT NULL PRIMARY KEY CHECK(Id=1),Active bit NOT NULL,
  JobId uniqueidentifier NULL,StepId int NULL,OriginalEnabled bit NULL,
  OriginalCommand nvarchar(max) NULL,NewCommand nvarchar(max) NULL,
  CutoverId uniqueidentifier NULL,TargetObjectId int NULL,
  LastRunId uniqueidentifier NULL,LastFullCheckUTC datetime2 NULL,
  SwitchedAtUTC datetime2 NULL,RolledBackAtUTC datetime2 NULL
 );
 INSERT document_trial.OfferPublicationControl(Id,Active) VALUES(1,0);
END;
IF OBJECT_ID(N'document_trial.OfferJobStepBackup',N'U') IS NULL
 SELECT TOP(0) CAST(NULL AS uniqueidentifier) AS CutoverId,
  CAST(NULL AS bit) AS EnabledBefore,CAST(NULL AS datetime2) AS SavedAtUTC,s.*
 INTO document_trial.OfferJobStepBackup FROM msdb.dbo.sysjobsteps s;
GO
/* Exact public multiset comparison, including duplicate counts and text bytes. */
CREATE OR ALTER PROCEDURE document_trial.CompareOfferTables
 @baseTarget nvarchar(300),@candidateTarget nvarchar(300)
AS
BEGIN
 SET NOCOUNT ON;
 DECLARE @baseId int=OBJECT_ID(@baseTarget,N'U'),@candidateId int=OBJECT_ID(@candidateTarget,N'U'),
 @sql nvarchar(max),@missing bigint,@extra bigint;
 IF @baseId IS NULL OR @candidateId IS NULL THROW 51300,'Comparison table missing.',1;
 /* Names must resolve to local document_trial tables. */
 IF (@baseTarget NOT LIKE N'[[]document_trial].%')
 OR (@candidateTarget NOT LIKE N'[[]document_trial].%')
 THROW 51300,'Comparison accepts private tables only.',1;
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

 SELECT @missing AS MissingGroups,@extra AS ExtraGroups;
 IF @missing<>0 OR @extra<>0 THROW 51300,'Full source comparison differs; nothing published.',1;
END;
GO
/* Table identity, permissions, indexes and consumers remain intact.
   Callable inside a caller transaction; never commits it. Temp fixtures allowed. */
CREATE OR ALTER PROCEDURE document_trial.ReplaceOfferRows
 @source nvarchar(300),@target nvarchar(300)=N'[erp].[Документы.бит_КоммерческоеПредложениеКлиентуТовары]',
 @ExpectedObjectId int=NULL
AS
BEGIN
 SET NOCOUNT ON;
 IF @@TRANCOUNT=0 THROW 51300,'Publication needs a caller transaction.',1;
 DECLARE @sid int=OBJECT_ID(CASE WHEN @source LIKE N'#%' THEN N'tempdb..'+@source ELSE @source END,N'U'),@tid int=OBJECT_ID(CASE WHEN @target LIKE N'#%' THEN N'tempdb..'+@target ELSE @target END,N'U'),
  @cols nvarchar(max),@sql nvarchar(max);
 IF @sid IS NULL OR @tid IS NULL THROW 51300,'Publication table missing.',1;
 IF @source NOT LIKE N'[[]document_trial].[[]OfferPublish_%' AND @source NOT LIKE N'#%'
  THROW 51300,'Unexpected publication source.',1;
 IF @target<>N'[erp].[Документы.бит_КоммерческоеПредложениеКлиентуТовары]' AND @target NOT LIKE N'#%'
  THROW 51300,'Unexpected publication target.',1;
 RAISERROR('Offer publication: acquire_live_lock (maximum lock wait 30 seconds in refresh).',10,1) WITH NOWAIT;
 /* Acquire the live table lock before metadata checks; retain it until commit. */
 SET @sql=N'DECLARE @n int; SELECT TOP(1) @n=1 FROM '+@target+N' WITH(TABLOCKX,HOLDLOCK);';
 EXEC sys.sp_executesql @sql;
 IF @ExpectedObjectId IS NOT NULL AND OBJECT_ID(@target,N'U')<>@ExpectedObjectId
  THROW 51300,'Live table identity changed while staging.',1;
 SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name,is_computed,is_identity
 INTO #OfferSourceColumns FROM sys.columns WHERE object_id=@sid AND @source NOT LIKE N'#%'
 UNION ALL SELECT column_id,name COLLATE DATABASE_DEFAULT,system_type_id,max_length,precision,scale,collation_name COLLATE DATABASE_DEFAULT,is_computed,is_identity
 FROM tempdb.sys.columns WHERE object_id=@sid AND @source LIKE N'#%';
 SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name,is_computed,is_identity
 INTO #OfferTargetColumns FROM sys.columns WHERE object_id=@tid AND @target NOT LIKE N'#%'
 UNION ALL SELECT column_id,name COLLATE DATABASE_DEFAULT,system_type_id,max_length,precision,scale,collation_name COLLATE DATABASE_DEFAULT,is_computed,is_identity
 FROM tempdb.sys.columns WHERE object_id=@tid AND @target LIKE N'#%';
 IF EXISTS(SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name FROM #OfferSourceColumns
 EXCEPT SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name FROM #OfferTargetColumns)
 OR EXISTS(SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name FROM #OfferTargetColumns
 EXCEPT SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name FROM #OfferSourceColumns)
 THROW 51300,'Live column schema differs; publication refused.',1;
 IF @target NOT LIKE N'#%'
 BEGIN
  IF EXISTS(SELECT 1 FROM sys.triggers WHERE parent_id=@tid AND is_disabled=0)
   OR EXISTS(SELECT 1 FROM sys.foreign_keys WHERE parent_object_id=@tid OR referenced_object_id=@tid)
   OR EXISTS(SELECT 1 FROM sys.security_predicates WHERE target_object_id=@tid)
   OR EXISTS(SELECT 1 FROM sys.change_tracking_tables WHERE object_id=@tid)
   OR EXISTS(SELECT 1 FROM sys.tables WHERE object_id=@tid AND (temporal_type<>0 OR is_tracked_by_cdc=1 OR is_replicated=1))
   THROW 51300,'Live table has unsupported side effects; review required.',1;
  IF NOT EXISTS(SELECT 1 FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
   JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
   WHERE i.object_id=@tid AND i.name=N'IX_Ссылка' AND i.is_disabled=0 AND i.is_unique=0 AND i.has_filter=0
   AND ic.key_ordinal=1 AND c.name=N'Ссылка')
  OR NOT EXISTS(SELECT 1 FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
   JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
   WHERE i.object_id=@tid AND i.name=N'IX_НомерСтроки' AND i.is_disabled=0 AND i.is_unique=0 AND i.has_filter=0
   AND ic.key_ordinal=1 AND c.name=N'НомерСтроки')
   THROW 51300,'Expected live reference/line-number indexes missing.',1;
 END;
 IF EXISTS(SELECT 1 FROM #OfferTargetColumns WHERE (is_computed=1 OR is_identity=1 OR system_type_id=189))
  THROW 51300,'Generated target columns are unsupported.',1;
 SELECT @cols=STRING_AGG(CONVERT(nvarchar(max),QUOTENAME(name)),N',') WITHIN GROUP(ORDER BY column_id)
 FROM #OfferTargetColumns;
 /* A table lock prevents ordinary readers observing a partially replaced set.
    NOLOCK consumers have no consistency guarantee. */
 SET @sql=N'RAISERROR(''Offer publication: delete_live_rows'',10,1) WITH NOWAIT; DELETE FROM '+@target+N' WITH(TABLOCKX);
 RAISERROR(''Offer publication: insert_live_rows'',10,1) WITH NOWAIT; INSERT '+@target+N' ('+@cols+N') SELECT '+@cols+N' FROM '+@source+N';';
 EXEC sys.sp_executesql @sql;
END;
GO
/* Both temp snapshots are caller-owned and have unique tenant/document keys.
   Indexed anti-joins avoid sorting two complete 730k-row snapshots for EXCEPT. */
CREATE OR ALTER PROCEDURE document_trial.ValidateOfferHeaders
AS
BEGIN
 SET NOCOUNT ON;
 IF EXISTS(SELECT 1 FROM #Headers h LEFT JOIN #HeadersAfter n
  ON n.[_Fld2951]=h.[_Fld2951] AND n.[_IDRRef]=h.[_IDRRef]
  WHERE n.[_IDRRef] IS NULL OR n.VersionBytes<>h.VersionBytes)
 OR EXISTS(SELECT 1 FROM #HeadersAfter n LEFT JOIN #Headers h
  ON n.[_Fld2951]=h.[_Fld2951] AND n.[_IDRRef]=h.[_IDRRef]
  WHERE h.[_IDRRef] IS NULL)
  THROW 51300,'ERP headers changed during read; retry after the source is stable.',1;
END;
GO
CREATE OR ALTER PROCEDURE document_trial.RefreshOfferLines
 @FullCheck bit=0,@Cutover bit=0,@SkipCutoverFullCheck bit=0
AS
/* OfferPublicationDiagnosticsV3 */
BEGIN
 SET NOCOUNT ON;
 SET XACT_ABORT ON;
 IF @@TRANCOUNT<>0 THROW 51300,'Run without an outer transaction.',1;
 IF @@LOCK_TIMEOUT>30000 THROW 51300,'Use a fresh window or LOCK_TIMEOUT at most 30000.',1;
 IF @SkipCutoverFullCheck IS NULL THROW 51300,'NULL skip option refused.',1;
 IF @SkipCutoverFullCheck=1 AND (@Cutover<>1 OR @Cutover IS NULL OR @FullCheck<>0 OR @FullCheck IS NULL)
  THROW 51300,'Skipping full comparison is allowed only for an explicit fast cutover.',1;
 IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
  THROW 51300,'Only vm-dwh is supported.',1;
 DECLARE @lock int,@run uniqueidentifier=NEWID(),@full bit=0,@worker nvarchar(max),@sql nvarchar(max),
 @target nvarchar(300),@reference nvarchar(300),@suffix varchar(32),@started datetime2=SYSUTCDATETIME(),@oid int,@ownsLock bit=0,@oldTimeout int=@@LOCK_TIMEOUT,@operationSession int=@@SPID;
 /* Cutover already owns this lock. Do not acquire it twice: each acquisition
    needs a release, and cancellation bypasses TRY/CATCH. */
 IF APPLOCK_MODE(N'public',N'document_trial.MeasureCommercialOffers',N'Session')=N'NoLock'
 BEGIN
  EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
  IF @lock<0 THROW 51300,'Another offer operation is running.',1;
  SET @ownsLock=1;
 END
 ELSE IF NOT (@Cutover=1 AND APPLOCK_MODE(N'public',N'document_trial.MeasureCommercialOffers',N'Session')=N'Exclusive')
  THROW 51300,'This session already owns an offer lock; reconnect before retry.',1;
 BEGIN TRY
  IF @oldTimeout=-1 SET LOCK_TIMEOUT 30000;
  RAISERROR('Offer publication: session %d started; lock waits limited to 30 seconds.',10,1,@operationSession) WITH NOWAIT;
  IF @Cutover IS NULL OR @FullCheck IS NULL THROW 51300,'NULL options refused.',1;
  IF @Cutover=0 AND NOT EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl WHERE Id=1 AND Active=1)
   THROW 51300,'Offer publication is not active.',1;
  IF @Cutover=1 AND NOT EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl WHERE Id=1 AND Active=0 AND CutoverId IS NOT NULL)
   THROW 51300,'No prepared cutover.',1;
  IF NOT EXISTS(SELECT 1 FROM document_trial.OfferDeltaState s WHERE Id=1 AND Protocol=1 AND
   (Verified=1 OR EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl c WHERE c.Id=1 AND c.LastRunId=s.PreparedRunId)))
   THROW 51300,'Cache checkpoint is not trusted; review and rebuild using the trial.',1;
  SET @oid=OBJECT_ID(N'erp.[Документы.бит_КоммерческоеПредложениеКлиентуТовары]',N'U');
  IF @oid IS NULL OR EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl WHERE Id=1 AND TargetObjectId IS NOT NULL AND TargetObjectId<>@oid)
   THROW 51300,'Live table identity changed.',1;
  IF @SkipCutoverFullCheck=1
  BEGIN
   /* Reuse an actual previous MATCH; do not manufacture a new full-check date. */
   IF NOT EXISTS(SELECT 1 FROM document_trial.OfferDeltaState s
    JOIN document_trial.OfferDeltaRuns r ON r.RunId=s.LastVerifiedRunId
    WHERE s.Id=1 AND s.LastVerifiedAtUTC IS NOT NULL AND r.Status='success'
     AND r.MissingGroups=0 AND r.ExtraGroups=0)
    THROW 51300,'Fast cutover requires a recorded successful full trial comparison.',1;
   RAISERROR('Fast cutover: reusing prior MATCH; no full reference or value comparison.',10,1) WITH NOWAIT;
  END
  ELSE IF @Cutover=1 OR EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl WHERE Id=1 AND
    (LastFullCheckUTC IS NULL OR LastFullCheckUTC<DATEADD(day,-7,SYSUTCDATETIME()))) SET @FullCheck=1;
  SET @suffix=REPLACE(CONVERT(varchar(36),@run),'-','');
  SET @target=N'[document_trial].'+QUOTENAME(N'OfferPublish_'+@suffix);
  SET @reference=N'[document_trial].'+QUOTENAME(N'OfferAudit_'+@suffix);
  INSERT document_trial.OfferDeltaRuns(RunId,SessionId,Mode,StartedAtUTC,Status,BaselineTable,CandidateTable)
   VALUES(@run,@@SPID,'production',@started,'running',N'OfferAudit_'+@suffix,N'OfferPublish_'+@suffix);
  SELECT @run AS RunId,@@SPID AS SessionId,@FullCheck AS FullSourceCheck;
  IF @FullCheck=1
  BEGIN
   RAISERROR('Offer publication: full independent source check requested.',10,1) WITH NOWAIT;
   SET @sql=N'SELECT * INTO '+@reference+N' FROM [1CUnloading].[1c].[Документы.бит_КоммерческоеПредложениеКлиентуТовары];';
   EXEC sys.sp_executesql @sql;
  END;
  SET @worker=N'DECLARE @rows bigint,@fetched bigint=0,@batches int=0,@tenant varchar(100),@ref binary(16),
    @refs nvarchar(max),@remote nvarchar(max),@fetchSql nvarchar(max),@cleanupSql nvarchar(max);
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
IF EXISTS (
 SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name
 FROM sys.columns WHERE object_id=OBJECT_ID(N''document_trial.OfferDeltaRawCache'')
 EXCEPT
 SELECT column_id,name COLLATE DATABASE_DEFAULT,system_type_id,max_length,precision,scale,collation_name COLLATE DATABASE_DEFAULT
 FROM tempdb.sys.columns WHERE object_id=OBJECT_ID(N''tempdb..#DeltaRows'')
) OR EXISTS (
 SELECT column_id,name COLLATE DATABASE_DEFAULT,system_type_id,max_length,precision,scale,collation_name COLLATE DATABASE_DEFAULT
 FROM tempdb.sys.columns WHERE object_id=OBJECT_ID(N''tempdb..#DeltaRows'')
 EXCEPT
 SELECT column_id,name,system_type_id,max_length,precision,scale,collation_name
 FROM sys.columns WHERE object_id=OBJECT_ID(N''document_trial.OfferDeltaRawCache'')
) THROW 51300,''Raw source/cache schemas differ; review before publication.'',1;
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
/* All remote reads still precede the local transaction. */
RAISERROR(''Offer delta: recheck_header_versions (remote read)'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases(RunId,StepNo,Phase,StartedAtUTC,Status)
 VALUES(@run,55,''recheck_header_versions'',SYSUTCDATETIME(),''running'');
SELECT [_Fld2951],[_IDRRef],CONVERT(binary(8),[_Version]) AS VersionBytes
INTO #HeadersAfter FROM [erp-db-02.erp.lcl].[ErpBackUp2].[dbo].[_Document80440];
SET @rows=ROWCOUNT_BIG();
CREATE UNIQUE CLUSTERED INDEX HeaderAfterKey ON #HeadersAfter ([_Fld2951],[_IDRRef]);
UPDATE document_trial.OfferDeltaPhases SET Status=''success'',CompletedAtUTC=SYSUTCDATETIME(),RowsCopied=@rows
 WHERE RunId=@run AND StepNo=55;
RAISERROR(''Offer delta: compare_header_versions (indexed local snapshots)'',10,1) WITH NOWAIT;
INSERT document_trial.OfferDeltaPhases(RunId,StepNo,Phase,StartedAtUTC,Status)
 VALUES(@run,56,''compare_header_versions'',SYSUTCDATETIME(),''running'');
EXEC document_trial.ValidateOfferHeaders;
UPDATE document_trial.OfferDeltaPhases SET Status=''success'',CompletedAtUTC=SYSUTCDATETIME()
 WHERE RunId=@run AND StepNo=56;
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
-- Publication and COMMIT are below, inside this same dynamic batch.
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
  /* Cache, live data and checkpoint commit inside this same dynamic batch.
     No transaction is left open across the sp_executesql return boundary. */
  IF @@TRANCOUNT<>1 THROW 51300,''Unexpected publication transaction depth.'',1;
  IF @FullCheck=1 EXEC document_trial.CompareOfferTables @baseTarget=@reference,@candidateTarget=@target;
  RAISERROR(''Offer publication: publish_live_rows (lock, DELETE, INSERT).'',10,1) WITH NOWAIT;
  INSERT document_trial.OfferDeltaPhases(RunId,StepNo,Phase,StartedAtUTC,Status)
   VALUES(@run,310,''publish_live_rows'',SYSUTCDATETIME(),''running'');
  EXEC document_trial.ReplaceOfferRows @source=@target,@ExpectedObjectId=@oid;
  UPDATE document_trial.OfferDeltaPhases SET Status=''success'',CompletedAtUTC=SYSUTCDATETIME() WHERE RunId=@run AND StepNo=310;
  IF @SkipCutoverFullCheck=1
   UPDATE document_trial.OfferPublicationControl SET LastFullCheckUTC=COALESCE(LastFullCheckUTC,
    (SELECT LastVerifiedAtUTC FROM document_trial.OfferDeltaState WHERE Id=1)) WHERE Id=1;
  UPDATE document_trial.OfferPublicationControl SET LastRunId=@run,
   LastFullCheckUTC=CASE WHEN @FullCheck=1 THEN SYSUTCDATETIME() ELSE LastFullCheckUTC END WHERE Id=1;
  /* Verified means FULL source comparison only, never merely a successful load. */
  IF @FullCheck=1 UPDATE document_trial.OfferDeltaState SET Verified=1,LastVerifiedRunId=@run,LastVerifiedAtUTC=SYSUTCDATETIME() WHERE Id=1;
  UPDATE document_trial.OfferDeltaRuns SET Status=''success'',CompletedAtUTC=SYSUTCDATETIME(),
   MissingGroups=CASE WHEN @FullCheck=1 THEN 0 ELSE NULL END,ExtraGroups=CASE WHEN @FullCheck=1 THEN 0 ELSE NULL END,
   CandidateMilliseconds=DATEDIFF_BIG(millisecond,@started,SYSUTCDATETIME()),CandidateObjectId=OBJECT_ID(@target,N''U'') WHERE RunId=@run;
  SET @cleanupSql=N''DROP TABLE ''+@target+N'';''; EXEC sys.sp_executesql @cleanupSql;
  IF @FullCheck=1 BEGIN SET @cleanupSql=N''DROP TABLE ''+@reference+N'';''; EXEC sys.sp_executesql @cleanupSql; END;
  COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;';
  SET @worker=REPLACE(@worker,N'__DELTA_TARGET__',@target);
  EXEC sys.sp_executesql @worker,
   N'@run uniqueidentifier,@full bit,@FullCheck bit,@SkipCutoverFullCheck bit,@oid int,@target nvarchar(300),@reference nvarchar(300),@started datetime2',
   @run=@run,@full=@full,@FullCheck=@FullCheck,@SkipCutoverFullCheck=@SkipCutoverFullCheck,
   @oid=@oid,@target=@target,@reference=@reference,@started=@started;
  IF @@TRANCOUNT<>0 THROW 51300,'Publication batch returned an open transaction.',1;
  IF @ownsLock=1 EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
  IF @oldTimeout=-1 SET LOCK_TIMEOUT -1;
  SELECT @run AS RunId,'PUBLISHED' AS Result,@FullCheck AS FullSourceChecked,
   DATEDIFF_BIG(millisecond,@started,SYSUTCDATETIME())/60000.0 AS TotalMinutes;
 END TRY
 BEGIN CATCH
  IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
  DECLARE @failure int=ERROR_NUMBER();
  BEGIN TRY
   UPDATE document_trial.OfferDeltaRuns SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),ErrorNumber=@failure WHERE RunId=@run;
   UPDATE document_trial.OfferDeltaPhases SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),ErrorNumber=@failure WHERE RunId=@run AND Status='running';
  END TRY
  BEGIN CATCH
   RAISERROR('Could not update failure journal; original error follows.',10,1) WITH NOWAIT;
  END CATCH;
  IF @ownsLock=1 EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
  IF @oldTimeout=-1 SET LOCK_TIMEOUT -1;
  THROW;
 END CATCH;
END;
GO
SET LOCK_TIMEOUT -1;
GO
