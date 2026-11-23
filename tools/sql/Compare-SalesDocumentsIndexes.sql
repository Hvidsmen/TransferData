/* ISOLATED header extraction experiment, vm-dwh. No Agent/live table writes.
   Reads the CURRENT procedure definition in 1CUnloading without executing it.
   Two private outputs; baseline 77 temp indexes, candidate omits only 43 fact
   indexes. Full public multiset comparison BEFORE arbitrary job deduplication.
   Read docs/SALES_DOCUMENTS_TRIAL.md. Run whole file outside ETL. */
USE [1CUnloading];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51400,'Use a new window without a transaction.',1;
IF @@LOCK_TIMEOUT<>-1 THROW 51400,'Use a fresh default SSMS window.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh' THROW 51400,'Only vm-dwh.',1;
IF OBJECT_ID(N'DataWH.document_trial.MeasurementMarker',N'U') IS NULL
 THROW 51400,'Existing document trial marker required.',1;
IF NOT EXISTS(SELECT 1 FROM DataWH.document_trial.MeasurementMarker WHERE Version=1)
 THROW 51400,'Unexpected document trial marker.',1;
DECLARE @CandidateFirst bit=0; -- Repeat in reverse order only if the first result warrants it.
DECLARE @definition nvarchar(max)=OBJECT_DEFINITION(OBJECT_ID(N'[1c].[sp_Документы.РеализацияТоваровУслуг]',N'P')),
 @flat nvarchar(max),@body nvarchar(max),@code nvarchar(max)=N'',@c nchar(1),@next nchar(1),@state int=0,@i int=1,
 @as int,@prefix nvarchar(max),@lock int,@run uniqueidentifier=NEWID(),@suffix varchar(32),
 @base nvarchar(300),@candidate nvarchar(300),@live nvarchar(300)=N'[1CUnloading].[1c].[Документы.РеализацияТоваровУслуг]',
 @indexStart int,@on int,@open int,@close int,@from int=1,@indexNo int=0,@factCount int=0,
 @indexName sysname,@tempName sysname,@stmt nvarchar(max),@builtBase nvarchar(max)=N'',@builtCandidate nvarchar(max)=N'',
 @baseBody nvarchar(max),@candidateBody nvarchar(max),@sql nvarchar(max),@variant varchar(12),@pass int=0,@t datetime2,
 @comparison nvarchar(max);
IF @definition IS NULL THROW 51400,'Source procedure is missing, encrypted or not visible.',1;
IF @CandidateFirst IS NULL THROW 51400,'CandidateFirst cannot be NULL.',1;
/* Keep offsets identical to the live module, preserving original comments and
   literals in executable bodies. Never execute the flattened pasted text. */
