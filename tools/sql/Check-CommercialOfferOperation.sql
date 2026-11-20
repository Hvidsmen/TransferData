/* Read-only progress. Run in a SEPARATE SSMS window during a switch.
   Never stops sessions or changes jobs/data. VIEW SERVER STATE is required.
   Journal reads use READUNCOMMITTED for progress only: active transaction
   phases may roll back. Never treat these reads as a committed checkpoint. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;
DECLARE @SessionId int=NULL; -- Set to the SessionId printed by the switch.
IF HAS_PERMS_BY_NAME(NULL,NULL,'VIEW SERVER STATE')<>1
 THROW 51300,'VIEW SERVER STATE is required for server-wide diagnosis.',1;
IF @SessionId IS NULL
 SELECT TOP(1) @SessionId=RunSession.SessionId
 FROM document_trial.OfferDeltaRuns AS RunSession WITH(READUNCOMMITTED)
 WHERE RunSession.Mode='production' ORDER BY RunSession.StartedAtUTC DESC;
SELECT @@SERVERNAME AS ServerName,@@SPID AS DiagnosticSession,@SessionId AS OperationSession;
IF @SessionId=@@SPID
 PRINT 'This is the operation session itself. Open a separate window to inspect a running statement.';
SELECT s.session_id,s.status AS SessionStatus,s.open_transaction_count,
 r.status AS RequestStatus,r.command,r.wait_type,r.wait_time/1000.0 AS WaitSeconds,
 r.blocking_session_id,r.total_elapsed_time/60000.0 AS Minutes,
 r.cpu_time/1000.0 AS CpuSeconds,r.reads,r.writes,r.logical_reads,
 r.percent_complete,r.estimated_completion_time/60000.0 AS EstimatedMinutes,
 SUBSTRING(t.text,r.statement_start_offset/2+1,
  (CASE WHEN r.statement_end_offset=-1 THEN DATALENGTH(t.text) ELSE r.statement_end_offset END-r.statement_start_offset)/2+1) AS CurrentStatement
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_requests r ON r.session_id=s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE s.session_id=@SessionId OR s.session_id IN
 (SELECT blocking_session_id FROM sys.dm_exec_requests WHERE session_id=@SessionId AND blocking_session_id>0);
SELECT request_session_id,request_mode,request_status,resource_description
FROM sys.dm_tran_locks
WHERE resource_type='APPLICATION' AND resource_database_id=DB_ID();
DECLARE @run uniqueidentifier;
SELECT TOP(1) @run=RunId FROM document_trial.OfferDeltaRuns WITH(READUNCOMMITTED)
WHERE SessionId=@SessionId AND Mode='production' ORDER BY StartedAtUTC DESC;
SELECT RunId,SessionId,Status,StartedAtUTC,CompletedAtUTC,ErrorNumber
FROM document_trial.OfferDeltaRuns WITH(READUNCOMMITTED) WHERE RunId=@run;
SELECT Phase,Status,RowsCopied,ErrorNumber,
 DATEDIFF_BIG(millisecond,StartedAtUTC,COALESCE(CompletedAtUTC,SYSUTCDATETIME()))/60000.0 AS Minutes,
 N'Uncommitted progress; may roll back' AS JournalVisibility
FROM document_trial.OfferDeltaPhases WITH(READUNCOMMITTED)
WHERE RunId=@run ORDER BY StartedAtUTC,StepNo;
SET LOCK_TIMEOUT -1;
