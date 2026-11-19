/* Metadata-only preflight. Does not read 140M rows or create indexes. */
USE [DataWH];
GO
EXEC price_trial.CheckPublicationTargets @CreateIndexes=0;
SELECT Id,Active,JobId,StepId,LastPublishedAsOf,SwitchedAt,RolledBackAt
FROM price_trial.PublicationControl;
SELECT j.job_id,j.name,j.enabled,SUSER_SNAME(j.owner_sid) AS JobOwner,
    s.step_id,s.step_name,s.subsystem,s.database_name,s.database_user_name,
    s.on_success_action,s.on_fail_action
FROM msdb.dbo.sysjobs AS j JOIN msdb.dbo.sysjobsteps AS s ON s.job_id=j.job_id
WHERE j.job_id='DBDF6C37-D349-4123-B4AC-29302CD125AD' AND s.step_id=1;
SELECT DB_NAME(f.database_id) AS DatabaseName,f.name,f.type_desc,
    f.physical_name,f.size/128.0 AS FileMB,f.growth,f.is_percent_growth,
    v.volume_mount_point,v.available_bytes/1073741824.0 AS AvailableGB
FROM sys.master_files AS f CROSS APPLY sys.dm_os_volume_stats(f.database_id,f.file_id) AS v
WHERE f.database_id IN (DB_ID(N'DataWH'),DB_ID(N'1CUnloading'),DB_ID(N'tempdb'));
/* Review other callers; this is not proof that dynamic/external writers do not
   exist. They must remain idle during validation and while the new path owns it. */
SELECT j.name AS OtherJob,s.step_id,s.step_name
FROM msdb.dbo.sysjobs AS j JOIN msdb.dbo.sysjobsteps AS s ON s.job_id=j.job_id
WHERE j.job_id<>'DBDF6C37-D349-4123-B4AC-29302CD125AD'
  AND (s.command LIKE N'%ЦеныНоменклатуры%' OR s.command LIKE N'%PriceERP%');