SET @flat=REPLACE(REPLACE(REPLACE(@definition,NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' ');
SET @as=CHARINDEX(N' AS ',@flat COLLATE Latin1_General_100_CI_AS);
IF @as=0 THROW 51400,'Unexpected procedure header.',1;
SET @prefix=REPLACE(SUBSTRING(@flat,1,@as),N' ',N'');
IF @prefix COLLATE Latin1_General_100_CI_AS<>N'CREATEPROCEDURE[1c].[sp_Документы.РеализацияТоваровУслуг]'
 THROW 51400,'Procedure header changed; review source before trial.',1;
SET @body=SUBSTRING(@definition,@as+4,LEN(@definition));
IF (LEN(@body)-LEN(REPLACE(@body,@live,N'')))/LEN(@live)<>3
 THROW 51400,'Expected exactly three original output references.',1;
/* Mask strings and single-line comments in a same-length analysis copy.
   Block comments are absent from the reviewed body: reject new ones for review. */
IF CHARINDEX(N'/*',@body)>0 THROW 51400,'Body changed (block comments); review first.',1;
WHILE @i<=LEN(@body)
BEGIN
 SET @c=SUBSTRING(@body,@i,1); SET @next=SUBSTRING(@body,@i+1,1);
 IF @state=2
 BEGIN
  IF @c IN(NCHAR(10),NCHAR(13)) SET @state=0;
  SET @code=@code+N' ';
 END
 ELSE IF @state=1
 BEGIN
  SET @code=@code+N' ';
  IF @c=NCHAR(39)
  BEGIN
   IF @next=NCHAR(39) BEGIN SET @code=@code+N' '; SET @i=@i+1; END
   ELSE SET @state=0;
  END;
 END
 ELSE IF @c=NCHAR(39) BEGIN SET @state=1; SET @code=@code+N' '; END
 ELSE IF @c=N'-' AND @next=N'-' BEGIN SET @state=2; SET @code=@code+N'  '; SET @i=@i+1; END
 ELSE SET @code=@code+CASE WHEN @c IN(NCHAR(13),NCHAR(10),NCHAR(9)) THEN N' ' ELSE @c END;
 SET @i=@i+1;
END;
IF @state=1 THROW 51400,'Unterminated literal in source.',1;
/* Reject additional executable writes/calls, even if output/index counts match. */
DECLARE @word varchar(20);
DECLARE denied CURSOR LOCAL FAST_FORWARD FOR SELECT word FROM (VALUES
 ('EXEC'),('EXECUTE'),('INSERT'),('UPDATE'),('DELETE'),('MERGE'),('TRUNCATE'),('ALTER'),('GRANT'),('REVOKE'),('DENY'),('TRAN'),('TRANSACTION'),('COMMIT'),('ROLLBACK')) w(word);
OPEN denied; FETCH NEXT FROM denied INTO @word;
WHILE @@FETCH_STATUS=0
BEGIN
 IF CHARINDEX(N' '+@word+N' ',N' '+@code+N' ' COLLATE Latin1_General_100_CI_AS)>0
  THROW 51400,'Source has unexpected executable writes or calls; review first.',1;
 FETCH NEXT FROM denied INTO @word;
END;
CLOSE denied; DEALLOCATE denied;
/* Only reviewed SELECT INTO and DROP TABLE destinations are allowed. */
DECLARE @keyword nvarchar(30),@pos int,@dest int;
DECLARE destinations CURSOR LOCAL FAST_FORWARD FOR SELECT k FROM (VALUES(N'INTO '),(N'DROP TABLE ')) x(k);
OPEN destinations; FETCH NEXT FROM destinations INTO @keyword;
WHILE @@FETCH_STATUS=0
BEGIN
 SET @pos=1;
 WHILE 1=1
 BEGIN
  SET @pos=CHARINDEX(@keyword,@code COLLATE Latin1_General_100_CI_AS,@pos);
  IF @pos=0 BREAK;
  SET @dest=@pos+LEN(@keyword)+1; -- LEN excludes the final space.
  WHILE SUBSTRING(@code,@dest,1)=N' ' SET @dest=@dest+1;
  IF SUBSTRING(@code,@dest,1)<>N'#' AND SUBSTRING(@code,@dest,LEN(@live))<>@live
   THROW 51400,'Unexpected permanent output/drop destination; trial refused.',1;
  SET @pos=@dest;
 END;
 FETCH NEXT FROM destinations INTO @keyword;
END;
CLOSE destinations; DEALLOCATE destinations;
/* Parse only the 77 reviewed simple single-column NONCLUSTERED INDEX statements.
   Preserve every other character, including newlines and comment boundaries. */
WHILE 1=1
BEGIN
 SET @indexStart=CHARINDEX(N'CREATE NONCLUSTERED INDEX ',@code COLLATE Latin1_General_100_CI_AS,@from);
 IF @indexStart=0 BREAK;
 SET @on=CHARINDEX(N' ON ',@code COLLATE Latin1_General_100_CI_AS,@indexStart);
 SET @open=CHARINDEX(N'(',@code,@on); SET @close=CHARINDEX(N')',@code,@open);
 IF @on=0 OR @open=0 OR @close=0 THROW 51400,'Unexpected index syntax.',1;
 SET @indexName=LTRIM(RTRIM(SUBSTRING(@body,@indexStart+LEN(N'CREATE NONCLUSTERED INDEX '),@on-@indexStart-LEN(N'CREATE NONCLUSTERED INDEX '))));
 SET @tempName=LTRIM(RTRIM(SUBSTRING(@body,@on+4,@open-@on-4)));
 IF LEFT(@tempName,1)<>N'#' OR CHARINDEX(N',',SUBSTRING(@code,@open,@close-@open))>0
  THROW 51400,'Expected a simple single-column temp-table index.',1;
 SET @indexNo=@indexNo+1;
 IF @tempName=N'#РеализацияТоваровУслуг' SET @factCount=@factCount+1;
 SET @stmt=NCHAR(10)+N'RAISERROR(''Sales extract: index '+CONVERT(nvarchar(10),@indexNo)+N'/77'',10,1) WITH NOWAIT; SET @__indexStarted=SYSUTCDATETIME();'+NCHAR(10)
  +SUBSTRING(@body,@indexStart,@close-@indexStart+1)+N';'+NCHAR(10)
  +N'INSERT DataWH.document_trial.SalesExtractIndexes(RunId,Variant,IndexNo,TempName,IndexName,Milliseconds) VALUES(@run,@variant,'
  +CONVERT(nvarchar(10),@indexNo)+N',N'+NCHAR(39)+REPLACE(@tempName,NCHAR(39),NCHAR(39)+NCHAR(39))+NCHAR(39)+N',N'
  +NCHAR(39)+REPLACE(@indexName,NCHAR(39),NCHAR(39)+NCHAR(39))+NCHAR(39)+N',DATEDIFF_BIG(millisecond,@__indexStarted,SYSUTCDATETIME()));'+NCHAR(10);
 SET @builtBase=@builtBase+SUBSTRING(@body,@from,@indexStart-@from)+@stmt;
 SET @builtCandidate=@builtCandidate+SUBSTRING(@body,@from,@indexStart-@from)
  +CASE WHEN @tempName=N'#РеализацияТоваровУслуг' THEN NCHAR(10)+N'/* Trial omits this fact index. */'+NCHAR(10) ELSE @stmt END;
 SET @from=@close+1;
END;
IF @indexNo<>77 OR @factCount<>43 THROW 51400,'Expected 77 temp indexes including 43 on the fact table; source changed.',1;
IF (LEN(@code)-LEN(REPLACE(@code COLLATE Latin1_General_100_CI_AS,N'CREATE ',N'')))/7<>77
 THROW 51400,'Unexpected additional CREATE statement.',1;
SET @builtBase=@builtBase+SUBSTRING(@body,@from,LEN(@body));
SET @builtCandidate=@builtCandidate+SUBSTRING(@body,@from,LEN(@body));
SET @suffix=REPLACE(CONVERT(varchar(36),@run),'-','');
SET @base=N'[DataWH].[document_trial].'+QUOTENAME(N'SalesBase_'+@suffix);
SET @candidate=N'[DataWH].[document_trial].'+QUOTENAME(N'SalesNoFactIndex_'+@suffix);
SET @baseBody=N'DECLARE @__indexStarted datetime2;'+NCHAR(10)+REPLACE(@builtBase,@live,@base);
SET @candidateBody=N'DECLARE @__indexStarted datetime2;'+NCHAR(10)+REPLACE(@builtCandidate,@live,@candidate);
IF APPLOCK_MODE(N'public',N'document_trial.SalesExtract',N'Session')<>N'NoLock'
 THROW 51400,'This session owns a previous trial lock; reconnect.',1;
EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.SalesExtract',@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51400,'Another sales trial is running.',1;
BEGIN TRY
 SET LOCK_TIMEOUT 30000;
 IF OBJECT_ID(N'DataWH.document_trial.SalesExtractRuns',N'U') IS NULL
  CREATE TABLE DataWH.document_trial.SalesExtractRuns(RunId uniqueidentifier NOT NULL PRIMARY KEY,SessionId int NOT NULL,
   CandidateFirst bit NOT NULL,StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,Status varchar(16) NOT NULL,
   SourceHash varbinary(32) NOT NULL,BaseTable nvarchar(300) NOT NULL,CandidateTable nvarchar(300) NOT NULL,
   BaselineMilliseconds bigint NULL,CandidateMilliseconds bigint NULL,ComparisonMilliseconds bigint NULL,ErrorNumber int NULL);
 IF OBJECT_ID(N'DataWH.document_trial.SalesExtractIndexes',N'U') IS NULL
  CREATE TABLE DataWH.document_trial.SalesExtractIndexes(RunId uniqueidentifier NOT NULL,Variant varchar(12) NOT NULL,
   IndexNo int NOT NULL,TempName sysname NOT NULL,IndexName sysname NOT NULL,Milliseconds bigint NOT NULL,PRIMARY KEY(RunId,Variant,IndexNo));
 INSERT DataWH.document_trial.SalesExtractRuns(RunId,SessionId,CandidateFirst,StartedAtUTC,Status,SourceHash,BaseTable,CandidateTable)
 VALUES(@run,@@SPID,@CandidateFirst,SYSUTCDATETIME(),'running',HASHBYTES('SHA2_256',@definition),@base,@candidate);
 SELECT @run AS RunId,@@SPID AS SessionId,@base AS BaseTable,@candidate AS CandidateTable;
 WHILE @pass<2
 BEGIN
  SET @variant=CASE WHEN @pass=CONVERT(int,@CandidateFirst) THEN 'baseline' ELSE 'candidate' END;
  SET @sql=CASE WHEN @variant='baseline' THEN @baseBody ELSE @candidateBody END;
  RAISERROR('Sales extract: %s started.',10,1,@variant) WITH NOWAIT;
  SET @t=SYSUTCDATETIME();
  EXEC sys.sp_executesql @sql,N'@run uniqueidentifier,@variant varchar(12)',@run=@run,@variant=@variant;
  IF @@TRANCOUNT<>0 THROW 51400,'Extraction returned an open transaction.',1;
  UPDATE DataWH.document_trial.SalesExtractRuns SET
   BaselineMilliseconds=CASE WHEN @variant='baseline' THEN DATEDIFF_BIG(millisecond,@t,SYSUTCDATETIME()) ELSE BaselineMilliseconds END,
   CandidateMilliseconds=CASE WHEN @variant='candidate' THEN DATEDIFF_BIG(millisecond,@t,SYSUTCDATETIME()) ELSE CandidateMilliseconds END WHERE RunId=@run;
  SET @pass=@pass+1;
 END;
 RAISERROR('Sales extract: exact full comparison (outside extraction timings).',10,1) WITH NOWAIT;
 SET @t=SYSUTCDATETIME();
 SET @comparison=N'BEGIN
 SET NOCOUNT ON;
 DECLARE @baseId int=OBJECT_ID(@baseTarget,N''U''),@candidateId int=OBJECT_ID(@candidateTarget,N''U''),
 @sql nvarchar(max),@missing bigint,@extra bigint;
 IF @baseId IS NULL OR @candidateId IS NULL THROW 51300,''Comparison table missing.'',1;
 /* Names must resolve to local document_trial tables. */
 IF (@baseTarget NOT LIKE N''[[]document_trial].%'')
 OR (@candidateTarget NOT LIKE N''[[]document_trial].%'')
 THROW 51300,''Comparison accepts private tables only.'',1;
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
        THROW 51290,''Column schemas differ. Keep copies for review; production unchanged.'',1;
    END;
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id=@baseId AND system_type_id IN (98,240))
        THROW 51290,''SQL variant or CLR column requires a reviewed comparison.'',1;

    DECLARE @projection nvarchar(max)=N'''',@groupColumns nvarchar(max)=N'''',
        @name sysname,@type int,@columnId int,@expression nvarchar(max);
    DECLARE columns_to_compare CURSOR LOCAL FAST_FORWARD FOR
        SELECT column_id,name,system_type_id FROM sys.columns WHERE object_id=@baseId ORDER BY column_id;
    OPEN columns_to_compare;
    FETCH NEXT FROM columns_to_compare INTO @columnId,@name,@type;
    WHILE @@FETCH_STATUS=0
    BEGIN
        SET @expression=CASE WHEN @type=241
            THEN N''CONVERT(varbinary(max),CONVERT(nvarchar(max),''+QUOTENAME(@name)+N''))''
            WHEN @type=35 THEN N''CONVERT(varbinary(max),CONVERT(varchar(max),''+QUOTENAME(@name)+N''))''
            WHEN @type=99 THEN N''CONVERT(varbinary(max),CONVERT(nvarchar(max),''+QUOTENAME(@name)+N''))''
            WHEN @type IN (34,35,99,165,167,173,175,231,239)
            THEN N''CONVERT(varbinary(max),''+QUOTENAME(@name)+N'')''
            ELSE QUOTENAME(@name) END;
        SET @projection=@projection+CASE WHEN LEN(@projection)>0 THEN N'','' ELSE N'''' END
            +@expression+N'' AS ''+QUOTENAME(@name);
        SET @groupColumns=@groupColumns+CASE WHEN LEN(@groupColumns)>0 THEN N'','' ELSE N'''' END+@expression;
        IF @type IN (34,35,99,165,167,173,175,231,239)
        BEGIN
            SET @projection=@projection+N'',DATALENGTH(''+QUOTENAME(@name)+N'') AS ''
                +QUOTENAME(N''__etl_length_''+CONVERT(nvarchar(10),@columnId));
            SET @groupColumns=@groupColumns+N'',DATALENGTH(''+QUOTENAME(@name)+N'')'';
        END;
        FETCH NEXT FROM columns_to_compare INTO @columnId,@name,@type;
    END;
    CLOSE columns_to_compare;
    DEALLOCATE columns_to_compare;
    SET @sql=N'';WITH a AS (SELECT ''+@projection+N'',COUNT_BIG(*) AS copies FROM ''+@baseTarget+N'' GROUP BY ''+@groupColumns+N''),
        b AS (SELECT ''+@projection+N'',COUNT_BIG(*) AS copies FROM ''+@candidateTarget+N'' GROUP BY ''+@groupColumns+N'')
        SELECT @missing=(SELECT COUNT_BIG(*) FROM (SELECT * FROM a EXCEPT SELECT * FROM b) d),
               @extra=(SELECT COUNT_BIG(*) FROM (SELECT * FROM b EXCEPT SELECT * FROM a) d);'';
    EXEC sys.sp_executesql @sql,N''@missing bigint OUTPUT,@extra bigint OUTPUT'',@missing=@missing OUTPUT,@extra=@extra OUTPUT;

 SELECT @missing AS MissingGroups,@extra AS ExtraGroups;
 IF @missing<>0 OR @extra<>0 THROW 51300,''Full source comparison differs; nothing published.'',1;
END;';
 /* Execute metadata/comparison in DataWH, where both private outputs reside. */
 DECLARE @baseLocal nvarchar(300)=N'[document_trial].'+QUOTENAME(N'SalesBase_'+@suffix),
  @candidateLocal nvarchar(300)=N'[document_trial].'+QUOTENAME(N'SalesNoFactIndex_'+@suffix);
 EXEC DataWH.sys.sp_executesql @comparison,N'@baseTarget nvarchar(300),@candidateTarget nvarchar(300)',@baseTarget=@baseLocal,@candidateTarget=@candidateLocal;
 UPDATE DataWH.document_trial.SalesExtractRuns SET Status='MATCH',CompletedAtUTC=SYSUTCDATETIME(),
  ComparisonMilliseconds=DATEDIFF_BIG(millisecond,@t,SYSUTCDATETIME()) WHERE RunId=@run;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.SalesExtract',@LockOwner='Session';
 SET LOCK_TIMEOUT -1;
 SELECT RunId,Status,CandidateFirst,BaselineMilliseconds/60000.0 AS BaselineMinutes,
  CandidateMilliseconds/60000.0 AS CandidateMinutes,ComparisonMilliseconds/60000.0 AS ComparisonMinutes,BaseTable,CandidateTable
 FROM DataWH.document_trial.SalesExtractRuns WHERE RunId=@run;
 SELECT Variant,COUNT(*) AS BuiltIndexes,SUM(Milliseconds)/60000.0 AS IndexMinutes,
  SUM(CASE WHEN TempName=N'#РеализацияТоваровУслуг' THEN Milliseconds ELSE 0 END)/60000.0 AS FactIndexMinutes
 FROM DataWH.document_trial.SalesExtractIndexes WHERE RunId=@run GROUP BY Variant;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 BEGIN TRY
  IF OBJECT_ID(N'DataWH.document_trial.SalesExtractRuns',N'U') IS NOT NULL
   UPDATE DataWH.document_trial.SalesExtractRuns SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),ErrorNumber=ERROR_NUMBER() WHERE RunId=@run;
 END TRY
 BEGIN CATCH
  RAISERROR('Could not record failure.',10,1) WITH NOWAIT;
 END CATCH;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.SalesExtract',@LockOwner='Session';
 SET LOCK_TIMEOUT -1;
 THROW;
END CATCH;
