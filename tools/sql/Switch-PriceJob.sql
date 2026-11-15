/* ACTUAL CUTOVER. Read docs/PRICE_CUTOVER.md first. Run on vm-dwh outside ETL.
   Full three-table validation + three large key-index builds may take hours.
   Never runs/stops a job. Preserves schedules, branches and other job steps.
   On ordinary failure restores enabled state and leaves old command/data intact.
   A session/host crash after disabling the job requires the documented recovery.
*/
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51100,'Use a new query window without a transaction.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
    THROW 51100,'This cutover is only for vm-dwh.',1;
IF OBJECT_ID(N'price_trial.PublicationControl',N'U') IS NULL
    THROW 51100,'Install publication procedures first.',1;
DECLARE @job uniqueidentifier='DBDF6C37-D349-4123-B4AC-29302CD125AD',
    @step int=1,@old nvarchar(max),@enabled bit,@lock int,@asof datetime,
    @new nvarchar(max)=N'EXEC [DataWH].[price_trial].[RefreshAndPublish];',
    @cut uniqueidentifier=NEWID(),@saved bit=0,@timeout int=@@LOCK_TIMEOUT,@rc int;
EXEC @lock=sys.sp_getapplock @Resource=N'price_trial.Refresh',
    @LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
IF @lock<0 THROW 51100,'Another trial/publication is running.',1;
BEGIN TRY
    IF NOT EXISTS (SELECT 1 FROM price_trial.PublicationControl WHERE Id=1)
        THROW 51100,'Publication control row is missing.',1;
    IF EXISTS (SELECT 1 FROM price_trial.PublicationControl WHERE Active=1)
        THROW 51100,'Already switched; do not repeat cutover.',1;
    IF OBJECT_DEFINITION(OBJECT_ID(N'price_trial.ApplyStage')) NOT LIKE N'%price_trial.PendingKeys%'
       OR OBJECT_DEFINITION(OBJECT_ID(N'price_trial.ApplyStage')) IS NULL
        THROW 51100,'The delta queue hook is missing.',1;
    IF NOT EXISTS (SELECT 1 FROM price_trial.Prices)
        THROW 51100,'Trial is empty; cutover refused.',1;
    SELECT @enabled=j.enabled,@old=s.command
    FROM msdb.dbo.sysjobs AS j JOIN msdb.dbo.sysjobsteps AS s ON s.job_id=j.job_id
    WHERE j.job_id=@job AND j.name=N'ERP to DWH Регистры накопления part 2'
      AND s.step_id=@step AND s.subsystem=N'TSQL';
    IF @old IS NULL OR CHARINDEX(N'[sp_РегистрСведений.ЦеныНоменклатуры]',@old)=0
        THROW 51100,'Expected original price step not found. Nothing switched.',1;
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobactivity
        WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
          AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
        THROW 51100,'The job is running; wait for it to finish.',1;

    /* Durable backup BEFORE changing enabled state. Never overwrite earlier
       backup rows; a crash can be recovered even while cutover is in progress. */
    BEGIN TRANSACTION;
    INSERT price_trial.JobStepBackup SELECT @cut,@enabled,SYSDATETIME(),s.*
        FROM msdb.dbo.sysjobsteps AS s WHERE s.job_id=@job AND s.step_id=@step;
    UPDATE price_trial.PublicationControl SET JobId=@job,StepId=@step,
        OriginalCommand=@old,NewCommand=@new,OriginalEnabled=@enabled,CurrentCutoverId=@cut
    WHERE Id=1;
    COMMIT TRANSACTION;
    SET @saved=1;
    EXEC @rc=msdb.dbo.sp_update_job @job_id=@job,@enabled=0;
    IF @rc<>0 THROW 51100,'Could not disable the job.',1;
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobactivity
        WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
          AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
        THROW 51100,'The job started concurrently. No step was changed.',1;
    SET LOCK_TIMEOUT 15000;
    RAISERROR('Price cutover: job disabled; checking schemas and publication baseline.',10,1) WITH NOWAIT;
    EXEC price_trial.CheckPublicationTargets @CreateIndexes=0;
    BEGIN TRANSACTION;
    EXEC price_trial.VerifyForPublication @VerifiedAsOf=@asof OUTPUT;
    RAISERROR('Price cutover: baseline matched; building publication key indexes.',10,1) WITH NOWAIT;
    EXEC price_trial.CheckPublicationTargets @CreateIndexes=1;
    IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysjobsteps WHERE job_id=@job AND step_id=@step AND command=@old)
        THROW 51100,'Job step changed during validation. Cutover refused.',1;
    IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobactivity
        WHERE job_id=@job AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
          AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
        THROW 51100,'Job was started manually during validation. Cutover refused.',1;
    DELETE FROM price_trial.PendingKeys;
    DELETE FROM price_trial.PendingGroups;
    UPDATE price_trial.PublicationControl SET Active=1,LastPublishedAsOf=@asof,
        SourceObjectId=OBJECT_ID(N'[1CUnloading].[1c].[РегистрСведений.ЦеныНоменклатуры]',N'U'),
        PricesObjectId=OBJECT_ID(N'erp.[РегистрСведений.ЦеныНоменклатуры]',N'U'),
        IntervalsObjectId=OBJECT_ID(N'erp.PriceERP',N'U'),SwitchedAt=SYSDATETIME(),RolledBackAt=NULL
    WHERE Id=1;
    EXEC @rc=msdb.dbo.sp_update_jobstep @job_id=@job,@step_id=@step,@command=@new;
    IF @rc<>0 THROW 51100,'Could not update the price step.',1;
    EXEC @rc=msdb.dbo.sp_update_job @job_id=@job,@enabled=@enabled;
    IF @rc<>0 THROW 51100,'Could not restore enabled state.',1;
    COMMIT TRANSACTION;
    SET LOCK_TIMEOUT @timeout;
    EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh',@LockOwner='Session';
    SELECT N'SWITCHED' AS Result,@job AS JobId,@step AS StepId,@enabled AS JobEnabled,
        @asof AS BaselineAsOf,@cut AS BackupId,@new AS NewCommand;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    SET LOCK_TIMEOUT @timeout;
    IF @saved=1 AND EXISTS (SELECT 1 FROM msdb.dbo.sysjobsteps
        WHERE job_id=@job AND step_id=@step AND command=@old)
        EXEC msdb.dbo.sp_update_job @job_id=@job,@enabled=@enabled;
    EXEC sys.sp_releaseapplock @Resource=N'price_trial.Refresh',@LockOwner='Session';
    THROW;
END CATCH;
