/* Controlled lines-only experiment: original remote view versus local joins.
   Real full reads/copies; no live views, tables, Agent or indexes changed.
   Run outside ETL with stable ERP/dictionaries. ~8 GiB for two output copies,
   PLUS tempdb staging, sorts and transaction logs. See DOCUMENTS_OPTIMIZATION.md.
*/
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51270,'Use a new window without a transaction.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
    THROW 51270,'This experiment is only for vm-dwh.',1;
IF OBJECT_ID(N'document_trial.MeasurementMarker',N'U') IS NULL
    THROW 51270,'Run the original Measure-CommercialOffers.sql first.',1;
IF (SELECT COUNT(*) FROM document_trial.MeasurementMarker)<>1
   OR NOT EXISTS (SELECT 1 FROM document_trial.MeasurementMarker WHERE Version=1)
    THROW 51270,'Unexpected document trial marker.',1;

-- For a second controlled run change to 1 to reverse cache/order effects.
DECLARE @CandidateFirst bit=0;
DECLARE @lock int,@run uniqueidentifier=NEWID(),@suffix varchar(32),
    @baseName sysname,@candidateName sysname,@baseTarget nvarchar(300),@candidateTarget nvarchar(300),
    @baselineSql nvarchar(max),@candidateSql nvarchar(max),@sql nvarchar(max),
    @started datetime2=SYSUTCDATETIME(),@partStarted datetime2,@variant varchar(12),
    @pass int=0,@ms bigint,@baseId int,@candidateId int,@missing bigint,@extra bigint;
IF @CandidateFirst IS NULL THROW 51270,'CandidateFirst must be 0 or 1.',1;
EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.MeasureCommercialOffers',
    @LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51270,'Another document benchmark is running.',1;
SET @suffix=REPLACE(CONVERT(varchar(36),@run),'-','');
SET @baseName=N'OfferLinesBase_'+@suffix;
SET @candidateName=N'OfferLinesLocal_'+@suffix;
SET @baseTarget=N'[document_trial].'+QUOTENAME(@baseName);
SET @candidateTarget=N'[document_trial].'+QUOTENAME(@candidateName);
BEGIN TRY
    IF OBJECT_ID(N'document_trial.LineCompareRuns',N'U') IS NULL
        CREATE TABLE document_trial.LineCompareRuns (
            RunId uniqueidentifier NOT NULL PRIMARY KEY,SessionId int NOT NULL,
            StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,
            Status varchar(16) NOT NULL,CandidateFirst bit NOT NULL,OptionsFlags int NOT NULL,
            BaselineTable sysname NOT NULL,CandidateTable sysname NOT NULL,
            BaselineObjectId int NULL,CandidateObjectId int NULL,
            BaselineMilliseconds bigint NULL,CandidateMilliseconds bigint NULL,
            MissingGroups bigint NULL,ExtraGroups bigint NULL,ErrorNumber int NULL
        );
    IF OBJECT_ID(N'document_trial.LineComparePhases',N'U') IS NULL
        CREATE TABLE document_trial.LineComparePhases (
            RunId uniqueidentifier NOT NULL,StepNo int NOT NULL,Variant varchar(12) NOT NULL,
            Phase varchar(60) NOT NULL,StartedAtUTC datetime2 NOT NULL,CompletedAtUTC datetime2 NULL,
            Status varchar(16) NOT NULL,RowsCopied bigint NULL,ErrorNumber int NULL,
            PRIMARY KEY (RunId,StepNo)
        );
    INSERT document_trial.LineCompareRuns
        (RunId,SessionId,StartedAtUTC,Status,CandidateFirst,OptionsFlags,BaselineTable,CandidateTable)
    VALUES (@run,@@SPID,@started,'running',@CandidateFirst,@@OPTIONS,@baseName,@candidateName);
    SELECT @run AS RunId,@@SPID AS SessionId,@CandidateFirst AS CandidateFirst,
        @baseName AS BaselineTable,@candidateName AS CandidateTable;

    -- Each candidate batch retains its temp tables until all its phases finish.
    -- No DISTINCT, tenant filter, ACTIVE filter or deduplication of dictionaries.
    SET @baselineSql=N'DECLARE @rows bigint;
