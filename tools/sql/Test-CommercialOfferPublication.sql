/* Tiny local fixtures only; no Agent, ERP reads or production/cache writes.
   Run after installation and before Switch-CommercialOfferJob.sql. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51300,'Use a new query window.',1;
/* Actual indexed header validator: unchanged snapshots pass, additions,
   deletions and lower/higher versions fail. Caller fixtures only; no ERP. */
CREATE TABLE #Headers([_Fld2951] int NOT NULL,[_IDRRef] binary(16) NOT NULL,VersionBytes binary(8) NOT NULL);
CREATE UNIQUE CLUSTERED INDEX HeaderFixtureKey ON #Headers([_Fld2951],[_IDRRef]);
CREATE TABLE #HeadersAfter([_Fld2951] int NOT NULL,[_IDRRef] binary(16) NOT NULL,VersionBytes binary(8) NOT NULL);
CREATE UNIQUE CLUSTERED INDEX HeaderAfterFixtureKey ON #HeadersAfter([_Fld2951],[_IDRRef]);
INSERT #Headers VALUES(1,0x01,0x02),(2,0x01,0x03);
INSERT #HeadersAfter SELECT * FROM #Headers;
EXEC document_trial.ValidateOfferHeaders;
DECLARE @scenario int=1;
WHILE @scenario<=4
BEGIN
 DELETE FROM #HeadersAfter;
 INSERT #HeadersAfter SELECT * FROM #Headers;
 IF @scenario=1 INSERT #HeadersAfter VALUES(1,0x03,0x01);
 IF @scenario=2 DELETE FROM #HeadersAfter WHERE [_Fld2951]=2;
 IF @scenario=3 UPDATE #HeadersAfter SET VersionBytes=0x01 WHERE [_Fld2951]=1;
 IF @scenario=4 UPDATE #HeadersAfter SET VersionBytes=0x04 WHERE [_Fld2951]=1;
 BEGIN TRY
  EXEC document_trial.ValidateOfferHeaders;
  THROW 51301,'Changed header snapshot was accepted.',1;
 END TRY
 BEGIN CATCH
  IF ERROR_NUMBER()<>51300 OR ERROR_MESSAGE()<>'ERP headers changed during read; retry after the source is stable.' THROW;
 END CATCH;
 SET @scenario=@scenario+1;
END;
DROP TABLE #Headers,#HeadersAfter;
/* Fast mode must never suppress ordinary production checks. No ERP reads. */
BEGIN TRY
 EXEC document_trial.RefreshOfferLines @Cutover=0,@SkipCutoverFullCheck=1;
 THROW 51301,'Ordinary production accepted the cutover-only skip option.',1;
END TRY
BEGIN CATCH
 IF ERROR_NUMBER()<>51300 OR ERROR_MESSAGE()<>'Skipping full comparison is allowed only for an explicit fast cutover.' THROW;
