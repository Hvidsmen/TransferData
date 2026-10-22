-- Source login MUST be SELECT-only. Replace object names with your 1C schema.
-- Preserve binary 1C references; do not CAST them to strings.
SELECT [_IDRRef] AS [Reference], [_Code] AS [Code], [_Description] AS [Description]
FROM [dbo].[_Reference123];
