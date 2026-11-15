/* Restore ONLY the saved original command and enabled state. No SQL job is run
   or stopped. No trial data, pending deltas, indexes or production rows deleted.
   Works after an interrupted switch that saved its backup but disabled the job.
*/
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51100,'Use a new query window without a transaction.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
    THROW 51100,'This rollback is only for vm-dwh.',1;
DECLARE @lock int,@job uniqueidentifier,@step int,@old nvarchar(max),@new nvarchar(max),@enabled bit,
    @disabled bit=0,@enabledBeforeAttempt bit,@rc int;
EXEC @lock=sys.sp_getapplock @Resource=N'price_trial.Refresh',
    @LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51100,'Refresh/publication is running; rollback refused.',1;
BEGIN TRY
    SELECT @job=JobId,@step=StepId,@old=OriginalCommand,@new=NewCommand,@enabled=OriginalEnabled
    FROM price_trial.PublicationControl WHERE Id=1;
    IF @old IS NULL OR @job IS NULL OR @step IS NULL OR @enabled IS NULL
       OR @job<>'DBDF6C37-D349-4123-B4AC-29302CD125AD' OR @step<>1
        THROW 51100,'No matching saved step. Nothing restored.',1;
    IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysjobs
        WHERE job_id=@job AND name=N'ERP to DWH Регистры накопления part 2')
        THROW 51100,'Expected job not found.',1;
    IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysjobsteps WHERE job_id=@job AND step_id=@step
        AND subsystem=N'TSQL' AND (command=@new OR command=@old))
        THROW 51100,'Step was edited outside cutover. Review manually.',1;
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobactivity
        WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
          AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
        THROW 51100,'Job is running; wait for completion.',1;
    SELECT @enabledBeforeAttempt=enabled FROM msdb.dbo.sysjobs WHERE job_id=@job;
    EXEC @rc=msdb.dbo.sp_update_job @job_id=@job,@enabled=0;
    IF @rc<>0 THROW 51100,'Could not disable the job for rollback.',1;
    SET @disabled=1;
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobactivity
        WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
          AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
        THROW 51100,'Job started concurrently; rollback refused.',1;
    BEGIN TRANSACTION;
    IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysjobsteps WHERE job_id=@job AND step_id=@step
        AND subsystem=N'TSQL' AND (command=@new OR command=@old))
        THROW 51100,'Step changed during rollback; review manually.',1;
    EXEC @rc=msdb.dbo.sp_update_jobstep @job_id=@job,@step_id=@step,@command=@old;
    IF @rc<>0 THROW 51100,'Could not restore the original command.',1;
    UPDATE price_trial.PublicationControl SET Active=0,RolledBackAt=SYSDATETIME() WHERE Id=1;
    EXEC @rc=msdb.dbo.sp_update_job @job_id=@job,@enabled=@enabled;
    IF @rc<>0 THROW 51100,'Could not restore enabled state.',1;
    COMMIT TRANSACTION;
    EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh',@LockOwner='Session';
    SELECT N'RESTORED' AS Result,@enabled AS JobEnabled;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    IF @disabled=1 AND @enabledBeforeAttempt IS NOT NULL AND EXISTS (SELECT 1 FROM msdb.dbo.sysjobsteps
        WHERE job_id=@job AND step_id=@step AND (command=@new OR command=@old))
        EXEC msdb.dbo.sp_update_job @job_id=@job,@enabled=@enabledBeforeAttempt;
    EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh',@LockOwner='Session';
    THROW;
END CATCH;
