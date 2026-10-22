# ERP_to_DWH integration

Source: https://github.com/Hvidsmen/ERP_to_DWH

Imported commit: 53cfc4d79a104ba548be1835f1548bf2402cb762

The original app and engine were retained under `transferdata.erp`; routes/templates/static assets were namespaced. The existing `control` model label and migrations are retained for export compatibility. Authentication uses the shared portal. Workers and ETL data remain separate.
