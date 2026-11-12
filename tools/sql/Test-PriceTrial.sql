/* Run after installation, BEFORE loading real data. Local synthetic data only.
   Refuses an existing trial state. Leaves installation/procedures intact.
   No ERP/linked-server access. No production table changes. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT <> 0 THROW 51000, 'Run test outside an existing transaction.', 1;
DECLARE @lock int;
EXEC @lock = sys.sp_getapplock @Resource = N'price_trial.Refresh',
    @LockMode = 'Exclusive', @LockOwner = 'Session', @LockTimeout = 0;
IF @lock < 0 THROW 51000, 'Trial is busy.', 1;
IF EXISTS (SELECT 1 FROM sys.tables
    WHERE schema_id = SCHEMA_ID(N'price_trial')
      AND name IN (N'Raw', N'Prices', N'Intervals', N'Nomen', N'Currencies', N'Kinds', N'State'))
BEGIN
    EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
    THROW 51000, 'Existing trial data found. Fixture test refused.', 1;
END;
BEGIN TRY
    CREATE TABLE #Raw (
        _Period datetime2(0), _RecorderTRef binary(4), _RecorderRRef binary(16),
        _LineNo numeric(9,0), _Active binary(1), _Fld48936RRef binary(16),
        _Fld48937RRef binary(16), _Fld48938RRef binary(16), _Fld48939 numeric(15,2),
        _Fld48940RRef binary(16), _Fld48941RRef binary(16), _Fld2951 numeric(9,0)
    );
    CREATE TABLE #Nomen ([Ссылка] binary(16), [Наименование] nvarchar(100));
    CREATE TABLE #Currencies ([Ссылка] binary(16), [Наименование] nvarchar(100));
    CREATE TABLE #Kinds ([Ссылка] binary(16), [Наименование] nvarchar(100));
    INSERT #Nomen VALUES (0x01, N'Goods A'), (0x02, N'Goods B');
    INSERT #Currencies VALUES (0x01, N'USD');
    INSERT #Kinds VALUES (0x01, N'Retail');
    INSERT #Raw VALUES
        ('20260101', 0x01, 0x01, 1, 0x01, 0x01, 0x00, 0x01, 100, 0x00, 0x01, 1),
        ('20260201', 0x01, 0x02, 1, 0x01, 0x01, 0x00, 0x01, 110, 0x00, 0x01, 1),
        ('20260101', 0x01, 0x03, 1, 0x01, 0x02, 0x00, 0x01, 200, 0x00, 0x01, 1);
    EXEC price_trial.ApplyStage @AsOf = '20261112';
    IF (SELECT COUNT(*) FROM price_trial.Prices) <> 3
        THROW 51000, 'Initial load count failed.', 1;
    IF NOT EXISTS (SELECT 1 FROM price_trial.Intervals
        WHERE [Номенклатура] = N'Goods A' AND [Период] = '20260101' AND DateEND = '20260131')
        THROW 51000, 'Initial interval failed.', 1;

    /* Correction of a past price, deletion of its successor, a new price,
       and a presentation rename affecting otherwise unchanged records. */
    UPDATE #Raw SET _Fld48939 = 101 WHERE _RecorderRRef = 0x01000000000000000000000000000000;
    DELETE #Raw WHERE _RecorderRRef = 0x02000000000000000000000000000000;
    INSERT #Raw VALUES
        ('20260301', 0x01, 0x04, 1, 0x01, 0x01, 0x00, 0x01, 120, 0x00, 0x01, 1);
    UPDATE #Nomen SET [Наименование] = N'Goods B renamed' WHERE [Ссылка] = 0x02000000000000000000000000000000;
    EXEC price_trial.ApplyStage @AsOf = '20261113';
    IF EXISTS (SELECT 1 FROM price_trial.Prices WHERE [Период] = '20260201')
        THROW 51000, 'Delete was not propagated.', 1;
    IF NOT EXISTS (SELECT 1 FROM price_trial.Intervals
        WHERE [Номенклатура] = N'Goods A' AND [Период] = '20260101'
          AND [Цена] = 101 AND DateEND = '20260228')
        THROW 51000, 'Correction or neighbour interval failed.', 1;
    IF EXISTS (SELECT 1 FROM price_trial.Intervals WHERE [Номенклатура] = N'Goods B')
       OR NOT EXISTS (SELECT 1 FROM price_trial.Intervals WHERE [Номенклатура] = N'Goods B renamed')
        THROW 51000, 'Dictionary rename failed.', 1;

    /* Changing a grouping reference must rebuild BOTH old and new groups. */
    UPDATE #Raw SET _Fld48936RRef = 0x02 WHERE _RecorderRRef = 0x04000000000000000000000000000000;
    EXEC price_trial.ApplyStage @AsOf = '20261114';
    IF NOT EXISTS (SELECT 1 FROM price_trial.Intervals
        WHERE [Номенклатура] = N'Goods A' AND [Период] = '20260101'
          AND DateEND = '20361114' AND __etl_terminal = 1)
        THROW 51000, 'Old group was not rebuilt after reference change.', 1;

    /* No raw changes: moving horizon must still advance. */
    EXEC price_trial.ApplyStage @AsOf = '20261115';
    IF EXISTS (SELECT 1 FROM price_trial.Intervals WHERE __etl_terminal = 1 AND DateEND <> '20361115')
        THROW 51000, 'Moving horizon failed.', 1;

    /* Invalid staging must not erase previously committed state. */
    INSERT #Kinds SELECT * FROM #Kinds;
    BEGIN TRY
        EXEC price_trial.ApplyStage @AsOf = '20261116';
        THROW 51001, 'Duplicate dictionary was accepted.', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51000 THROW;
    END CATCH;
    IF (SELECT AsOf FROM price_trial.State) <> '20261115'
        THROW 51000, 'Failed refresh changed committed state.', 1;

    DROP TABLE price_trial.Intervals, price_trial.Prices, price_trial.Raw,
        price_trial.Nomen, price_trial.Currencies, price_trial.Kinds, price_trial.State;
    EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
    PRINT 'PASS: initial load, correction, deletion, insert, rename, regroup, horizon, failure preservation.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    DROP TABLE IF EXISTS price_trial.Intervals, price_trial.Prices, price_trial.Raw,
        price_trial.Nomen, price_trial.Currencies, price_trial.Kinds, price_trial.State;
    EXEC sys.sp_releaseapplock @Resource = N'price_trial.Refresh', @LockOwner = 'Session';
    THROW;
END CATCH;