END CATCH;
CREATE TABLE #OfferPublishSource(Id int NOT NULL,Label nvarchar(30) NULL);
CREATE TABLE #OfferTarget(Id int NOT NULL CHECK(Id>0),Label nvarchar(30) NULL);
CREATE NONCLUSTERED INDEX FixtureIndex ON #OfferTarget(Label);
CREATE TABLE #Checkpoint(Id int NOT NULL);
INSERT #Checkpoint VALUES(1);
INSERT #OfferTarget VALUES(1,N'old'),(2,NULL);
INSERT #OfferPublishSource VALUES(1,N'corrected'),(3,N'new'),(3,N'new');
DECLARE @oid int=OBJECT_ID(N'tempdb..#OfferTarget');
BEGIN TRY
 BEGIN TRANSACTION;
 EXEC document_trial.ReplaceOfferRows @source=N'#OfferPublishSource',@target=N'#OfferTarget';
 IF (SELECT COUNT(*) FROM #OfferTarget)<>3
  OR NOT EXISTS(SELECT 1 FROM #OfferTarget WHERE Id=1 AND Label=N'corrected')
  OR EXISTS(SELECT 1 FROM #OfferTarget WHERE Id=2)
  OR (SELECT COUNT(*) FROM #OfferTarget WHERE Id=3)<>2
  THROW 51300,'Replacement/duplicates failed.',1;
 IF OBJECT_ID(N'tempdb..#OfferTarget')<>@oid
  OR NOT EXISTS(SELECT 1 FROM tempdb.sys.indexes WHERE object_id=@oid AND name=N'FixtureIndex')
  THROW 51300,'Table identity/index changed.',1;
 COMMIT TRANSACTION;
 /* Empty materialization is a valid replacement, including removed documents. */
 DELETE FROM #OfferPublishSource;
 BEGIN TRANSACTION;
 EXEC document_trial.ReplaceOfferRows @source=N'#OfferPublishSource',@target=N'#OfferTarget';
 IF EXISTS(SELECT 1 FROM #OfferTarget) THROW 51300,'Empty replacement failed.',1;
 ROLLBACK TRANSACTION;
 /* Real INSERT constraint error occurs AFTER DELETE. Check both target and
    the simulated cache checkpoint survive caller rollback. */
 INSERT #OfferPublishSource VALUES(-1,N'invalid');
 BEGIN TRY
  BEGIN TRANSACTION;
  UPDATE #Checkpoint SET Id=2;
  EXEC document_trial.ReplaceOfferRows @source=N'#OfferPublishSource',@target=N'#OfferTarget';
  THROW 51300,'Invalid insertion was accepted.',1;
 END TRY
 BEGIN CATCH
  IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
  IF ERROR_NUMBER()<>547 THROW;
 END CATCH;
 IF (SELECT Id FROM #Checkpoint)<>1 OR (SELECT COUNT(*) FROM #OfferTarget)<>3
  OR (SELECT COUNT(*) FROM #OfferTarget WHERE Id=3)<>2
  THROW 51300,'Failed publication changed committed data/checkpoint.',1;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
/* Real comparator, tiny transactional private tables. Duplicate multiplicity
   must fail even though simple EXCEPT on rows would see the same distinct set. */
IF OBJECT_ID(N'document_trial.OfferFixtureBase',N'U') IS NOT NULL
 OR OBJECT_ID(N'document_trial.OfferFixtureCandidate',N'U') IS NOT NULL
 THROW 51300,'Unexpected fixture objects; review before testing.',1;
BEGIN TRY
 BEGIN TRANSACTION;
 CREATE TABLE document_trial.OfferFixtureBase(Id int,Label nvarchar(30));
 CREATE TABLE document_trial.OfferFixtureCandidate(Id int,Label nvarchar(30));
 INSERT document_trial.OfferFixtureBase VALUES(1,N'Case '),(2,NULL);
 INSERT document_trial.OfferFixtureCandidate SELECT * FROM document_trial.OfferFixtureBase;
 EXEC document_trial.CompareOfferTables @baseTarget=N'[document_trial].[OfferFixtureBase]',@candidateTarget=N'[document_trial].[OfferFixtureCandidate]';
 INSERT document_trial.OfferFixtureCandidate VALUES(1,N'Case ');
 EXEC document_trial.CompareOfferTables @baseTarget=N'[document_trial].[OfferFixtureBase]',@candidateTarget=N'[document_trial].[OfferFixtureCandidate]';
 THROW 51301,'Duplicate difference was accepted.',1;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 IF ERROR_NUMBER()<>51300 OR ERROR_MESSAGE()<>'Full source comparison differs; nothing published.' THROW;
END CATCH;
DROP TABLE #OfferPublishSource,#OfferTarget,#Checkpoint;
PRINT 'PASS: indexed header recheck (add/delete/version), replacement, duplicates, empty result, identity/index preservation, failed INSERT rollback and exact duplicate comparison.';
