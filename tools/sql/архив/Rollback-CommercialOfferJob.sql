/* Restores the saved command and enabled state. Never starts/stops the job.
   Keeps last committed data/cache; next original run does its usual full copy. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51300,'Use a new window without a transaction.',1;
IF @@LOCK_TIMEOUT<>-1 THROW 51300,'Use a fresh default SSMS window with LOCK_TIMEOUT -1.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh' THROW 51300,'Only vm-dwh.',1;
DECLARE @job uniqueidentifier,@step int,@old nvarchar(max),@new nvarchar(max),@enabled bit,@lock int,@rc int;
IF APPLOCK_MODE(N'public',N'document_trial.MeasureCommercialOffers',N'Session')<>N'NoLock'
 THROW 51300,'This SSMS session still owns an offer lock; close/reconnect it before retry.',1;
EXEC @lock=sys.sp_getapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51300,'Another offer operation is running.',1;
BEGIN TRY
 SET LOCK_TIMEOUT 30000;
 SELECT @job=JobId,@step=StepId,@old=OriginalCommand,@new=NewCommand,@enabled=OriginalEnabled
 FROM document_trial.OfferPublicationControl WHERE Id=1 AND CutoverId IS NOT NULL;
 IF @job IS NULL OR @old IS NULL OR @enabled IS NULL THROW 51300,'No saved cutover to restore.',1;
 BEGIN TRANSACTION;
 IF NOT EXISTS(SELECT 1 FROM msdb.dbo.sysjobs WHERE job_id=@job AND name=N'ERP to DWH Документы')
  THROW 51300,'Saved job identity changed.',1;
 IF NOT EXISTS(SELECT 1 FROM msdb.dbo.sysjobsteps WITH(UPDLOCK,HOLDLOCK) WHERE job_id=@job AND step_id=@step AND (command=@old OR command=@new))
  THROW 51300,'Step changed independently; review backup.',1;
 IF EXISTS(SELECT 1 FROM msdb.dbo.sysjobactivity WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
 AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
 THROW 51300,'Wait until the document job finishes.',1;
 EXEC @rc=msdb.dbo.sp_update_jobstep @job_id=@job,@step_id=@step,@command=@old;
 IF @rc<>0 THROW 51300,'Could not restore step.',1;
 EXEC @rc=msdb.dbo.sp_update_job @job_id=@job,@enabled=@enabled;
 IF @rc<>0 THROW 51300,'Could not restore job enabled state.',1;
 UPDATE document_trial.OfferPublicationControl SET Active=0,RolledBackAtUTC=SYSUTCDATETIME() WHERE Id=1;
 COMMIT TRANSACTION;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
 SET LOCK_TIMEOUT -1;
 SELECT 'ROLLED_BACK' AS Result,@job AS JobId,@step AS StepId,@enabled AS JobEnabled;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 EXEC sys.sp_releaseapplock @Resource=N'document_trial.MeasureCommercialOffers',@LockOwner='Session';
 SET LOCK_TIMEOUT -1;
 THROW;
END CATCH;
