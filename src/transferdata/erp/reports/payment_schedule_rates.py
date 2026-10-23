"""As-of cross rates against RUB, using actual 1C metadata and multiplicities."""
from django.core.exceptions import ValidationError
from .cash_receipts_sql import corrected
from .payment_schedule import REGISTER, RATES, ZERO, concrete_references


def conversion(m, order, order_alias, currencies, currency_id):
    c = m.column
    if len(currencies) != 1:
        raise ValidationError('Для курса нужна одна конкретная валюта платежа.')
    currency = next(iter(currencies))
    if len(m.prop(order, 'Валюта')['columns']) != 1:
        raise ValidationError('Составной тип валюты заказа требует отдельной настройки пересчёта.')
    if concrete_references(m.objects[RATES], 'Валюта') != [currency]:
        raise ValidationError('Валюта регистра курсов не совпадает с валютой заказа.')
    extra = [p['name'] for p in m.objects[RATES]['properties'] if p.get('is_dimension') and p['name'] != 'Валюта']
    if extra:
        raise ValidationError('Нужно настроить дополнительные измерения курсов: ' + ', '.join(extra))
    # No implicit choice between USD, USD3, synthetic currencies or duplicate codes.
    codes = []
    for name in ('БуквенныйКод', 'Код'):
        if any(p['name'] == name for p in m.objects[currency]['properties']):
            codes.append(c(currency, name, 'cc'))
    if not codes:
        raise ValidationError('В справочнике валют нет кода для определения USD и RUB.')
    joins = []
    for alias, alpha, numeric in [('usd_currency', 'USD', '840'), ('rub_currency', 'RUB', '643')]:
        predicate = ' OR '.join(f"LTRIM(RTRIM(CONVERT(nvarchar(100), {code}))) IN (N'{alpha}', N'{numeric}')" for code in codes)
        joins.append(f'''OUTER APPLY (SELECT TOP (1) {c(currency, 'Ссылка', 'cc')} AS [Id], COUNT_BIG(*) OVER () AS [Matches]
 FROM {m.table(currency)} AS [cc] WHERE ({predicate}) AND {c(currency, 'Ссылка', 'cc')} <> {ZERO}
 AND {m.same_area(order, order_alias, currency, 'cc')}) AS [{alias}]''')
    raw_date = c(REGISTER, 'ДатаКурса', 't')
    date = corrected(raw_date)
    offset = m.objects[REGISTER].get('year_offset')
    if offset not in (0, 2000):
        raise ValidationError('Неизвестное смещение дат 1С для пустой ДатаКурса.')
    empty = f"CONVERT(date, {raw_date}) = CONVERT(date, '{1 + offset:04d}0101', 112)"
    effective = f"CASE WHEN {date} IS NULL OR YEAR({date}) = 1 OR {empty} THEN [t].[PaymentDay] ELSE {date} END"
    joins.append(f'OUTER APPLY (SELECT CONVERT(date, {effective}) AS [Date]) AS [fx_date]')
    for alias, reference in [('payment_rate', currency_id), ('usd_rate', '[usd_currency].[Id]')]:
        period = corrected(c(RATES, 'Период', 'fx'))
        joins.append(f'''OUTER APPLY (SELECT TOP (1) {c(RATES, 'Курс', 'fx')} AS [Rate], {c(RATES, 'Кратность', 'fx')} AS [Multiplicity],
 COUNT_BIG(*) OVER (PARTITION BY {c(RATES, 'Период', 'fx')}) AS [Matches]
 FROM {m.table(RATES)} AS [fx]
 WHERE {c(RATES, 'Валюта', 'fx')} = {reference} AND {m.same_area(REGISTER, 't', RATES, 'fx')}
 AND CONVERT(date, {period}) <= [fx_date].[Date]
 ORDER BY {period} DESC) AS [{alias}]''')
    amount = c(REGISTER, 'СуммаПлатежа', 't')
    is_usd = f'{currency_id} = [usd_currency].[Id]'
    is_rub = f'{currency_id} = [rub_currency].[Id]'
    good_usd = '[usd_currency].[Matches] = 1 AND [rub_currency].[Matches] = 1'
    good_payment = f'''(({is_rub} AND [rub_currency].[Matches] = 1) OR
 ([payment_rate].[Matches] = 1 AND [payment_rate].[Rate] > 0 AND [payment_rate].[Multiplicity] > 0))'''
    valid = f'''{good_usd} AND ({is_usd} OR ({currency_id} <> {ZERO} AND {good_payment}
 AND [usd_rate].[Matches] = 1 AND [usd_rate].[Rate] > 0 AND [usd_rate].[Multiplicity] > 0))'''
    # A bad rate must abort the transaction rather than silently emit NULL/zero or omit a payment.
    # Conversion of a row-dependent diagnostic raises on missing/ambiguous/invalid rates.
    guard = f"CONVERT(decimal(28,8), CASE WHEN {valid} THEN N'1' ELSE CONCAT(N'Нет однозначного курса USD/валюты платежа на ', CONVERT(nvarchar(10), [fx_date].[Date], 120)) END)"
    value = f'''CASE WHEN {is_usd} THEN {amount} ELSE
 CAST({amount} AS decimal(28,8)) *
 (CASE WHEN {is_rub} THEN CAST(1 AS decimal(28,12)) ELSE CAST([payment_rate].[Rate] AS decimal(28,12)) / NULLIF([payment_rate].[Multiplicity], 0) END)
 / NULLIF(CAST([usd_rate].[Rate] AS decimal(28,12)) / NULLIF([usd_rate].[Multiplicity], 0), 0) END'''
    return f'({value}) * ({guard})', joins
