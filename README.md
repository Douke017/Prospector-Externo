# Prospector Externo DATAX

Monolito modular con puertos hexagonales y registro de workflows. B13 ejecuta adquisición → RAW/snapshot → proyección → legacy → paquete; la API consulta el catálogo JSON compartido. Mongo es un backend opcional cuya operación real sigue pendiente de aceptación. Este repositorio entrega referencias y metadatos; no certifica disponibilidad remota ni descarga documentos para el consumidor.

## Estado de entrega al 24/10/2026

- 52 identidades lógicas: 45 operativas (incluye FDTA retenida) y 7 status-only; 39 configuraciones físicas.
- Las siete corridas existentes cubren las 39 físicas: **8 SUCCESS, 25 PARTIAL_SUCCESS, 4 FAILED, 2 EXTERNAL_BLOCKER**. `READY` describe preparación del paquete, no exhaustividad.
- Catálogo compartido verificado: **39 fuentes, 10.815 referencias RAW**. No sumar registros de paquetes independientes como si fueran un conjunto único.
- BBV: 1.000 RAW / 931 seleccionados; FINRURAL: 203 RAW / 135 seleccionados. Revisión offline conjunta: **1.066 registros**, sin sustituir el ZIP histórico de 780.
- QA final offline: **763 PASS, 1 SKIP, 80,79 %** de cobertura `src/ + apps/`. Matriz HU: 12 PASS / 7 PARTIAL / 2 EXTERNAL_BLOCKER / 1 FAIL, con alcance funcional y pendientes de producción.

