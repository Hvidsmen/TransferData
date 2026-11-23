/* Complete trial header view with an inline DocumentKey expression.
   Run on vm-dwh AFTER the current baseline benchmark has finished.
   Creates/updates ONLY the marked *_InlineTrial view. No Agent step, existing
   view/function or production data is changed. No 730K-row copy is performed.
   Collation is obtained from the ORIGINAL DocumentKey; both the input number
   and result retain nvarchar(50). Eight local function checks run first.
*/
USE [1CUnloading];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF @@TRANCOUNT<>0 THROW 51250,'Use a new query window without a transaction.',1;
IF LOWER(CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')))<>N'vm-dwh'
    THROW 51250,'This trial view is only for vm-dwh.',1;
DECLARE @original int=OBJECT_ID(N'[1c].[Документ.бит_КоммерческоеПредложениеКлиенту]',N'V'),
    @candidate int=OBJECT_ID(N'[1c].[Документ.бит_КоммерческоеПредложениеКлиенту_InlineTrial]'),
    @collation sysname,@ddl nvarchar(max),@check nvarchar(max);
SELECT @collation=collation_name FROM sys.columns
WHERE object_id=@original AND name=N'DocumentKey' AND system_type_id=231 AND max_length=100;
IF @original IS NULL OR @collation IS NULL
    THROW 51250,'Original view or its nvarchar(50) DocumentKey metadata is unavailable.',1;
IF NOT EXISTS (SELECT 1 FROM sys.fn_helpcollations() WHERE name=@collation)
    THROW 51250,'Unknown DocumentKey collation.',1;
IF @candidate IS NOT NULL
BEGIN
    IF OBJECT_ID(N'[1c].[Документ.бит_КоммерческоеПредложениеКлиенту_InlineTrial]',N'V') IS NULL
        THROW 51250,'Candidate name is already occupied by a different object type.',1;
    IF NOT EXISTS (SELECT 1 FROM sys.extended_properties
        WHERE [class]=1 AND major_id=@candidate AND minor_id=0
          AND [name]=N'TransferData.InlineKeyTrial' AND CONVERT(nvarchar(100),[value])=N'1')
        THROW 51250,'Candidate name is already occupied by an unmarked view.',1;
END;

-- Local artificial inputs only; no rows of the document views are selected.
SET @check = N'DECLARE @cases TABLE (InputDate datetime2(7), InputNumber nvarchar(200));
INSERT @cases VALUES
    (NULL,NULL), (NULL,N'' AB 12 ''), (''20261117'',NULL), (''20261117'',N''''),
    (''2026-11-17T12:34:56.1234567'',N''  АБ 12 3  ''),
    (''20261117'',REPLICATE(N''Я'',80)),
    (''20261117'',REPLICATE(N'' '',50)+N''Z''),
    (''20261117'',N''A''+NCHAR(160)+NCHAR(9)+N'' B'');
IF EXISTS (
    SELECT 1 FROM @cases AS v
    CROSS APPLY (SELECT [DataWH].[dbo].[makeDocumentKeyDate](v.InputDate,v.InputNumber) AS ExpectedKey,
        CONVERT(nvarchar(50), CONCAT(
    CONVERT(date, v.InputDate),
    REPLACE(LTRIM(RTRIM(CONVERT(nvarchar(50), v.InputNumber) COLLATE __ETL_DOCUMENT_KEY_COLLATION__)), N'' '', N'''')
)) COLLATE __ETL_DOCUMENT_KEY_COLLATION__ AS ActualKey) AS c
    WHERE CONVERT(varbinary(max),c.ExpectedKey)<>CONVERT(varbinary(max),c.ActualKey)
       OR DATALENGTH(c.ExpectedKey)<>DATALENGTH(c.ActualKey)
       OR (c.ExpectedKey IS NULL AND c.ActualKey IS NOT NULL)
       OR (c.ExpectedKey IS NOT NULL AND c.ActualKey IS NULL)
) THROW 51250,''Inline DocumentKey differs from the existing function. No view created.'',1;
PRINT ''PASS: date, NULL, Unicode, whitespace and both nvarchar(50) truncations.'';';
SET @check=REPLACE(@check,N'__ETL_DOCUMENT_KEY_COLLATION__',@collation);
EXEC sys.sp_executesql @check;

