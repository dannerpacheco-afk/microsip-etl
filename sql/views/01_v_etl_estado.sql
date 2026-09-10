-- Última corrida por tabla (monitoreo en Looker: página "Estado del ETL").
CREATE OR REPLACE VIEW `{project}.{dataset}.v_etl_estado` AS
SELECT
  table_name,
  last_sync_date,
  last_run_at,
  records_synced,
  status,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), last_run_at, HOUR) AS horas_desde_corrida
FROM `{project}.{dataset}._etl_sync_state`
QUALIFY ROW_NUMBER() OVER (PARTITION BY table_name ORDER BY last_run_at DESC) = 1;
