/* Small local DML fixture. Uses the REAL publication engine with Fixture=1.
   No production rows, Agent settings or persistent trial state are changed.
   Run in a dedicated SSMS window after Install-PricePublication.sql. */
USE [DataWH];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51100,'Use a new window without a transaction.',1;
IF OBJECT_ID(N'price_trial.PublishBatch',N'P') IS NULL
    THROW 51100,'Install publication procedures first.',1;
DROP TABLE IF EXISTS #TargetSource,#TargetPrices,#TargetIntervals,#PublishKeys,
    #PublishGroups,#PublishPrices,#PublishIntervals,#PublishTerminals;
SELECT TOP (0) * INTO #TargetSource FROM price_trial.Prices;
SELECT TOP (0) * INTO #TargetPrices FROM price_trial.Prices;
SELECT TOP (0) [Период],[РегистраторВид],[РегистраторСсылка],[НомерСтроки],
    [Активность],[НоменклатураСсылка],[Номенклатура],[Характеристика],
    [ВидЦеныСсылка],[ВидЦены],[Цена],[Упаковка],[ВалютаСсылка],[Валюта],
    [ОбластьДанныхОсновныеДанные],DateStart,DateEND
INTO #TargetIntervals FROM price_trial.Intervals;
SELECT TOP (0) _Fld2951,_RecorderTRef,_RecorderRRef,_LineNo
INTO #PublishKeys FROM price_trial.Raw;
SELECT TOP (0) [Номенклатура],[ВидЦены] INTO #PublishGroups FROM price_trial.Prices;
SELECT TOP (0) * INTO #PublishPrices FROM price_trial.Prices;
SELECT TOP (0) * INTO #PublishIntervals FROM #TargetIntervals;
SELECT TOP (0) [ОбластьДанныхОсновныеДанные],[РегистраторВид],[РегистраторСсылка],
    [НомерСтроки],DateEND INTO #PublishTerminals FROM price_trial.Intervals;
BEGIN TRY
    INSERT #TargetSource VALUES
        ('20260101',0x01,0x01,1,0x01,0x01,N'A',0x00,0x01,N'Retail',100,0x00,0x01,N'USD',1),
        ('20260201',0x01,0x02,1,0x01,0x01,N'A',0x00,0x01,N'Retail',110,0x00,0x01,N'USD',1),
        ('20260101',0x01,0x03,1,0x01,0x02,NULL,0x00,0x01,NULL,200,0x00,0x01,N'USD',1);
    INSERT #TargetPrices SELECT * FROM #TargetSource;
    INSERT #TargetIntervals SELECT *,[Период],CASE WHEN [РегистраторСсылка]=0x01000000000000000000000000000000
        THEN CAST('20260131' AS datetime) ELSE CAST('20361114' AS datetime) END FROM #TargetSource;
    INSERT #PublishKeys VALUES (1,0x01,0x01,1),(1,0x01,0x02,1),(1,0x01,0x03,1),(1,0x01,0x04,1);
    INSERT #PublishGroups VALUES (N'A',N'Retail'),(NULL,NULL),(N'B',N'Retail');
    INSERT #PublishPrices VALUES
        ('20260101',0x01,0x01,1,0x01,0x01,N'A',0x00,0x01,N'Retail',101,0x00,0x01,N'USD',1),
        ('20260101',0x01,0x03,1,0x01,0x02,N'B',0x00,0x01,N'Retail',200,0x00,0x01,N'USD',1),
        ('20260301',0x01,0x04,1,0x01,0x01,N'A',0x00,0x01,N'Retail',120,0x00,0x01,N'USD',1);
    INSERT #PublishIntervals SELECT *,[Период],CASE WHEN [РегистраторСсылка]=0x01000000000000000000000000000000
        THEN CAST('20260228' AS datetime) ELSE CAST('20361115' AS datetime) END FROM #PublishPrices;
    INSERT #PublishTerminals SELECT [ОбластьДанныхОсновныеДанные],[РегистраторВид],
        [РегистраторСсылка],[НомерСтроки],DateEND FROM #PublishIntervals WHERE DateEND='20361115';
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC price_trial.PublishBatch @Fixture=1,@InjectFixtureFailure=1;
        COMMIT TRANSACTION;
        THROW 51102,'Expected failure was not raised.',1;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
        IF ERROR_NUMBER()<>51101 THROW;
    END CATCH;
    IF (SELECT COUNT(*) FROM #TargetSource)<>3
       OR NOT EXISTS (SELECT 1 FROM #TargetSource WHERE [Цена]=100)
       OR NOT EXISTS (SELECT 1 FROM #TargetSource WHERE [Период]='20260201')
       OR EXISTS (SELECT * FROM #TargetSource EXCEPT SELECT * FROM #TargetPrices)
       OR EXISTS (SELECT * FROM #TargetPrices EXCEPT SELECT * FROM #TargetSource)
        THROW 51100,'Publication failure did not roll back target changes.',1;
    IF (SELECT COUNT(*) FROM #PublishKeys)<>4
        THROW 51100,'Failed publication lost pending keys.',1;
    BEGIN TRANSACTION;
    EXEC price_trial.PublishBatch @Fixture=1;
    COMMIT TRANSACTION;
    IF (SELECT COUNT(*) FROM #TargetSource)<>3 OR (SELECT COUNT(*) FROM #TargetPrices)<>3
       OR (SELECT COUNT(*) FROM #TargetIntervals)<>3
       OR EXISTS (SELECT * FROM #TargetSource EXCEPT SELECT * FROM #PublishPrices)
       OR EXISTS (SELECT * FROM #PublishPrices EXCEPT SELECT * FROM #TargetSource)
       OR EXISTS (SELECT * FROM #TargetPrices EXCEPT SELECT * FROM #PublishPrices)
       OR EXISTS (SELECT * FROM #PublishPrices EXCEPT SELECT * FROM #TargetPrices)
       OR EXISTS (SELECT * FROM #TargetIntervals EXCEPT SELECT * FROM #PublishIntervals)
       OR EXISTS (SELECT * FROM #PublishIntervals EXCEPT SELECT * FROM #TargetIntervals)
        THROW 51100,'Publication result differs from fixture.',1;
    DELETE FROM #PublishKeys;
    DELETE FROM #PublishGroups;
    UPDATE #PublishTerminals SET DateEND='20361116';
    BEGIN TRANSACTION;
    EXEC price_trial.PublishBatch @Fixture=1;
    COMMIT TRANSACTION;
    IF (SELECT COUNT(*) FROM #TargetIntervals WHERE DateEND='20361116')<>2
       OR NOT EXISTS (SELECT 1 FROM #TargetIntervals WHERE [Период]='20260101' AND [Цена]=101 AND DateEND='20260228')
        THROW 51100,'No-change publication damaged intervals or failed to advance horizon.',1;
    DROP TABLE #TargetSource,#TargetPrices,#TargetIntervals,#PublishKeys,
        #PublishGroups,#PublishPrices,#PublishIntervals,#PublishTerminals;
    PRINT 'PASS: publication correction, deletion, rename, NULL group, neighbours, atomic failure/retry, no-change horizon.';
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    DROP TABLE IF EXISTS #TargetSource,#TargetPrices,#TargetIntervals,#PublishKeys,
        #PublishGroups,#PublishPrices,#PublishIntervals,#PublishTerminals;
    THROW;
END CATCH;