-- Full supplied projection, CTE and all 27 joins; only DocumentKey is replaced.
SET @ddl = N'CREATE OR ALTER VIEW [1c].[Документ.бит_КоммерческоеПредложениеКлиенту_InlineTrial]
AS


WITH ДокументОснование AS
(
                    SELECT
                        Ссылка
                        ,Наименование
                        ,''СделкиСКлиентами'' AS Тип
                    FROM
                        [erp-db-02.erp.lcl].exch_biview2.dbo.[Справочники.СделкиСКлиентами]


                    UNION ALL
                    SELECT
                        Ссылка
                        ,Номер
                        ,''ЗапросКоммерческогоПредложенияОтКлиента'' AS Тип
                    FROM
                        [erp-db-02.erp.lcl].exch_biview2.dbo.[Документы.ЗапросКоммерческогоПредложенияОтКлиента]
                    )
SELECT
t.Ссылка
,t.ВерсияДанных
,t.ПометкаУдаления
,t.Дата
,t.Номер
,t.Проведен
,CONVERT(nvarchar(50), CONCAT(
    CONVERT(date, t.Дата),
    REPLACE(LTRIM(RTRIM(CONVERT(nvarchar(50), t.Номер) COLLATE __ETL_DOCUMENT_KEY_COLLATION__)), N'' '', N'''')
)) COLLATE __ETL_DOCUMENT_KEY_COLLATION__ AS [DocumentKey]
,COALESCE(Организация.Наименование, '''') AS Организация
,COALESCE(Клиент.Наименование, '''') AS Клиент
,COALESCE(Контрагент.Наименование, '''') AS Контрагент
,COALESCE(КонтактноеЛицо.Наименование, '''') AS КонтактноеЛицо
,COALESCE(Валюта.Наименование, '''') AS Валюта
,COALESCE(КартаЛояльности.Наименование, '''') AS КартаЛояльности
,COALESCE(ХозяйственнаяОперация.ВидыОперацийКоммерческихПредложений, '''') AS ХозяйственнаяОперация
,t.Налогообложение
,t.ЦенаВключаетНДС
,t.СрокДействия
,COALESCE(Статус.СтатусыКоммерческихПредложенийКлиентам, '''') AS Статус
,t.УсловияДоставкиТекст
,t.УсловияОплатыТекст
,COALESCE(Сделка.Наименование, '''') AS Сделка
,t.СкидкиРассчитаны
,t.ПрочаяДополнительнаяИнформацияТекст
,t.МожетВыкупатьсяЧастично
,COALESCE(Менеджер.Наименование, '''') AS Менеджер
,t.УдалитьВернутьМногооборотнуюТару
,COALESCE(ВариантУказанияСрокаПоставки.ВариантыУказанияСрокаПоставки, '''') AS ВариантУказанияСрокаПоставки
,COALESCE(УдалитьСклад.Наименование, '''') AS УдалитьСклад
,COALESCE(УдалитьСоглашение.Наименование, '''') AS УдалитьСоглашение
,COALESCE(УдалитьСостояниеЗаполненияМногооборотнойТары.СостоянияЗаполненияМногооборотнойТары, '''') AS УдалитьСостояниеЗаполненияМногооборотнойТары
,COALESCE(УдалитьСпособДоставки.СпособыДоставки, '''') AS УдалитьСпособДоставки
,t.УдалитьСрокВозвратаМногооборотнойТары
,t.СуммаДокумента
,t.УдалитьТребуетсяЗалогЗаТару
,t.ДополнительнаяИнформацияКлиентуHTML
,t.Комментарий
,t.ДатаЭД
,t.НомерЭД
,COALESCE(УдалитьАвтор.Наименование, '''') AS УдалитьАвтор
,COALESCE(УдалитьГрафикОплаты.Наименование, '''') AS УдалитьГрафикОплаты
,t.УдалитьДополнительнаяИнформация
,COALESCE(УдалитьНалогообложениеНДС.ТипыНалогообложенияНДС, '''') AS УдалитьНалогообложениеНДС
,COALESCE(УдалитьПартнер.Наименование, '''') AS УдалитьПартнер
,t.УдалитьСогласован
,t.УдалитьСрокПоставки
,COALESCE(УдалитьФормаОплаты.ФормыОплаты, '''') AS УдалитьФормаОплаты
,t.ХэшСуммаЗапроса
,t.ДокументОснованиеTип
,t.ДокументОснованиеВид
,COALESCE(ДокументОснование.Наименование, '''') AS ДокументОснованиеНомер
 ,COALESCE(ДокументОснование.Тип,'''') AS ДокументОснованиеТип,t.ИдентификаторЗКПВСервисе
,COALESCE(УдалитьДокументОснование.Номер, '''') AS УдалитьДокументОснование
,COALESCE(УдалитьСтатус.СтатусыКоммерческихПредложенийКлиентам, '''') AS УдалитьСтатус
,COALESCE(УдалитьХозяйственнаяОперация.ХозяйственныеОперации, '''') AS УдалитьХозяйственнаяОперация
,t.бит_ОснованиеTип
,t.бит_ОснованиеВид
,COALESCE(бит_Основание.Номер, '''') AS бит_Основание
,t.ФлагВнесенияВОчередьВыгрузки
,t.КомментарийВнутренний
,t.ССВДатаСрезаЦены
,COALESCE(ТехническийСпециалист.Наименование, '''') AS ТехническийСпециалист
,t.БИТ_ПредставлениеНомера
,t.МаксимальныйКодСтроки
,COALESCE(СкладДляЗапросаСроков.Наименование, '''') AS СкладДляЗапросаСроков
,t.ОбластьДанныхОсновныеДанные

,t.Организация AS ОрганизацияСсылка
,t.Клиент AS КлиентСсылка
,t.Контрагент AS КонтрагентСсылка
,t.КонтактноеЛицо AS КонтактноеЛицоСсылка
,t.Валюта AS ВалютаСсылка
,t.КартаЛояльности AS КартаЛояльностиСсылка
,t.ХозяйственнаяОперация AS ХозяйственнаяОперацияСсылка
,t.Статус AS СтатусСсылка
,t.Сделка AS СделкаСсылка
,t.Менеджер AS МенеджерСсылка
,t.ВариантУказанияСрокаПоставки AS ВариантУказанияСрокаПоставкиСсылка
,t.УдалитьСклад AS УдалитьСкладСсылка
,t.УдалитьСоглашение AS УдалитьСоглашениеСсылка
,t.УдалитьСостояниеЗаполненияМногооборотнойТары AS УдалитьСостояниеЗаполненияМногооборотнойТарыСсылка
,t.УдалитьСпособДоставки AS УдалитьСпособДоставкиСсылка
,t.УдалитьАвтор AS УдалитьАвторСсылка
,t.УдалитьГрафикОплаты AS УдалитьГрафикОплатыСсылка
,t.УдалитьНалогообложениеНДС AS УдалитьНалогообложениеНДССсылка
,t.УдалитьПартнер AS УдалитьПартнерСсылка
,t.УдалитьФормаОплаты AS УдалитьФормаОплатыСсылка
,t.ДокументОснование AS ДокументОснование_Многосостав
,t.УдалитьДокументОснование AS УдалитьДокументОснованиеСсылка
,t.УдалитьСтатус AS УдалитьСтатусСсылка
,t.УдалитьХозяйственнаяОперация AS УдалитьХозяйственнаяОперацияСсылка
,t.бит_Основание AS бит_ОснованиеСсылка
,t.ТехническийСпециалист AS ТехническийСпециалистСсылка
,t.СкладДляЗапросаСроков AS СкладДляЗапросаСроковСсылка

 FROM [erp-db-02.erp.lcl].exch_biview2.dbo.[Документы.бит_КоммерческоеПредложениеКлиенту] t
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Организации] Организация ON t.Организация = Организация.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Партнеры] Клиент ON t.Клиент = Клиент.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Контрагенты] Контрагент ON t.Контрагент = Контрагент.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.КонтактныеЛицаПартнеров] КонтактноеЛицо ON t.КонтактноеЛицо = КонтактноеЛицо.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Валюты] Валюта ON t.Валюта = Валюта.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.КартыЛояльности] КартаЛояльности ON t.КартаЛояльности = КартаЛояльности.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.ВидыОперацийКоммерческихПредложений] ХозяйственнаяОперация ON t.ХозяйственнаяОперация = ХозяйственнаяОперация.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.СтатусыКоммерческихПредложенийКлиентам] Статус ON t.Статус = Статус.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.СделкиСКлиентами] Сделка ON t.Сделка = Сделка.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Пользователи] Менеджер ON t.Менеджер = Менеджер.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.ВариантыУказанияСрокаПоставки] ВариантУказанияСрокаПоставки ON t.ВариантУказанияСрокаПоставки = ВариантУказанияСрокаПоставки.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Склады] УдалитьСклад ON t.УдалитьСклад = УдалитьСклад.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.СоглашенияСКлиентами] УдалитьСоглашение ON t.УдалитьСоглашение = УдалитьСоглашение.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.СостоянияЗаполненияМногооборотнойТары] УдалитьСостояниеЗаполненияМногооборотнойТары ON t.УдалитьСостояниеЗаполненияМногооборотнойТары = УдалитьСостояниеЗаполненияМногооборотнойТары.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.СпособыДоставки] УдалитьСпособДоставки ON t.УдалитьСпособДоставки = УдалитьСпособДоставки.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Пользователи] УдалитьАвтор ON t.УдалитьАвтор = УдалитьАвтор.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.ГрафикиОплаты] УдалитьГрафикОплаты ON t.УдалитьГрафикОплаты = УдалитьГрафикОплаты.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.ТипыНалогообложенияНДС] УдалитьНалогообложениеНДС ON t.УдалитьНалогообложениеНДС = УдалитьНалогообложениеНДС.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Партнеры] УдалитьПартнер ON t.УдалитьПартнер = УдалитьПартнер.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.ФормыОплаты] УдалитьФормаОплаты ON t.УдалитьФормаОплаты = УдалитьФормаОплаты.Ссылка
LEFT JOIN ДокументОснование ON t.ДокументОснование = ДокументОснование.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Документы.бит_КоммерческоеПредложениеКлиенту] УдалитьДокументОснование ON t.УдалитьДокументОснование = УдалитьДокументОснование.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.СтатусыКоммерческихПредложенийКлиентам] УдалитьСтатус ON t.УдалитьСтатус = УдалитьСтатус.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Перечисления.ХозяйственныеОперации] УдалитьХозяйственнаяОперация ON t.УдалитьХозяйственнаяОперация = УдалитьХозяйственнаяОперация.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Документы.Даичи_ЗаявкаНаТКП] бит_Основание ON t.бит_Основание = бит_Основание.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Пользователи] ТехническийСпециалист ON t.ТехническийСпециалист = ТехническийСпециалист.Ссылка
LEFT JOIN [erp-db-02.erp.lcl].exch_biview2.[dbo].[Справочники.Склады] СкладДляЗапросаСроков ON t.СкладДляЗапросаСроков = СкладДляЗапросаСроков.Ссылка';
SET @ddl=REPLACE(@ddl,N'__ETL_DOCUMENT_KEY_COLLATION__',@collation);
BEGIN TRY
    BEGIN TRANSACTION;
    EXEC sys.sp_executesql @ddl;
    SET @candidate=OBJECT_ID(N'[1c].[Документ.бит_КоммерческоеПредложениеКлиенту_InlineTrial]',N'V');
    IF NOT EXISTS (SELECT 1 FROM sys.extended_properties
        WHERE [class]=1 AND major_id=@candidate AND minor_id=0 AND [name]=N'TransferData.InlineKeyTrial')
        EXEC sys.sp_addextendedproperty @name=N'TransferData.InlineKeyTrial',@value=N'1',
            @level0type=N'SCHEMA',@level0name=N'1c',@level1type=N'VIEW',
            @level1name=N'Документ.бит_КоммерческоеПредложениеКлиенту_InlineTrial';
    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=@candidate AND name=N'DocumentKey'
        AND system_type_id=231 AND max_length=100 AND collation_name=@collation)
        THROW 51250,'Candidate DocumentKey type/collation mismatch.',1;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
SELECT OBJECT_SCHEMA_NAME(object_id) AS SchemaName,OBJECT_NAME(object_id) AS ViewName,
    name AS ColumnName,TYPE_NAME(system_type_id) AS DataType,max_length AS MaxBytes,
    collation_name,is_nullable
FROM sys.columns WHERE object_id IN (@original,@candidate) AND name=N'DocumentKey';
PRINT 'Trial view ready. Existing view, function and Agent job unchanged.';