La [guía de entrega](docs/final_delivery_summary.md#cierre-de-entrega-verificado--24102026) contiene la matriz de 52 identidades, resultados por corrida, contratos, hashes, límites y comandos Git propuestos. [Evidencia del cierre](docs/audit/20261024_delivery_closure.json). Los antecedentes permanecen en esa guía.

## 1. Recibir e instalar el código en Windows

Usar Python 3.13.2 para reproducir el entorno de QA de esta entrega. `pyproject.toml` declara `>=3.9`; no se certificaron todas las versiones admitidas. Desde la raíz del clon:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

La instalación requiere acceso al repositorio de dependencias; no se ejecutó durante este cierre. No es necesario activar el entorno ni cambiar ExecutionPolicy para invocar su Python. API JSON usa las dependencias base; Playwright/browser y Mongo requieren sus extras e infraestructura cuando se autorice validarlos. No se entrega `.venv` ni se presupone que Docker está activo.

## 2. Recibir los datos por separado

Un clon de Git entrega código/configuración/documentación/tests; **no incluye `output/catalog`, `.runtime` ni paquetes ZIP**. Recibir del responsable:

- `output/data-transfer/20261024-cierre/catalog-json-20261024.zip`: **2.309.382 bytes**, 195 archivos JSON, 30.575.382 bytes sin comprimir. SHA-256 `1a70ad7d05b31114cf9993dbf7f207240ff0a3c7378e60d2f9e034dd9eea45fd`.
- Opcionalmente los paquetes DATAX por corrida de la [tabla de entrega](docs/final_delivery_summary.md#paquetes-verificados-y-transferencia). Sus manifests están dentro del ZIP. La revisión de 1.066 registros es la vigente para BBV/FINRURAL.

Colocar el ZIP recibido en `output/data-transfer/20261024-cierre/`. Comprobar y extraer a un destino nuevo para conservar cualquier catálogo existente:

```powershell
$archive = '.\output\data-transfer\20261024-cierre\catalog-json-20261024.zip'
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne '1a70ad7d05b31114cf9993dbf7f207240ff0a3c7378e60d2f9e034dd9eea45fd') { throw 'SHA-256 incorrecto' }
$restore = '.\output\received-20261024'
if (Test-Path -LiteralPath $restore) { throw 'Usar otro destino nuevo' }
.\.venv\Scripts\python.exe -B -m zipfile -t $archive
.\.venv\Scripts\python.exe -B -m zipfile -e $archive $restore
```

Resultado: `output/received-20261024/catalog/state/sources.json` y el resto del catálogo. Se verificó una restauración y lectura API sin cambios en sus 195 archivos. Conserva snapshots, observaciones y reportes existentes; metadatos históricos de owners y `deliveries.json` pueden referirse al host original. La consulta API usa su nueva raíz relativa; este ZIP **no es una migración para reanudar procesos o pipelines de otro equipo**.

El empaquetador B13 no exporta el catálogo global. El ZIP de transferencia se creó con `python -m zipfile`, sin un pipeline adicional, sin HTTP, logs ni `.runtime`. Para repetir la captura del catálogo en reposo, elegir un nombre NUEVO y ejecutar:

```powershell
$archive = '.\output\data-transfer\20261024-cierre\catalog-json-nueva-captura.zip'
if (Test-Path -LiteralPath $archive) { throw 'No sobrescribir una captura' }
.\.venv\Scripts\python.exe -B -m zipfile -c $archive .\output\catalog
Get-FileHash -LiteralPath $archive -Algorithm SHA256
```

El hash publicado corresponde al ZIP entregado; una captura posterior debe llevar su propio hash. La API puede leer durante la captura, pero no debe haber un proceso de adquisición/sincronización escribiendo el catálogo. La captura de esta entrega se contrastó archivo por archivo con la línea base.

## 3. Servir el catálogo recibido

```powershell
.\.venv\Scripts\python.exe -m apps.catalog_api.main --backend json --catalog-dir .\output\received-20261024\catalog --host 127.0.0.1 --port 8000
```

Si se trabaja con el catálogo original de este checkout, usar `--catalog-dir .\output\catalog`. El directorio explícito debe contener `state/sources.json` o el CLI rechaza arrancar. No hace falta una nueva carga para consultar los datos recibidos. Detener con Ctrl+C.

Swagger: http://127.0.0.1:8000/docs · OpenAPI: http://127.0.0.1:8000/openapi.json

| Método y endpoint | Contrato efectivo |
|---|---|
| `GET /health` | 200 `{"status":"UP","service":"catalog-api-facade"}`; 503 si falla la comprobación del repositorio |
| `GET /catalog/sources` | Array `Source[]`; `limit=1..500`, `offset>=0` opcionales; orden por identidad al paginar |
| `GET /catalog/sources/{source_id}` | Una fuente; 404 si no existe |
| `GET /catalog/resources?source_id=bbv` | Array RAW `ResourceCandidate[]`, excluye REMOVED; filtro antes de paginar, `limit=1..500`, `offset>=0`. Sin paginación conserva la lista legacy |
| `GET /catalog/sources/{source_id}/projection` | `schema_version=datax-projection-1.0`, `run_id`, `resources_hash`, totales, `families[].periods[].representations[]` seleccionados y `decisions` con exclusiones. Completa, sin paginación; 404 sin fuente/snapshot |
| `GET /runs?limit=20` | Reportes recientes `RunReport[]`; máximo 100 |
| `GET /runs/{run_id}` | Estado `Run`, sin hostname/PID del owner; 404 si no existe |
| `GET /runs/{run_id}/report` | `RunReport`, resultados con `execution_status` y métricas `coverage`; 404 si no existe |
| `GET /sources/{source_id}/observations?limit=20` | Observaciones; máximo 50; puede devolver lista vacía |
| `POST /mapping/inspect` | `{"url":"https://example.org/data.csv"}` o `{"resource":{"url":"https://example.org/data.csv"}}`; normalización, tipo inferido, SHA de URL, `hash_basis=normalized_url`, `inspection_mode=offline_url_only` |

Parámetros/payload inválidos →422; métodos no implementados →405. El `run_id` de REST es el **crawl_run_id**, no el `pipeline_run_id` que agrupa fuentes/artefactos B13. Una fuente fallida puede conservar RAW anterior: revisar fechas, observaciones y `run_id`, no inferir adquisición nueva del número de recursos.

La proyección REST usa agrupamiento técnico por defecto; una exportación puede aplicar `config/grouping/{fuente}.yaml`. BCB demuestra esa diferencia: **585 seleccionados REST frente a 537 legacy exportados**, sobre el mismo snapshot; 48 quedan fuera del contrato de agrupamiento de exportación. No son resultados intercambiables. No hay `/search/export-index` ni `UnifiedSearchDocument` acordados; frontend/Bridge/Interno quedan fuera del alcance.

Validación del consumidor, con la API encendida en otra terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
(Invoke-RestMethod 'http://127.0.0.1:8000/catalog/resources?source_id=bbv').Count
(Invoke-RestMethod http://127.0.0.1:8000/catalog/sources/bbv/projection).total_selected_resources
(Invoke-RestMethod http://127.0.0.1:8000/catalog/sources/finrural/projection).total_selected_resources
```

Esperado: UP, 1000, 931, 135. Los enlaces son referencias: estas consultas no prueban que un servidor remoto permita descargarlos.

## 4. Comandos operativos existentes

Durante este cierre **no se ejecutó adquisición**. Los siguientes comandos documentan la operación futura cuando corresponda; no son necesarios para arrancar la API sobre el ZIP recibido. B13 procesa las fuentes **secuencialmente por subprocess**; no hay una medición de velocidad global ni adquisición paralela certificada.

Plan sin red ni escrituras:

```powershell
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main plan --mode incremental --source-ids finrural,bbv --catalog-dir .\output\catalog --json
```

Carga inicial con ID NUEVO, actualización con otro ID y recuperación de la misma selección/mismo catálogo:

```powershell
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main run --mode initial --run-id nueva-carga-01 --source-ids finrural,bbv --catalog-dir .\output\catalog
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main run --mode incremental --run-id nueva-actualizacion-01 --source-ids finrural,bbv --catalog-dir .\output\catalog
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main run --run-id nueva-actualizacion-01 --resume --catalog-dir .\output\catalog
```

No reutilizar IDs históricos como IDs nuevos. Sin `--source-ids` se consideran las 45 operativas; FDTA conserva `IDENTITY_UNVERIFIED_FDTA_ICCO`. Las siete status-only no se adquieren. `--resume` protege modo, selección, catálogo y fingerprints; una adquisición completa no se reabre por copiar evidencia. API/custom reinician semillas; HTML/comentarios/JavaScript tienen cursor durable. Los cinco alias habilitados conservan identidad y procedencia, sin certificar una exportación lógica nueva por compartir evidencia física.

Empaquetar los exports corregidos existentes, sin adquisiciones HTTP ni sobrescritura del paquete histórico:

```powershell
.\.venv\Scripts\python.exe -B -m apps.b13_pipeline.main package-offline --run-id carga-20261023-171007 --projection-dir output/resource-quality/carga-20261023-171007/reprojection-verified --catalog-dir output/catalog
```

Genera otra revisión con el mismo empaquetador B13 y `schema_version=1.0.0`; exige snapshots/exports de origen coherentes, no basta el ZIP del catálogo recibido. La revisión ya entregada es `20261023T221057_2179de3cbbaa`: ZIP `output/b13-pipeline/carga-20261023-171007/datax-package/datax_package_20261023T221057_2179de3cbbaa.zip`, SHA-256 `e836c1c1fe2550a12ec67d52704e0923336d14d9d4c4e196cbd747aacf65d995`. Manifest en `datax-package/revisions/20261023T221057_2179de3cbbaa/manifest.json`. No se repitió el empaquetado B13 en este cierre.

`apps.catalog_sync` valida/importa evidencias JSON hacia Mongo, sin recrawl; `--check` es offline y `--commit` escribe en Mongo. No es una herramienta de transferencia del catálogo JSON. No se ejecutó importación ni despliegue.

## 5. QA y pendientes de aceptación

Repetir tests offline con una carpeta TEMP nueva; dejar `DATAX_BROWSER_INTEGRATION` sin activar:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
Remove-Item Env:DATAX_BROWSER_INTEGRATION -ErrorAction SilentlyContinue
$qa = Join-Path $env:TEMP ('datax-qa-' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $qa | Out-Null
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp (Join-Path $qa 't') --cov=src --cov=apps --cov-report=term --cov-fail-under=80
```

La suite usa mocks y fixtures localhost; la prueba de Chromium real de 30 páginas es opt-in y se omite. Los tests de Mongo/Compose/scheduler verifican contratos o fakes y no certifican infraestructura real. HU-14/15 siguen EXTERNAL_BLOCKER; HU-21 FAIL por falta de cinco SUCCESS actuales y MEFP bajo hold. Quedan decisiones sobre Pydantic/dataclasses, 250 vs 500 URLs y cadencia.

**HU-16 / Decisión 1.2:** los scripts `ops/run_scheduled_batch.ps1` y `register_windows_scheduler.ps1` son opt-in, no se activaron ni se certificaron en operación. El bloqueo por archivo solo cubre el mismo host/directorio, no coordinación multinodo. El wrapper histórico ejecuta el batch directo, no B13; no asumir que genera paquetes DATAX. Docker/Mongo reales y aceptación del consumidor requieren su propia validación; no bloquean la entrega del código desarrollado.

La selección exacta propuesta está en [docs/git_stage_selection.txt](docs/git_stage_selection.txt). Excluye datos, entorno, runtime y temporales. No se creó rama, no hay stage, commit ni push ejecutados por este cierre.
