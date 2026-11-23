/* Read-only metadata/history for step 5. Never executes the source procedure. */
USE [1CUnloading];
GO
SET NOCOUNT ON;
SELECT @@SERVERNAME AS ServerName,DB_NAME() AS DatabaseName,SYSDATETIME() AS ServerTime;
SELECT N'1CUnloading' AS DatabaseName,s.name AS SchemaName,o.name AS ObjectName,o.type_desc,
 (SELECT SUM(rows) FROM sys.partitions WHERE object_id=o.object_id AND index_id IN(0,1)) AS ApproxRows,p.ReservedMB
FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
OUTER APPLY(SELECT SUM(CASE WHEN i.index_id IN(0,1) AND a.type=1 THEN i.rows ELSE 0 END) AS ApproxRows,
 SUM(a.total_pages)/128.0 AS ReservedMB
 FROM sys.partitions i JOIN sys.allocation_units a
 ON a.container_id=CASE WHEN a.type IN(1,3) THEN i.hobt_id ELSE i.partition_id END
 WHERE i.object_id=o.object_id) p
WHERE s.name=N'1c' AND o.name IN(N'Документы.РеализацияТоваровУслуг',N'Документы.РеализацияТоваровУслугТовары');
SELECT N'DataWH' AS DatabaseName,s.name AS SchemaName,o.name AS ObjectName,o.type_desc,
 (SELECT SUM(rows) FROM DataWH.sys.partitions WHERE object_id=o.object_id AND index_id IN(0,1)) AS ApproxRows,p.ReservedMB
FROM DataWH.sys.objects o JOIN DataWH.sys.schemas s ON s.schema_id=o.schema_id
OUTER APPLY(SELECT SUM(CASE WHEN i.index_id IN(0,1) AND a.type=1 THEN i.rows ELSE 0 END) AS ApproxRows,
 SUM(a.total_pages)/128.0 AS ReservedMB
 FROM DataWH.sys.partitions i JOIN DataWH.sys.allocation_units a
 ON a.container_id=CASE WHEN a.type IN(1,3) THEN i.hobt_id ELSE i.partition_id END
 WHERE i.object_id=o.object_id) p
WHERE s.name=N'erp' AND o.name IN(N'Документы.РеализацияТоваровУслуг',N'Документы.РеализацияТоваровУслугТовары');
SELECT o.name,o.type_desc,m.definition
FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id=o.object_id
WHERE s.name=N'1c' AND o.name=N'Документы.РеализацияТоваровУслугТовары';
SELECT j.name,s.step_id,s.step_name,s.command
FROM msdb.dbo.sysjobs j JOIN msdb.dbo.sysjobsteps s ON s.job_id=j.job_id
WHERE j.name=N'ERP to DWH Документы' AND s.step_id=5;
SELECT TOP(20) h.instance_id,h.run_date,h.run_time,h.run_status,
 (h.run_duration/10000*3600+h.run_duration/100%100*60+h.run_duration%100)/60.0 AS Minutes,
 h.sql_message_id,h.message
FROM msdb.dbo.sysjobs j JOIN msdb.dbo.sysjobhistory h ON h.job_id=j.job_id
WHERE j.name=N'ERP to DWH Документы' AND h.step_id=5 ORDER BY h.instance_id DESC;
/* If the trial is running, inspect it from a separate SSMS window. */
IF OBJECT_ID(N'DataWH.document_trial.SalesExtractRuns',N'U') IS NOT NULL
BEGIN
 DECLARE @session int;
 SELECT TOP(1) @session=SessionId FROM DataWH.document_trial.SalesExtractRuns ORDER BY StartedAtUTC DESC;
 SELECT TOP(1) * FROM DataWH.document_trial.SalesExtractRuns ORDER BY StartedAtUTC DESC;
 SELECT r.session_id,r.status,r.command,r.wait_type,r.blocking_session_id,
 r.total_elapsed_time/60000.0 AS Minutes,r.cpu_time/1000.0 AS CpuSeconds,r.reads,r.writes,
 SUBSTRING(t.text,r.statement_start_offset/2+1,
 (CASE WHEN r.statement_end_offset=-1 THEN DATALENGTH(t.text) ELSE r.statement_end_offset END-r.statement_start_offset)/2+1) AS CurrentStatement
 FROM sys.dm_exec_requests r OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
 WHERE r.session_id=@session;
END;
