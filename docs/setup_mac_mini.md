# Prompt de arranque para la Mac mini

Pega el bloque siguiente en una sesión de Claude Code en la Mac mini (o síguelo a mano).
Antes de pegarlo copia a la Mac mini los dos archivos secretos que NO viajan en git:
`.env` y `credentials.json` (están en `/Users/dannerpacheco/development/Claude/microsip-etl/`
de la MacBook, o se regeneran como se indica abajo).

---

```
Vas a instalar y dejar operando el ETL "microsip-etl" (Microsip → BigQuery → Looker Studio)
en esta Mac mini. Trabaja en español, corre cada paso y verifica antes de pasar al siguiente.
No modifiques código salvo que un paso lo pida; si algo falla, muéstrame el error y para.

## 1. Requisitos
- Verifica git y Xcode Command Line Tools (`xcode-select -p`; si falta: `xcode-select --install`).
- Instala uv si no existe: `curl -LsSf https://astral.sh/uv/install.sh | sh` y abre una shell nueva.
- Opcional (solo si hay que regenerar la llave): Google Cloud SDK (`brew install --cask google-cloud-sdk`).

## 2. Clonar e instalar
    git clone git@github.com:dannerpacheco-afk/microsip-etl.git ~/microsip-etl
    cd ~/microsip-etl
    uv venv --python 3.12 .venv
    uv pip install -p .venv/bin/python -r requirements.txt pytest
    .venv/bin/python -m pytest tests -q        # deben pasar todos
Lee README.md y PLAN_BI_V2.md para entender tablas, vistas y comandos.

## 3. Secretos (no están en git)
Coloca en ~/microsip-etl:
- `.env`: copia desde la MacBook:
      scp dannerpacheco@MacBook-Pro-7.local:/Users/dannerpacheco/development/Claude/microsip-etl/.env ~/microsip-etl/.env
  Si no hay acceso por scp, créalo desde `.env.example` con estos valores y pide la API key:
      MICROSIP_API_URL=http://microsip-server:8000/api/v1
      MICROSIP_API_KEY=<pedir>
      MICROSIP_EMPRESA=ALMACENES PACHECO
      GCP_PROJECT_ID=lookerstudio-microsip-508301
      BQ_DATASET=microsip
      BQ_DATASET_PROVEEDORES=microsip_proveedores
      BQ_LOCATION=us-central1
      GOOGLE_APPLICATION_CREDENTIALS=./credentials.json
- `credentials.json`: llave de la service account
  microsip-etl@lookerstudio-microsip-508301.iam.gserviceaccount.com. Cópiala igual por scp, o
  genérala aquí (requiere gcloud y `gcloud auth login` con la cuenta de Google Workspace):
      gcloud iam service-accounts keys create ~/microsip-etl/credentials.json \
        --iam-account microsip-etl@lookerstudio-microsip-508301.iam.gserviceaccount.com \
        --project lookerstudio-microsip-508301
Verifica permisos: `chmod 600 .env credentials.json`.

## 4. Conectividad
- El servidor de la API se alcanza por Tailscale: `microsip-server` = `microsip-server.tail236a62.ts.net`
  (100.121.48.68). Instala Tailscale en esta Mac (`brew install --cask tailscale` o App Store),
  inicia sesión en la misma red (tailnet) y confirma con `tailscale status` que aparece microsip-server.
- API: `curl -s http://microsip-server:8000/health` debe responder `{"status":"ok",...}`.
  Si el nombre corto no resuelve, usa `http://microsip-server.tail236a62.ts.net:8000/api/v1`
  (o la IP 100.121.48.68) en MICROSIP_API_URL.
- Endpoints /etl (existen solo después del deploy de la rama feat/etl-endpoints de la API):
      curl -s -o /dev/null -w "%{http_code}\n" -H "X-API-Key: $(grep ^MICROSIP_API_KEY .env | cut -d= -f2)" \
        "http://microsip-server:8000/api/v1/etl/catalogos-aux?tabla=sucursales"
  200 = listo; 404 = la API aún no tiene el router /etl, avísame y detente después del paso 5.
- BigQuery: `.venv/bin/python main.py ensure-tables` debe terminar sin error (usa credentials.json).

## 5. Primera carga (funciona aunque /etl no esté desplegado)
    .venv/bin/python main.py catalogs        # dims básicas; las que usan /etl fallan hasta el deploy
    .venv/bin/python main.py transactions    # encabezados de ventas, últimos 45 días
Confirma en BigQuery (dataset microsip) que dim_articulos, dim_clientes y ventas_documentos tienen filas.

## 6. Cuando /etl responda 200 (después del deploy de la API)
    .venv/bin/python main.py backfill                       # 3 años, ~1 h, reanudable si se corta
    .venv/bin/python main.py backfill --tables ventas_documentos --from 2026-06-29 --to 2026-07-29   # mes que falló por un byte inválido en la API vieja
    .venv/bin/python main.py views                          # vistas para Looker
    .venv/bin/python main.py reporte-mensual --mes 2026-08 --sin-correo   # Excel ISCAM en reports/
Revisa el Excel generado y muéstrame el resumen (filas por hoja, totales).

## 7. Programación con launchd (corrida diaria 02:30 y reporte el día 2 a las 03:30)
    cd ~/microsip-etl && mkdir -p logs
    for j in nightly reporte-mensual; do
      sed "s#__ETL_DIR__#$PWD#g" deploy/launchd/com.grupopacheco.microsip-etl.$j.plist \
        > ~/Library/LaunchAgents/com.grupopacheco.microsip-etl.$j.plist
      launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.grupopacheco.microsip-etl.$j.plist
    done
    launchctl list | grep microsip-etl
Prueba el nightly una vez: `launchctl kickstart -k gui/$(id -u)/com.grupopacheco.microsip-etl.nightly`
y revisa logs/etl-<fecha>.log. Evita que la Mac duerma de noche: `sudo pmset -a sleep 0` (o
Ajustes → Energía → "Impedir que se suspenda automáticamente").

## 8. Entrega
Repórtame: versión de Python usada, resultado de los tests, filas por tabla en BigQuery,
estado de los dos jobs de launchd y cualquier paso que no hayas podido completar.
```
