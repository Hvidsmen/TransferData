"""Allocate each payment across the supplier order's goods lines, without losing payments."""
from django.core.exceptions import ValidationError
from transferdata.erp.engine.validators import identifier
from .payment_schedule import REGISTER, concrete_references


def allocated_amount(amount):
    """Cumulative rounding telescopes: allocations sum to the payment rounded to 6 decimals."""
    value = f'CAST([p].{identifier(amount)} AS decimal(28,6))'
    return f'''CASE WHEN [i].[OrderTotal] IS NULL OR [i].[OrderTotal] = 0 THEN {value}
 ELSE ROUND({value} * [i].[ThroughShare], 6) - ROUND({value} * [i].[BeforeShare], 6) END'''


def detail_sql(m, order, turnover, payments, names):
    parts = [part for part in m.objects[order].get('table_parts', []) if part['name'] == 'Товары']
    if len(parts) != 1 or not parts[0].get('properties'):
        raise ValidationError('Нужна структура табличной части заказа поставщику «Товары». Обновите помощник и повторите подготовку.')
    part = '__order_goods'
    m.objects[part] = dict(parts[0])
    c = m.column
    refs = concrete_references(m.objects[part], 'Номенклатура')
    if len(refs) != 1 or not refs[0].startswith('Справочник.'):
        raise ValidationError('Номенклатура должна ссылаться на один справочник.')
    target = m.targets.get(refs[0], {})
    if not all(target.get(key) for key in ('table', 'id', 'description')):
        raise ValidationError('Не найдена структура справочника номенклатуры.')
    product = '__product'
    m.objects[product] = dict(target)
    # Reject union columns rather than accidentally joining an identity of another type.
    for name in ('Ссылка', 'Номенклатура', 'Сумма', 'НомерСтроки'):
        if len(m.prop(part, name)['columns']) != 1:
            raise ValidationError(f'Составной реквизит Товары.{name} требует отдельной настройки.')
    owner = c(part, 'Ссылка', 'g')
    area = m.area(part, 'g')
    line = c(part, 'НомерСтроки', 'g')
    amount = f"CAST({c(part, 'Сумма', 'g')} AS decimal(28,6))"
    partition = f'PARTITION BY {owner}, {area}'
    # Order only by the native line number; identical products on different lines stay separate.
    windows = f'''[GoodsAmounts] AS (
 SELECT {owner} AS [OrderId], {area} AS [Area], {line} AS [Line],
 {c(part, 'Номенклатура', 'g')} AS [ProductId], {amount} AS [LineAmount],
 SUM({amount}) OVER ({partition}) AS [OrderTotal],
 SUM({amount}) OVER ({partition} ORDER BY {line} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS [ThroughAmount]
 FROM {m.table(part)} AS [g]
), [GoodsShares] AS (
 SELECT *,
 CAST(CAST([ThroughAmount] AS decimal(28,6)) / NULLIF(CAST([OrderTotal] AS decimal(28,6)), 0) AS decimal(18,12)) AS [ThroughShare],
 CAST(CAST([ThroughAmount] - [LineAmount] AS decimal(28,6)) / NULLIF(CAST([OrderTotal] AS decimal(28,6)), 0) AS decimal(18,12)) AS [BeforeShare]
 FROM [GoodsAmounts]
)'''
    fields = []
    for name in names:
        expression = allocated_amount(name) if name in ('Сумма', 'USD') else '[p].' + identifier(name)
        fields.append(expression + ' AS ' + identifier(name))
    fields.extend([
        '[i].[Line] AS [НомерСтрокиЗаказа]',
        '[i].[ProductId] AS [НоменклатураСсылка]',
        f'[n].{identifier(target["description"])} AS [Номенклатура]',
        '[i].[LineAmount] AS [СуммаСтрокиЗаказа]',
        '[i].[OrderTotal] AS [СуммаТоваровЗаказа]',
        '([i].[ThroughShare] - [i].[BeforeShare]) AS [ДоляНоменклатуры]',
        'CAST(100 * ([i].[ThroughShare] - [i].[BeforeShare]) AS decimal(18,8)) AS [ПроцентНоменклатуры]',
        "CASE WHEN [i].[OrderId] IS NULL THEN N'Нет товарных строк заказа' WHEN [i].[OrderTotal] = 0 THEN N'Нулевая сумма товарных строк' ELSE N'Распределено по сумме строк' END AS [РаспределениеПлатежа]",
    ])
    # Use the actual metadata column name for area checks against the payment's preserved data area.
    parts_area = m.objects[part].get('partitions', [])
    order_area = m.objects[order].get('partitions', [])
    reg_area = m.objects[REGISTER].get('partitions', [])
    if [p['key'] for p in parts_area] != [p['key'] for p in order_area] or [p['key'] for p in parts_area] != [p['key'] for p in reg_area]:
        raise ValidationError('Разделители данных заказа, товаров и платежа не совпадают.')
    # Zero-total orders get exactly one fallback row with the full payment, not one per item.
    fallback = '''OUTER APPLY (SELECT TOP (1) * FROM [GoodsShares] AS [z]
 WHERE [z].[OrderId] = [p].[ЗаказПоставщикуСсылка] AND [z].[Area] = [p].[ОбластьДанных]) AS [one_item]
 LEFT JOIN [GoodsShares] AS [goods] ON [goods].[OrderId] = [p].[ЗаказПоставщикуСсылка]
 AND [goods].[Area] = [p].[ОбластьДанных] AND [goods].[OrderTotal] <> 0
 OUTER APPLY (SELECT COALESCE([goods].[OrderId], [one_item].[OrderId]) AS [OrderId],
 COALESCE([goods].[OrderTotal], [one_item].[OrderTotal]) AS [OrderTotal],
 [goods].[Line], [goods].[ProductId], [goods].[LineAmount], [goods].[ThroughShare], [goods].[BeforeShare]) AS [i]'''
    product_area = m.same_area(part, 'unused', product, 'n')
    # The goods CTE exposes its separator under Area, not the source physical name.
    if target.get('partitions'):
        product_area = '[p].[ОбластьДанных] = ' + m.area(product, 'n')
    return ('WITH ' + turnover + ',\n[Payments] AS (\n' + payments + '\n),\n' + windows
        + '\nSELECT\n ' + ',\n '.join(fields) + '\nFROM [Payments] AS [p]\n' + fallback
        + f'\nLEFT JOIN [dbo].{identifier(target["table"])} AS [n] ON [i].[ProductId] = [n].{identifier(target["id"])} AND {product_area}')