RAISERROR(''Offer lines comparison: baseline / copy_view'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,100,''baseline'',''copy_view'',SYSUTCDATETIME(),''running'');
SELECT * INTO __BASE_TARGET__ FROM [1CUnloading].[1c].[Документы.бит_КоммерческоеПредложениеКлиентуТовары];
SET @rows=ROWCOUNT_BIG();
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=100;
RAISERROR(''Offer lines comparison: baseline / index_reference'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,101,''baseline'',''index_reference'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_Ссылка ON __BASE_TARGET__ ([Ссылка]);
SET @rows=NULL;
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=101;
RAISERROR(''Offer lines comparison: baseline / index_linenumber'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,102,''baseline'',''index_linenumber'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_НомерСтроки ON __BASE_TARGET__ ([НомерСтроки]);
SET @rows=NULL;
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=102;';
    SET @candidateSql=N'DECLARE @rows bigint;
RAISERROR(''Offer lines comparison: candidate / read_raw'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,200,''candidate'',''read_raw'',SYSUTCDATETIME(),''running'');
SELECT
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
    [_Document80440_IDRRef]
INTO #OfferRaw FROM [erp-db-02.erp.lcl].[ErpBackUp2].[dbo].[_Document80440_VT80488];
SET @rows=ROWCOUNT_BIG();
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=200;
RAISERROR(''Offer lines comparison: candidate / read_nomen'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,201,''candidate'',''read_nomen'',SYSUTCDATETIME(),''running'');
SELECT [Ссылка],[Наименование] INTO #OfferNomen FROM [erp-db-02.erp.lcl].[exch_biview2].[dbo].[Справочники.Номенклатура];
SET @rows=ROWCOUNT_BIG();
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=201;
RAISERROR(''Offer lines comparison: candidate / read_kinds'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,202,''candidate'',''read_kinds'',SYSUTCDATETIME(),''running'');
SELECT [Ссылка],[Наименование] INTO #OfferKinds FROM [erp-db-02.erp.lcl].[exch_biview2].[dbo].[Справочники.ВидыЦен];
SET @rows=ROWCOUNT_BIG();
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=202;
RAISERROR(''Offer lines comparison: candidate / read_currencies'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,203,''candidate'',''read_currencies'',SYSUTCDATETIME(),''running'');
SELECT [Ссылка],[Наименование] INTO #OfferCurrencies FROM [erp-db-02.erp.lcl].[exch_biview2].[dbo].[Справочники.Валюты];
SET @rows=ROWCOUNT_BIG();
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=203;
RAISERROR(''Offer lines comparison: candidate / index_nomen'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,204,''candidate'',''index_nomen'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_Reference ON #OfferNomen ([Ссылка]) INCLUDE ([Наименование]);
SET @rows=NULL;
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=204;
RAISERROR(''Offer lines comparison: candidate / index_kinds'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,205,''candidate'',''index_kinds'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_Reference ON #OfferKinds ([Ссылка]) INCLUDE ([Наименование]);
SET @rows=NULL;
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=205;
RAISERROR(''Offer lines comparison: candidate / index_currencies'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,206,''candidate'',''index_currencies'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_Reference ON #OfferCurrencies ([Ссылка]) INCLUDE ([Наименование]);
SET @rows=NULL;
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=206;
RAISERROR(''Offer lines comparison: candidate / join_local'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,207,''candidate'',''join_local'',SYSUTCDATETIME(),''running'');
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
INTO __CANDIDATE_TARGET__
FROM #OfferRaw AS r
LEFT JOIN #OfferNomen AS n ON n.[Ссылка]=r.[_Fld80496RRef]
LEFT JOIN #OfferKinds AS k ON k.[Ссылка]=r.[_Fld80514RRef]
LEFT JOIN #OfferCurrencies AS c ON c.[Ссылка]=r.[_Fld80599RRef];
SET @rows=ROWCOUNT_BIG();
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=207;
RAISERROR(''Offer lines comparison: candidate / index_reference'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,208,''candidate'',''index_reference'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_Ссылка ON __CANDIDATE_TARGET__ ([Ссылка]);
SET @rows=NULL;
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=208;
RAISERROR(''Offer lines comparison: candidate / index_linenumber'',10,1) WITH NOWAIT;
INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status) VALUES (@run,209,''candidate'',''index_linenumber'',SYSUTCDATETIME(),''running'');
CREATE NONCLUSTERED INDEX IX_НомерСтроки ON __CANDIDATE_TARGET__ ([НомерСтроки]);
SET @rows=NULL;
UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status=''success'',RowsCopied=@rows WHERE RunId=@run AND StepNo=209;';

    SET @baselineSql=REPLACE(@baselineSql,N'__BASE_TARGET__',@baseTarget);
    SET @candidateSql=REPLACE(@candidateSql,N'__CANDIDATE_TARGET__',@candidateTarget);
    WHILE @pass<2
    BEGIN
        SET @variant=CASE WHEN (@pass=0 AND @CandidateFirst=1) OR (@pass=1 AND @CandidateFirst=0)
            THEN 'candidate' ELSE 'baseline' END;
        SET @partStarted=SYSUTCDATETIME();
        IF @variant='baseline'
        BEGIN
            EXEC sys.sp_executesql @baselineSql,N'@run uniqueidentifier',@run=@run;
            SET @ms=DATEDIFF_BIG(millisecond,@partStarted,SYSUTCDATETIME());
            UPDATE document_trial.LineCompareRuns SET BaselineMilliseconds=@ms,
                BaselineObjectId=OBJECT_ID(@baseTarget,N'U') WHERE RunId=@run;
        END
        ELSE
        BEGIN
            EXEC sys.sp_executesql @candidateSql,N'@run uniqueidentifier',@run=@run;
            SET @ms=DATEDIFF_BIG(millisecond,@partStarted,SYSUTCDATETIME());
            UPDATE document_trial.LineCompareRuns SET CandidateMilliseconds=@ms,
                CandidateObjectId=OBJECT_ID(@candidateTarget,N'U') WHERE RunId=@run;
        END;
        SET @pass=@pass+1;
    END;
    SET @baseId=OBJECT_ID(@baseTarget,N'U');
    SET @candidateId=OBJECT_ID(@candidateTarget,N'U');
    INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status)
        VALUES (@run,300,'validation','compare_schema',SYSUTCDATETIME(),'running');
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
        THROW 51270,'Column schemas differ. Keep copies for review; production unchanged.',1;
    END;
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id=@baseId AND system_type_id IN (98,240))
        THROW 51270,'SQL variant or CLR column requires a reviewed comparison.',1;
    UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status='success'
        WHERE RunId=@run AND StepNo=300;

    RAISERROR('Offer lines comparison: full multiset validation (excluded from candidate time).',10,1) WITH NOWAIT;
    INSERT document_trial.LineComparePhases (RunId,StepNo,Variant,Phase,StartedAtUTC,Status)
        VALUES (@run,301,'validation','compare_values',SYSUTCDATETIME(),'running');
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
    UPDATE document_trial.LineComparePhases SET CompletedAtUTC=SYSUTCDATETIME(),Status='success'
        WHERE RunId=@run AND StepNo=301;
    UPDATE document_trial.LineCompareRuns SET Status=CASE WHEN @missing=0 AND @extra=0 THEN 'success' ELSE 'different' END,
        CompletedAtUTC=SYSUTCDATETIME(),MissingGroups=@missing,ExtraGroups=@extra WHERE RunId=@run;
    EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
    SELECT Variant,Phase,RowsCopied,
        CAST(DATEDIFF_BIG(millisecond,StartedAtUTC,CompletedAtUTC)/60000.0 AS decimal(14,3)) AS Minutes
    FROM document_trial.LineComparePhases WHERE RunId=@run ORDER BY StartedAtUTC,StepNo;
    SELECT RunId,Status,CandidateFirst,
        CASE WHEN MissingGroups=0 AND ExtraGroups=0 THEN 'MATCH' ELSE 'DIFFERENT' END AS ValidationResult,
        CAST(BaselineMilliseconds/60000.0 AS decimal(14,3)) AS BaselineMinutes,
        CAST(CandidateMilliseconds/60000.0 AS decimal(14,3)) AS CandidateMinutes,
        MissingGroups,ExtraGroups,BaselineTable,CandidateTable,
        CAST(DATEDIFF_BIG(millisecond,StartedAtUTC,CompletedAtUTC)/60000.0 AS decimal(14,3)) AS IncludingValidationMinutes
    FROM document_trial.LineCompareRuns WHERE RunId=@run;
END TRY
BEGIN CATCH
    IF OBJECT_ID(N'document_trial.LineCompareRuns',N'U') IS NOT NULL
        UPDATE document_trial.LineCompareRuns SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),
            BaselineObjectId=OBJECT_ID(@baseTarget,N'U'),CandidateObjectId=OBJECT_ID(@candidateTarget,N'U'),
            ErrorNumber=ERROR_NUMBER() WHERE RunId=@run;
    IF OBJECT_ID(N'document_trial.LineComparePhases',N'U') IS NOT NULL
        UPDATE document_trial.LineComparePhases SET Status='failed',CompletedAtUTC=SYSUTCDATETIME(),
            ErrorNumber=ERROR_NUMBER() WHERE RunId=@run AND Status='running';
    EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
    THROW;
END CATCH;
