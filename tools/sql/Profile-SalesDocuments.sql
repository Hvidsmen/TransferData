/* PROFILE the unchanged original extraction once, on vm-dwh.
   All 77 indexes and SELECT * remain. One PRIVATE output. No Agent/live writes.
   Per-read phases + fact/read/order totals + actual XML plan for FINAL SELECT only.
   Read docs/SALES_DOCUMENTS_PROFILE.md. Run whole file outside ETL. */
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
DECLARE @definition nvarchar(max)=OBJECT_DEFINITION(OBJECT_ID(N'[1c].[sp_Документы.РеализацияТоваровУслуг]',N'P')),
 @flat nvarchar(max),@body nvarchar(max),@code nvarchar(max)=N'',@c nchar(1),@next nchar(1),@state int=0,@i int=1,
 @as int,@prefix nvarchar(max),@lock int,@run uniqueidentifier=NEWID(),@suffix varchar(32),
 @base nvarchar(300),@live nvarchar(300)=N'[1CUnloading].[1c].[Документы.РеализацияТоваровУслуг]',
 @indexStart int,@on int,@open int,@close int,@from int=1,@indexNo int=0,@factCount int=0,
 @indexName sysname,@tempName sysname,@stmt nvarchar(max),@builtBase nvarchar(max)=N'',
 @worker nvarchar(max),@variant varchar(12)='profile',@started datetime2;
IF @definition IS NULL THROW 51400,'Source procedure is missing, encrypted or not visible.',1;
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
  +N'INSERT DataWH.document_trial.SalesProfileIndexes(RunId,Variant,IndexNo,TempName,IndexName,Milliseconds) VALUES(@run,@variant,'
  +CONVERT(nvarchar(10),@indexNo)+N',N'+NCHAR(39)+REPLACE(@tempName,NCHAR(39),NCHAR(39)+NCHAR(39))+NCHAR(39)+N',N'
  +NCHAR(39)+REPLACE(@indexName,NCHAR(39),NCHAR(39)+NCHAR(39))+NCHAR(39)+N',DATEDIFF_BIG(millisecond,@__indexStarted,SYSUTCDATETIME()));'+NCHAR(10);
 SET @builtBase=@builtBase+SUBSTRING(@body,@from,@indexStart-@from)+@stmt;
 SET @from=@close+1;
END;
IF @indexNo<>77 OR @factCount<>43 THROW 51400,'Expected 77 temp indexes including 43 on the fact table; source changed.',1;
IF (LEN(@code)-LEN(REPLACE(@code COLLATE Latin1_General_100_CI_AS,N'CREATE ',N'')))/7<>77
 THROW 51400,'Unexpected additional CREATE statement.',1;
SET @builtBase=@builtBase+SUBSTRING(@body,@from,LEN(@body));
/* Locate the final SELECT INTO and the 35 original temp reads from masked
   code. No parsing/rewriting of JOINs or expressions is performed. */
IF ISNULL(HAS_PERMS_BY_NAME(N'1CUnloading',N'DATABASE',N'SHOWPLAN'),0)<>1
 OR ISNULL(HAS_PERMS_BY_NAME(N'DataWH',N'DATABASE',N'SHOWPLAN'),0)<>1
 OR ISNULL(HAS_PERMS_BY_NAME(N'tempdb',N'DATABASE',N'SHOWPLAN'),0)<>1
 THROW 51400,'SHOWPLAN permission in 1CUnloading, DataWH and tempdb is required.',1;
DECLARE @outputInto int=CHARINDEX(N'INTO '+@live,@code COLLATE Latin1_General_100_CI_AS),
 @finalStart int=0,@scan int=1,@found int,@readInto int,@readStart int,@readEnd int,
 @nextIndex int,@nextIf int,@nameEnd int,@readNo int=0,@readKind varchar(16),
 @oldRead nvarchar(max),@wrappedRead nvarchar(max),@finalOriginal nvarchar(max),@finalSql nvarchar(max);
