/* FAST step 41 cutover: reuses a previous recorded MATCH. No new full source
   copy/value comparison. Still refreshes delta and publishes ALL local rows.
   Outside ETL, stable ERP copy. Read docs/COMMERCIAL_OFFER_CUTOVER.md. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51300,'Use a new window without a transaction.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh' THROW 51300,'Only vm-dwh.',1;
IF OBJECT_ID(N'document_trial.RefreshOfferLines',N'P') IS NULL THROW 51300,'Install publication first.',1;
IF OBJECT_DEFINITION(OBJECT_ID(N'document_trial.RefreshOfferLines')) NOT LIKE N'%@SkipCutoverFullCheck%'
 OR OBJECT_DEFINITION(OBJECT_ID(N'document_trial.RefreshOfferLines')) IS NULL
 THROW 51300,'Run updated Install-CommercialOfferPublication.sql first.',1;
DECLARE @job uniqueidentifier,@step int=41,@old nvarchar(max),@enabled bit,@rc int,@lock int,
 @cut uniqueidentifier=NEWID(),@saved bit=0,@expected nvarchar(max)=N'EXEC [1CUnloading].dbo.transferTalbeToDatWHErp ''Документ.бит_КоммерческоеПредложениеКлиенту''
EXEC [1CUnloading].dbo.transferTalbeToDatWHErp ''Документы.бит_КоммерческоеПредложениеКлиентуТовары''
CREATE NONCLUSTERED INDEX IX_Ссылка ON DataWH.erp.[Документ.бит_КоммерческоеПредложениеКлиенту](Ссылка)
CREATE NONCLUSTERED INDEX IX_DocumentKey ON DataWH.erp.[Документ.бит_КоммерческоеПредложениеКлиенту](DocumentKey)
CREATE NONCLUSTERED INDEX IX_ДатНомерДата ON DataWH.erp.[Документ.бит_КоммерческоеПредложениеКлиенту](Номер)
CREATE NONCLUSTERED INDEX IX_Ссылка ON DataWH.erp.[Документы.бит_КоммерческоеПредложениеКлиентуТовары](Ссылка)
CREATE NONCLUSTERED INDEX IX_НомерСтроки ON DataWH.erp.[Документы.бит_КоммерческоеПредложениеКлиентуТовары](НомерСтроки)',@new nvarchar(max)=N'EXEC [1CUnloading].dbo.transferTalbeToDatWHErp ''Документ.бит_КоммерческоеПредложениеКлиенту'';
CREATE NONCLUSTERED INDEX IX_Ссылка ON DataWH.erp.[Документ.бит_КоммерческоеПредложениеКлиенту](Ссылка);
CREATE NONCLUSTERED INDEX IX_DocumentKey ON DataWH.erp.[Документ.бит_КоммерческоеПредложениеКлиенту](DocumentKey);
CREATE NONCLUSTERED INDEX IX_ДатНомерДата ON DataWH.erp.[Документ.бит_КоммерческоеПредложениеКлиенту](Номер);
EXEC [DataWH].[document_trial].[RefreshOfferLines];',
 @norm nvarchar(max),@expectedNorm nvarchar(max),@oid int;
EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51300,'Another offer operation is running.',1;
BEGIN TRY
 IF EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl WHERE Active=1) THROW 51300,'Already switched.',1;
 IF EXISTS(SELECT 1 FROM document_trial.OfferPublicationControl WHERE CutoverId IS NOT NULL AND RolledBackAtUTC IS NULL)
  THROW 51300,'Previous attempt needs Rollback-CommercialOfferJob.sql before retry.',1;
 SELECT @job=j.job_id,@enabled=j.enabled,@old=s.command
 FROM msdb.dbo.sysjobs j JOIN msdb.dbo.sysjobsteps s ON s.job_id=j.job_id
 WHERE j.name=N'ERP to DWH Документы' AND s.step_id=@step AND s.subsystem=N'TSQL'
 AND s.step_name=N'Документ.бит_КоммерческоеПредложениеКлиенту';
 IF @job IS NULL OR @old IS NULL THROW 51300,'Expected document job/step 41 missing.',1;
 SET @norm=@old; SET @expectedNorm=@expected;
 /* Ignore formatting, brackets and statement terminators, not executable tokens. */
 DECLARE @chars nvarchar(20)=N' '+NCHAR(9)+NCHAR(10)+NCHAR(13)+N'[];',@i int=1;
 WHILE @i<=LEN(@chars)
 BEGIN
  SET @norm=REPLACE(@norm,SUBSTRING(@chars,@i,1),N'');
  SET @expectedNorm=REPLACE(@expectedNorm,SUBSTRING(@chars,@i,1),N'');
  SET @i=@i+1;
 END;
 IF @norm COLLATE Latin1_General_100_CI_AS<>@expectedNorm COLLATE Latin1_General_100_CI_AS
  THROW 51300,'Step command differs from the reviewed original; nothing switched.',1;
 IF EXISTS(SELECT 1 FROM msdb.dbo.sysjobactivity WHERE job_id=@job
 AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
 AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
 THROW 51300,'Documents job is running; wait for completion.',1;
 SET @oid=OBJECT_ID(N'erp.[Документы.бит_КоммерческоеПредложениеКлиентуТовары]',N'U');
 IF @oid IS NULL THROW 51300,'Live goods table missing.',1;
 BEGIN TRANSACTION;
 INSERT document_trial.OfferJobStepBackup SELECT @cut,@enabled,SYSUTCDATETIME(),s.* FROM msdb.dbo.sysjobsteps s WHERE job_id=@job AND step_id=@step;
 UPDATE document_trial.OfferPublicationControl SET JobId=@job,StepId=@step,OriginalEnabled=@enabled,
 OriginalCommand=@old,NewCommand=@new,CutoverId=@cut,TargetObjectId=@oid,RolledBackAtUTC=NULL WHERE Id=1;
 IF @@ROWCOUNT<>1 THROW 51300,'Missing publication control.',1;
 COMMIT TRANSACTION;
 SET @saved=1;
 EXEC @rc=msdb.dbo.sp_update_job @job_id=@job,@enabled=0;
 IF @rc<>0 THROW 51300,'Could not disable job.',1;
 IF EXISTS(SELECT 1 FROM msdb.dbo.sysjobactivity WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
 AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
 THROW 51300,'Job started concurrently; cutover refused.',1;
 /* This explicit FAST variant reuses the previous MATCH without a new comparison.
    A successful publication before a later command-update failure is safe:
    the original full-load command remains compatible with published data. */
 EXEC document_trial.RefreshOfferLines @FullCheck=0,@Cutover=1,@SkipCutoverFullCheck=1;
 BEGIN TRANSACTION;
 IF NOT EXISTS(SELECT 1 FROM msdb.dbo.sysjobsteps WITH(UPDLOCK,HOLDLOCK) WHERE job_id=@job AND step_id=@step AND command=@old)
 THROW 51300,'Step changed during cutover.',1;
 IF EXISTS(SELECT 1 FROM msdb.dbo.sysjobactivity WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
 AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
 THROW 51300,'Job was started manually during validation.',1;
 EXEC @rc=msdb.dbo.sp_update_jobstep @job_id=@job,@step_id=@step,@command=@new;
 IF @rc<>0 THROW 51300,'Could not update step.',1;
 EXEC @rc=msdb.dbo.sp_update_job @job_id=@job,@enabled=@enabled;
 IF @rc<>0 THROW 51300,'Could not restore job enabled state.',1;
 UPDATE document_trial.OfferPublicationControl SET Active=1,SwitchedAtUTC=SYSUTCDATETIME(),RolledBackAtUTC=NULL WHERE Id=1;
 COMMIT TRANSACTION;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
 SELECT 'SWITCHED' AS Result,@job AS JobId,@step AS StepId,@enabled AS JobEnabled,@cut AS BackupId,@new AS NewCommand,CAST(0 AS bit) AS FullSourceChecked;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 IF @saved=1 AND EXISTS(SELECT 1 FROM msdb.dbo.sysjobsteps WHERE job_id=@job AND step_id=@step AND command=@old)
  EXEC msdb.dbo.sp_update_job @job_id=@job,@enabled=@enabled;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
 THROW;
END CATCH;