IF @outputInto=0 OR CHARINDEX(N'INTO '+@live,@code COLLATE Latin1_General_100_CI_AS,@outputInto+1)>0
 THROW 51400,'Expected a single final SELECT INTO.',1;
WHILE 1=1
BEGIN
 SET @found=CHARINDEX(N'SELECT ',@code COLLATE Latin1_General_100_CI_AS,@scan);
 IF @found=0 OR @found>=@outputInto BREAK;
 SET @finalStart=@found; SET @scan=@found+1;
END;
IF @finalStart=0 OR (LEN(@code)-LEN(REPLACE(@code COLLATE Latin1_General_100_CI_AS,N'LEFT JOIN',N'')))/9<>45
 THROW 51400,'Final SELECT/45 LEFT JOIN shape changed; review first.',1;
SET @finalOriginal=SUBSTRING(@body,@finalStart,LEN(@body));
/* REPLACE's search pattern is page-size limited even for nvarchar(max).
   The unchanged final statement is a suffix: validate bytes and cut by offset.
   DATALENGTH preserves trailing spaces, unlike LEN. */
DECLARE @finalChars bigint=DATALENGTH(@finalOriginal)/2,@builtChars bigint=DATALENGTH(@builtBase)/2;
IF @finalChars=0 OR @builtChars<@finalChars
 THROW 51400,'Unexpected final SELECT suffix length.',1;
IF CONVERT(varbinary(max),RIGHT(@builtBase,@finalChars))<>CONVERT(varbinary(max),@finalOriginal)
 THROW 51400,'Final SELECT suffix differs in private body.',1;
SET @builtBase=LEFT(@builtBase,@builtChars-@finalChars);
SET @scan=1;
WHILE 1=1
BEGIN
 SET @readInto=CHARINDEX(N'INTO #',@code COLLATE Latin1_General_100_CI_AS,@scan);
 IF @readInto=0 OR @readInto>=@finalStart BREAK;
 SET @nameEnd=CHARINDEX(N' ',@code,@readInto+5);
 IF @nameEnd=0 THROW 51400,'Unexpected temp destination syntax.',1;
 SET @tempName=SUBSTRING(@code,@readInto+5,@nameEnd-@readInto-5);
 SET @readStart=0; SET @scan=1;
 WHILE 1=1
 BEGIN
  SET @found=CHARINDEX(N'SELECT ',@code COLLATE Latin1_General_100_CI_AS,@scan);
  IF @found=0 OR @found>=@readInto BREAK;
  SET @readStart=@found; SET @scan=@found+1;
 END;
 IF @readStart=0 THROW 51400,'Temp SELECT start missing.',1;
 SET @nextIndex=CHARINDEX(N'CREATE NONCLUSTERED INDEX ',@code COLLATE Latin1_General_100_CI_AS,@nameEnd);
 SET @nextIf=CHARINDEX(N'IF OBJECT_ID',@code COLLATE Latin1_General_100_CI_AS,@nameEnd);
 SET @readEnd=@finalStart;
 IF @nextIndex>0 AND @nextIndex<@readEnd SET @readEnd=@nextIndex;
 IF @nextIf>0 AND @nextIf<@readEnd SET @readEnd=@nextIf;
 IF @readEnd<=@readStart THROW 51400,'Unexpected temp-read statement boundaries.',1;
 SET @oldRead=SUBSTRING(@body,@readStart,@readEnd-@readStart);
 IF DATALENGTH(@oldRead)>8000 THROW 51400,'Temp-read search pattern exceeds 8000 bytes; review source before profiling.',1;
 IF (LEN(@builtBase)-LEN(REPLACE(@builtBase,@oldRead,N'')))/NULLIF(LEN(@oldRead),0)<>1
  THROW 51400,'Temp read is not unique; source shape changed.',1;
 SET @readNo=@readNo+1;
 SET @readKind=CASE WHEN @tempName=N'#РеализацияТоваровУслуг' THEN 'read_fact'
  WHEN @tempName=N'#ЗаказКлиента' THEN 'read_orders' ELSE 'read_lookup' END;
 SET @wrappedRead=NCHAR(10)+N'RAISERROR(''Sales profile: '+REPLACE(@tempName,NCHAR(39),NCHAR(39)+NCHAR(39))+N''',10,1) WITH NOWAIT;'
  +NCHAR(10)+N'INSERT DataWH.document_trial.SalesProfilePhases(RunId,StepNo,Phase,TempName,StartedAtUTC,Status) VALUES(@run,'
  +CONVERT(nvarchar(10),@readNo)+N','+NCHAR(39)+@readKind+NCHAR(39)+N',N'+NCHAR(39)
  +REPLACE(@tempName,NCHAR(39),NCHAR(39)+NCHAR(39))+NCHAR(39)+N',SYSUTCDATETIME(),''running'');'
  +NCHAR(10)+N'SET @__readStarted=SYSUTCDATETIME();'+NCHAR(10)+@oldRead+NCHAR(10)
  +N'; SET @__readRows=ROWCOUNT_BIG(); SET @__readMs=DATEDIFF_BIG(millisecond,@__readStarted,SYSUTCDATETIME());'
  +NCHAR(10)+N'UPDATE DataWH.document_trial.SalesProfilePhases SET Status=''success'',CompletedAtUTC=SYSUTCDATETIME(),Milliseconds=@__readMs,RowsCopied=@__readRows WHERE RunId=@run AND StepNo='
  +CONVERT(nvarchar(10),@readNo)+N';'+NCHAR(10);
 SET @builtBase=REPLACE(@builtBase,@oldRead,@wrappedRead);
 SET @scan=@nameEnd;
END;
IF @readNo<>35 THROW 51400,'Expected 35 original temporary reads.',1;
SET @suffix=REPLACE(CONVERT(varchar(36),@run),'-','');
SET @base=N'[DataWH].[document_trial].'+QUOTENAME(N'SalesProfile_'+@suffix);
/* Parent batch owns the temp tables; nested final SELECT reads them without
   rematerializing and returns its ACTUAL plan through STATISTICS XML. */
SET @worker=N'DECLARE @__indexStarted datetime2,@__readStarted datetime2,@__readRows bigint,@__readMs bigint;'
 +NCHAR(10)+REPLACE(@builtBase,@live,@base)+NCHAR(10)
 +N'RAISERROR(''Sales profile: final_join; actual XML plan enabled for this statement.'',10,1) WITH NOWAIT;
 INSERT DataWH.document_trial.SalesProfilePhases(RunId,StepNo,Phase,StartedAtUTC,Status) VALUES(@run,1000,''final_join'',SYSUTCDATETIME(),''running'');
 SET @__readStarted=SYSUTCDATETIME();
 EXEC sys.sp_executesql @finalSql,N''@n bigint OUTPUT'',@n=@__readRows OUTPUT;
 SET @__readMs=DATEDIFF_BIG(millisecond,@__readStarted,SYSUTCDATETIME());
 UPDATE DataWH.document_trial.SalesProfilePhases SET Status=''success'',CompletedAtUTC=SYSUTCDATETIME(),Milliseconds=@__readMs,RowsCopied=@__readRows WHERE RunId=@run AND StepNo=1000;';
SET @finalSql=N'SET STATISTICS XML ON;'+NCHAR(10)+REPLACE(@finalOriginal,@live,@base)+NCHAR(10)
 +N'; SET @n=ROWCOUNT_BIG(); SET STATISTICS XML OFF;';
IF APPLOCK_MODE(N'public',N'document_trial.SalesExtract',N'Session')<>N'NoLock'
 THROW 51400,'This session owns a previous trial lock; reconnect.',1;
EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.SalesExtract',@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51400,'Another sales trial is running.',1;
BEGIN TRY
 SET LOCK_TIMEOUT 30000;
 IF OBJECT_ID(N'DataWH.document_trial.SalesProfileRuns',N'U') IS NULL
  CREATE TABLE DataWH.document_trial.SalesProfileRuns(RunId uniqueidentifier NOT NULL PRIMARY KEY,SessionId int NOT NULL,
   StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,Status varchar(16) NOT NULL,
   SourceHash varbinary(32) NOT NULL,OutputTable nvarchar(300) NOT NULL,TotalMilliseconds bigint NULL,ErrorNumber int NULL);
 IF OBJECT_ID(N'DataWH.document_trial.SalesProfilePhases',N'U') IS NULL
  CREATE TABLE DataWH.document_trial.SalesProfilePhases(RunId uniqueidentifier NOT NULL,StepNo int NOT NULL,
   Phase varchar(16) NOT NULL,TempName sysname NULL,StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,
   Status varchar(16) NOT NULL,Milliseconds bigint NULL,RowsCopied bigint NULL,PRIMARY KEY(RunId,StepNo));
 IF OBJECT_ID(N'DataWH.document_trial.SalesProfileIndexes',N'U') IS NULL
  CREATE TABLE DataWH.document_trial.SalesProfileIndexes(RunId uniqueidentifier NOT NULL,Variant varchar(12) NOT NULL,
   IndexNo int NOT NULL,TempName sysname NOT NULL,IndexName sysname NOT NULL,Milliseconds bigint NOT NULL,PRIMARY KEY(RunId,Variant,IndexNo));
 SET @started=SYSUTCDATETIME();
 INSERT DataWH.document_trial.SalesProfileRuns(RunId,SessionId,StartedAtUTC,Status,SourceHash,OutputTable)
 VALUES(@run,@@SPID,@started,'running',HASHBYTES('SHA2_256',@definition),@base);
 SELECT @run AS RunId,@@SPID AS SessionId,@base AS OutputTable;
 EXEC sys.sp_executesql @worker,N'@run uniqueidentifier,@variant varchar(12),@finalSql nvarchar(max)',@run=@run,@variant=@variant,@finalSql=@finalSql;
 IF @@TRANCOUNT<>0 THROW 51400,'Profile returned an open transaction.',1;
 UPDATE DataWH.document_trial.SalesProfileRuns SET Status='success',CompletedAtUTC=SYSUTCDATETIME(),
 TotalMilliseconds=DATEDIFF_BIG(millisecond,@started,SYSUTCDATETIME()) WHERE RunId=@run;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.SalesExtract',@LockOwner='Session';
 SET LOCK_TIMEOUT -1;
 SELECT RunId,Status,TotalMilliseconds/60000.0 AS TotalMinutes,OutputTable
 FROM DataWH.document_trial.SalesProfileRuns WHERE RunId=@run;
 SELECT Phase,COUNT(*) AS Stages,SUM(Milliseconds)/60000.0 AS Minutes
 FROM DataWH.document_trial.SalesProfilePhases WHERE RunId=@run GROUP BY Phase;
 SELECT COUNT(*) AS BuiltIndexes,SUM(Milliseconds)/60000.0 AS IndexMinutes
 FROM DataWH.document_trial.SalesProfileIndexes WHERE RunId=@run;
 SELECT StepNo,Phase,TempName,Status,Milliseconds/60000.0 AS Minutes,RowsCopied
 FROM DataWH.document_trial.SalesProfilePhases WHERE RunId=@run ORDER BY StepNo;
END TRY
BEGIN CATCH
 SET STATISTICS XML OFF;
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 DECLARE @error int=ERROR_NUMBER();
 BEGIN TRY
  IF OBJECT_ID(N'DataWH.document_trial.SalesProfileRuns',N'U') IS NOT NULL
   UPDATE DataWH.document_trial.SalesProfileRuns SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),ErrorNumber=@error WHERE RunId=@run;
  IF OBJECT_ID(N'DataWH.document_trial.SalesProfilePhases',N'U') IS NOT NULL
   UPDATE DataWH.document_trial.SalesProfilePhases SET Status='failed',CompletedAtUTC=SYSUTCDATETIME() WHERE RunId=@run AND Status='running';
 END TRY
 BEGIN CATCH
  RAISERROR('Could not record profile failure.',10,1) WITH NOWAIT;
 END CATCH;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.SalesExtract',@LockOwner='Session';
 SET LOCK_TIMEOUT -1;
 THROW;
END CATCH;
