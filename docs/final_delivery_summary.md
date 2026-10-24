# Estado de entrega DATAX — leer primero

## Cierre de entrega verificado — 24/10/2026

Consolidación offline solicitada en `DATAX_CIERRE_FINAL_ENTREGA_GIT_CODEX.md`. Se trabajó sobre código y adquisiciones existentes, sin crawling, optimizaciones, instalación, despliegue, scheduler ni cambios en frontend/Bridge/Interno. Se actualizaron README, esta guía, el enlace local del backlog y dos documentos de selección/evidencia. **Git sigue sin rama nueva, stage, commit o push.** Los apartados anteriores se conservan como antecedentes; sus métricas/comandos describen su fecha, no tareas ejecutadas hoy.

[README de instalación/consumo](../README.md) · [matriz de 52 identidades](#inventario-completo-y-cobertura-conservada) · [HU-01..22](#matriz-hu-01-a-hu-22) · [evidencia estructurada](audit/20261024_delivery_closure.json) · [selección exacta Git](git_stage_selection.txt).

### Aceptación sustentada

- Inventario: **52 lógicas, 45 operativas, 7 status-only, 39 físicas**. Partición física demostrada en siete corridas: **8 SUCCESS / 25 PARTIAL_SUCCESS / 4 FAILED / 2 EXTERNAL_BLOCKER**. No hay PENDING en esos estados finales; una cola de URLs pendiente no equivale a un paso B13 PENDING.
- Los **8 ZIP** (7 históricos y 1 revisión) pasan CRC, igualdad de manifest interno/externo, SHA de artefactos y conteo/validación legacy. No se suman como catálogo único. Todos los manifests B13 citados usan **`schema_version=1.0.0`**; la revisión incorpora origen, hashes de proyección/export y referencias al snapshot.
- Catálogo transferido: **39 fuentes / 10.815 RAW**, 195 JSON. La API restaurada pasó **57 comprobaciones ASGI**: fuentes, RAW, las 33 proyecciones no fallidas, reportes, observaciones, Swagger/OpenAPI y respuestas 200/404/422/405/503. Ninguna lectura alteró sus archivos. No se confunde contenido anterior conservado con una adquisición nueva de una fuente fallida.
- BBV: 1.000 RAW, **931 seleccionados**; FINRURAL: 203 RAW, **135 seleccionados**. Se conserva la corrección de 423 paths PDF `%20` y exclusión de oEmbed de la selección BBV; el RAW queda intacto. La revisión de **1.066** y el original de **780 registros** siguen íntegros. No hubo HEAD/GET a documentos remotos en este cierre.
- **BCB: 537 legacy exportados / 585 seleccionados REST.** Es una diferencia contractual existente: el paquete usa `config/grouping/bcb.yaml`, `unmatched_policy: exclude`; la API usa agrupamiento técnico por defecto. Con idénticos `crawl_run_id/resources_hash`, 48 cambian de pertenencia por `grouping_contract_unmatched` (47 documentos públicos y 1 estructurado). Reaplicar en memoria el contrato BCB reproduce **toda la proyección histórica exactamente**. Las otras 32 proyecciones consultadas coinciden en conteo con su entrega vigente; eso no certifica agrupamiento idéntico. No se reempaquetó BCB ni se cambió REST.
- QA global offline: **763 PASS / 1 SKIP / 2 warnings; 12.423/15.377 líneas = 80,78949 %**, gate ≥80; proceso completo 241,89 s. Chromium real opt-in omitido. Warnings de dependencias, sin instalar actualizaciones. Logs/cobertura en TEMP; métricas y digest en auditoría. Se ejecutó **una sola suite global**; después pasaron **9 tests documentales** existentes de Docker/scheduler y la validación de 52 filas, 22 HU, 62 enlaces locales y selección Git.
- Integridad: **900 archivos** de las siete corridas, runtime correspondiente y catálogo contrastados contra SHA inicial/final sin cambios. El ZIP de transferencia es una salida nueva separada.

ASFI/Denmark conservan la reparación de temporales Windows de 260/263 caracteres y recuperación `resume/NOT_DUE`; ASFI aún tiene **25 pendientes**. `otros_historicos` es parcial por **dos errores de inspección de archivos** aun con cero pendientes. CEPAL HTTP 403; min_educacion TIMEOUT; senamhi/mdryt_oap ROBOTS_UNREACHABLE. MHE/SIGMA tienen adquisición FAILED y clasificación EXTERNAL_BLOCKER. SICOES: SUCCESS/EMPTY_RESULT, cero recursos; TRANSTATS: SUCCESS/ACQUISITION_JOB_READY, sin legacy ficticio. UNDATA parcial por semillas API curadas. ANAPO/MMYM excluyen CMS/oEmbed del DATAX sin borrar RAW. Otros parciales conservan límites de requests/runtime/enlaces/recursos/paginación, detallados en los reportes.

### HU, arquitectura y límites de aceptación

Se contrastó `BACKLOG_USER_STORIES.md` con `documento-arquitectura-prospector-externo.md` y la [matriz HU existente](#matriz-hu-01-a-hu-22): **12 PASS / 7 PARTIAL / 2 EXTERNAL_BLOCKER / 1 FAIL**. PASS es funcional y acotado a la evidencia; no acredita DoD transversal completo ni revisión por pares ya aprobada. El documento del backlog conserva sus estados históricos sin cambiar requisitos; únicamente se corrigió su hipervínculo local.

Permanecen Pydantic vs dataclasses (HU-01), default 250 frente al criterio 500 (HU-18), aprobación de cadencia/scheduler y contrato del consumidor. HU-21 no tiene los cinco SUCCESS; MEFP continúa bajo hold técnico TLS/robots. Mongo real y Docker: EXTERNAL_BLOCKER; los fakes/tests estáticos no los certifican. HU-22 demuestra recuperación JSON, no Mongo/contenedor. La evidencia anterior de Chromium local se conserva y no se repitió ahora.

La arquitectura hexagonal/microkernel existente no cambia. B13 sigue **secuencial por subprocess**, sin promesa de velocidad global ni coordinación multinodo. Scheduler no activado. Integración de producción/autenticación/TLS y aceptación del consumidor siguen pendientes; no bloquean entregar el desarrollo y sus límites documentados.

### Paquetes verificados y transferencia

R0 conserva su estado/manifest original de 780; R0r es otra revisión offline, no modifica ese estado. Fechas internas UTC; IDs de lote tomados del reloj local del operador. Rutas de manifests, tamaños y SHA de reportes están en la auditoría JSON.

| Ref. / pipeline_run_id | Estado B13 | Legacy de ese paquete | ZIP relativo a raíz | SHA-256 verificado |
|---|---|---:|---|---|
| R0 `carga-20261023-171007` | READY | 780 | `output/b13-pipeline/carga-20261023-171007/datax-package/datax_package.zip` | `8ff69dacabcce41c8f24381aa95617d12ff9acddc546e383d2578f0e32fad8af` |
| R1 `validacion-fuentes-01-20261023-181728` | READY | 1541 | `output/b13-pipeline/validacion-fuentes-01-20261023-181728/datax-package/datax_package.zip` | `f1bdea2f1397e4dc97f376d2c0534c2732c23dca8906e677f27d5fcd2c8f41dc` |
| R2 `aceptacion-lote-02-20261023-184753` | FAILED | 590 | `output/b13-pipeline/aceptacion-lote-02-20261023-184753/datax-package/datax_package.zip` | `0e8a7f51ea95f388262f66cdfa74f1c85a7ca9c00a0b81db8c5c56439a2269f8` |
| R3 `aceptacion-lote-03-20261023-204129` | READY | 2842 | `output/b13-pipeline/aceptacion-lote-03-20261023-204129/datax-package/datax_package.zip` | `86b2283f7661ab3f9e4f468d340fac1ff16cc4009c66656b01eb1782dfbdaf00` |
| R4 `aceptacion-lote-04-20261023-221223` | FAILED | 302 | `output/b13-pipeline/aceptacion-lote-04-20261023-221223/datax-package/datax_package.zip` | `0287ef4fafeeb442366143a662a41dcd5c266e3698dfe4762a68dc5f803eea00` |
| R5 `aceptacion-lote-05-20261023-225630` | FAILED | 1964 | `output/b13-pipeline/aceptacion-lote-05-20261023-225630/datax-package/datax_package.zip` | `064b6147cacb21ebdca5eb8cc43701432f39691e1b7ed71e31f2e4fb03f16d6a` |
| R6 `aceptacion-lote-06-20261023-234425` | READY | 341 | `output/b13-pipeline/aceptacion-lote-06-20261023-234425/datax-package/datax_package.zip` | `d556d07a07268d2b9f4d723d448a540d5e0d652662186c828c5bf21ee7a84d29` |
| R0r `20261023T221057_2179de3cbbaa` | revisión offline | 1066 | `output/b13-pipeline/carga-20261023-171007/datax-package/datax_package_20261023T221057_2179de3cbbaa.zip` | `e836c1c1fe2550a12ec67d52704e0923336d14d9d4c4e196cbd747aacf65d995` |

Manifest R0r: `output/b13-pipeline/carga-20261023-171007/datax-package/revisions/20261023T221057_2179de3cbbaa/manifest.json`. R0–R6: `output/b13-pipeline/{pipeline_run_id}/datax-package/latest/manifest.json`. Entregar los ZIP existentes de esta tabla si el compañero necesita exports por corrida; contienen su manifest. No hacen falta para servir JSON. No enviar toda `.runtime`, `.venv`, cachés ni todo `output`.

**Datos para la API:** `output/data-transfer/20261024-cierre/catalog-json-20261024.zip`, **2.309.382 bytes** (195 archivos, 30.575.382 bytes descomprimidos), SHA-256 **`1a70ad7d05b31114cf9993dbf7f207240ff0a3c7378e60d2f9e034dd9eea45fd`**. Creado con la herramienta estándar `python -m zipfile`, sin otro pipeline. B13 `package-offline` solo empaqueta exports DATAX; `catalog_sync` valida/importa evidencia hacia Mongo. Ninguno exporta el catálogo JSON compartido. No se repitió empaquetado B13 ni se conectó Mongo.

La captura contiene únicamente `catalog/state` y `catalog/reports`; todos los miembros se contrastaron con origen. Retiene el historial actual para observaciones y `/runs`, sin árboles checkpointed/logs. No se detectaron claves privadas, tokens con prefijos conocidos ni URLs con credenciales en el escaneo; no es una certificación exhaustiva de secretos. `deliveries.json` y owners históricos guardan procedencia del host original: el ZIP es para **consulta**, no para reanudar pipelines/procesos en otro equipo. La API usa la nueva raíz relativa. No incluye runtime ni exports para reconstruir paquetes históricos.

**Comando exacto usado**, desde raíz (el directorio de destino se creó antes; no repetir sobre un archivo existente):

```powershell
.\.venv\Scripts\python.exe -B -m zipfile -c .\output\data-transfer\20261024-cierre\catalog-json-20261024.zip .\output\catalog
```

El README ofrece captura con nombre NUEVO y guardia de sobrescritura, SHA, extracción a destino nuevo y arranque JSON. Se probó restauración en TEMP aislado; los 195 SHA de miembros están en auditoría. Durante una captura no debe haber procesos escribiendo el catálogo.

### Contrato efectivo para el compañero

El [README](../README.md#3-servir-el-catálogo-recibido) documenta instalación Windows, `--backend json --catalog-dir`, Swagger/OpenAPI, filtros, paginación y ejemplos PowerShell. `git clone` no transportará el ZIP: compartirlo por el canal de archivos del equipo junto con su SHA.

`/catalog/resources` conserva array/campos RAW, excluye REMOVED y pagina opcionalmente; **no devuelve seleccionados**. `/catalog/sources/{id}/projection` devuelve `datax-projection-1.0` completo, sin paginar, desde el último snapshot y con agrupamiento técnico por defecto. Consumir `families[].periods[].representations[]`, revisar `decisions`, `run_id/resources_hash` y totales. Para exports con grouping usar el ZIP/manifest de su corrida. `/mapping/inspect` solo infiere desde la URL, hash de URL, sin descarga ni prueba de existencia.

`pipeline_run_id` agrupa B13. `crawl_run_id` es la adquisición física y el `run_id` de snapshot, proyección y `/runs/{run_id}`. Enviar un ID de pipeline a `/runs` no crea un reporte agregado: en la prueba dio 404. `/runs` lista RunReport, no estados B13. Reportes distinguen `execution_status`, `content_status`, `failure_code`, `skip_reason` y `coverage`; una omisión por estado no equivale a NO_CHANGE. Se verificaron 404 sin fuente/snapshot/run/report, 422 parámetros/payload inválidos, 405 métodos no implementados y 503 health con fallo de repositorio simulado. No se añadió `/search/export-index`, `UnifiedSearchDocument` ni contrato Bridge supuesto.

### Selección Git lista para revisión, sin ejecutar

Baseline comprobado: rama **`fix/prospector-cierre`**, HEAD **`7a7caead651529297737db941d84511b0f6533bf`**, **72 tracked modificados**, índice vacío. La enumeración inicial encontró **2.160 untracked**, frente a 2.133 citados en el mandato (diferencia 27), sin atribuir la diferencia a este cierre. Incluía 1.777 bajo output y 279 bajo runtime. La advertencia histórica Permission denied de `tests-initial` no se reprodujo con git ls-files: no se alteraron permisos ni se borró la carpeta. `git diff --check`: 0, sin errores reales; 68 avisos LF→CRLF informativos. No se modificó configuración Git global ni `.gitignore` durante esta intervención.

**124 rutas exactas** en [git_stage_selection.txt](git_stage_selection.txt): los 72 tracked, 50 untracked existentes relevantes y los dos documentos nuevos. Incluye `apps/catalog_sync/{__init__,main}.py`, `application/catalog_sync.py`, los 30 tests nuevos, los tres scripts ops, Dockerfiles/Compose/`.dockerignore`, backlog/arquitectura, ocho auditorías anteriores y la actual. Diff/archivos revisados, imports y contratos cubiertos por suite; sin patrones de claves/tokens conocidos ni literales de credenciales candidatas identificados. URLs peligrosas en tests son fixtures.

Las auditorías históricas conservan **rutas locales de TEMP/workspace** como procedencia. La auditoría nueva identifica los archivos afectados; no toma esas rutas como configuración ni altera evidencias originales. En esta guía se abreviaron prefijos TEMP y se hizo relativo el enlace del backlog, sin modificar requisitos ni resultados. Estas rutas son metadatos locales visibles en las auditorías seleccionadas, aunque no se detectaron credenciales.

**Exclusiones explícitas:** árboles `output/`, `.runtime/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `logs/`, `htmlcov/`; `.coverage*`, ZIP/binarios, `.patch12e/` a `.patch12i/`, `apply_batch_*.py`, validadores raíz `validar_exportacion_fuentes_12C.py`/`validar_rescate_fuentes_12B.py`, `INSTRUCCIONES_*.txt` y mandatos raíz `CODEX_*.md`/`DATAX_*.md`. Lista exacta de otros untracked excluidos en auditoría. Nada se borró ni ocultó con nuevos ignores: `.gitignore` sigue sin ocultar todo output/runtime, por eso es obligatoria la selección positiva.

Comandos **propuestos para aprobar y ejecutar después**, desde raíz; safe.directory solo por comando. Revisar primero las 124 rutas y HEAD. No se ha creado esta rama:

```powershell
$gitRoot = (Get-Location).Path.Replace('\','/')
git -c "safe.directory=$gitRoot" rev-parse HEAD
git -c "safe.directory=$gitRoot" switch -c delivery/prospector-externo-20261024
git -c "safe.directory=$gitRoot" add --pathspec-from-file=docs/git_stage_selection.txt
git -c "safe.directory=$gitRoot" diff --cached --check
git -c "safe.directory=$gitRoot" diff --cached --stat
git -c "safe.directory=$gitRoot" diff --cached --name-only
# Tras revisar selección y mensaje:
git -c "safe.directory=$gitRoot" commit -m "Entrega Prospector Externo DATAX: catalogo, proyeccion y evidencia"
git -c "safe.directory=$gitRoot" push -u origin delivery/prospector-externo-20261024
```

Se confirmó remoto origin sin conectarlo ni exponer URL. La aprobación pendiente corresponde a selección y operaciones Git; no se declara Git cerrado.

## Antecedentes de implementación y aceptación

Las secciones siguientes conservan fases anteriores. La matriz y los límites HU se actualizaron donde correspondía; los relatos de canarios, pruebas y comandos históricos no sustituyen la aceptación consolidada de arriba.

## Fase 1: ejecución y reanudación reparadas — 23/10/2026

Se ejecutó exclusivamente `CODEX_DATAX_CIERRE_FASE_1.md`, sobre el pipeline `validacion-fuentes-01-20261023-181728`. **No se declara cierre final ni se ejecutan las fases posteriores de aceptación.**

**Causas demostradas sin HTTP, con stack traces independientes.** ASFI fallaba en `save_discovery_checkpoint → _atomic_write_text → NamedTemporaryFile`: al anteponer al temporal el nombre completo del archivo de destino, la ruta alcanzaba **260 caracteres**. Statistics Denmark fallaba después de HTTP 200 en `save_snapshot → _mirror_evidence → NamedTemporaryFile`, con una ruta temporal de **263 caracteres**. Los destinos finales y los directorios eran válidos; fallaban los temporales por el límite de Windows. No eran fallos de portal, TLS ni contenido. Las trazas originales están en `.runtime/fase1_repair/file_failure_reproduction.json`.

Se usan nombres temporales aleatorios cortos en el mismo directorio, conservando escritura y reemplazo atómicos, nombres finales e identidades. Los errores posteriores a HTTP 200 durante reconciliación ahora dejan observación y reporte `FAILED / SOURCE_PERSISTENCE_ERROR`, sin proyectar un éxito supuesto; los logs conservan traceback completo. Si un proceso anterior dejó checkpoint `FAILED` sin reporte final, B13 lo presenta como `EXECUTION_FAILED`, en lugar de confundirlo con una fuente aún `PENDING`.

**Por qué aparecía NOT_DUE.** La política existente programa cuatro horas de espera después de un fallo. El resume volvía a aplicar esa cadencia y pasaba `FAILED → SKIPPED_STATUS`, terminal; el proceso hijo también comprobaba la fecha. Se separó la recuperación de un mismo pipeline de una ronda periódica nueva: solo una recuperación con identidad y configuración compatibles puede omitir esa espera; **suspensión, holds, robots, TLS, cortesía y presupuestos se mantienen**. La reparación del skip heredado exige un intento previo, salida fallida, `NOT_DUE`, fingerprint compatible y selección explícita. No reabre fuentes completadas o status-only y conserva el reporte original de la omisión. No se utilizó `--force` ni se editaron salud, configuración o límites por conveniencia.

Se respaldaron los punteros originales en `.runtime/fase1_repair/original/` y se ejecutó una única reanudación real autorizada, con la selección persistida:

```powershell
.\.venv\Scripts\python.exe -B -m apps.b13_pipeline.main run --run-id validacion-fuentes-01-20261023-181728 --resume
```

El sandbox bloqueó el preflight de conectividad con `WinError 10013`; la reanudación se ejecutó con acceso de red autorizado, sin cambiar la seguridad HTTP. Adquisiciones reales: **solo ASFI Valores y Statistics Denmark**.

| Fuente | Resultado real | RAW | DATAX | Solicitudes en este resume | Checkpoint |
|---|---|---:|---:|---:|---|
| ASFI Valores | PARTIAL_SUCCESS — REQUEST_BUDGET_REACHED | 1.000 | 1.000 | 250 | PARTIAL; intento 2 |
| Statistics Denmark | SUCCESS — API_SEEDS_EXHAUSTED | 1 | 1 | 2 | SUCCEEDED; intento 3 |
| BCB | SUCCESS preservado, ALREADY_COMPLETED | 585 | 537 | 0 | SUCCEEDED; intento 1 |
| VIPFE | SUCCESS preservado, ALREADY_COMPLETED | 3 | 3 | 0 | SUCCEEDED; intento 1 |

Statistics Denmark ya materializa snapshot, observación y reporte. No aparecen nuevos `FileNotFoundError` ni `UNHANDLED_WORKFLOW_EXCEPTION` en el segmento nuevo del log. ASFI mantiene **25 URLs pendientes** y 3 URLs fallidas (incluidos dos HTTP 403), dentro del presupuesto original; no se intentó forzar cobertura completa ni hacer otro resume. El batch declara una fuente parcial y una exitosa adquiridas, ninguna fallida. El pipeline queda `READY` para consumo, con `pending=[]`, `unresolved=[]` y `coverage_partial_sources=["asfi_valores"]`; el checkpoint conserva ASFI como candidata parcial reintentable. **READY describe el paquete, no cobertura exhaustiva.**

Nuevo paquete del lote: **1.541 registros**, cero grupos duplicados entre físicas.

- Manifest: `output/b13-pipeline/validacion-fuentes-01-20261023-181728/datax-package/latest/manifest.json`.
- ZIP: `output/b13-pipeline/validacion-fuentes-01-20261023-181728/datax-package/datax_package.zip`.
- SHA-256: `f1bdea2f1397e4dc97f376d2c0534c2732c23dca8906e677f27d5fcd2c8f41dc`.
- Validados CRC, contenido ZIP contra directorio y **8 checksums**. El manifest identifica los cuatro resultados, los `crawl_run_id` originales, `ALREADY_COMPLETED` y la cobertura parcial de ASFI. Los registros son referencias, no documentos descargados ni disponibilidad remota certificada.

**Pruebas:** 86 pruebas focalizadas aprobadas; las 11 regresiones específicas también pasaron con el código definitivo. Se ejecutó **una sola regresión global**, después de la validación real: **763 PASS, 1 SKIP opt-in, 2 avisos, 129,48 s; cobertura 80,79 % global sobre src/ + apps/**, gate ≥80 % aprobado. Incluye rutas Windows con espacios y límite de temporales, fallo tras HTTP 200, persistencia de observaciones/reportes, recuperación de FAILED/interrumpida/NOT_DUE heredado, cadencia periódica, suspensión y conservación de completadas. No se activó Chromium real.

**Integridad:** de 135 archivos existentes registrados antes del resume, **126 permanecen idénticos**. Los otros nueve son punteros de catálogo/estado y logs de la ejecución autorizada; el log principal conserva íntegro su prefijo anterior y se respaldaron los originales de runtime. No se borró ningún archivo ni se alteraron snapshots, observaciones, reportes o intentos anteriores. La evidencia BCB/VIPFE permanece idéntica y sus entidades del catálogo coinciden con sus copias conservadas. El paquete de 780 registros conserva `8ff69dacabcce41c8f24381aa95617d12ff9acddc546e383d2578f0e32fad8af`; la revisión de 1.066 conserva `e836c1c1fe2550a12ec67d52704e0923336d14d9d4c4e196cbd747aacf65d995`.

Archivos cambiados: `src/prospector_externo/adapters/persistence/local_json_adapter.py`, `src/prospector_externo/application/{dispatcher,orchestrator,status_gate}.py`, `src/execution/checkpointed_batch.py`, `apps/{checkpointed_batch,b13_pipeline}/main.py` y `tests/test_execution_resume_repair.py`. No hay runner, capa ni script nuevo. Evidencia focalizada y hashes del código: [audit/20261023_fase1_resume.json](audit/20261023_fase1_resume.json); logs de QA y baseline: `.runtime/fase1_repair/`. No se modificaron Bridge, frontend, Interno, scheduler ni README maestro de integración; sin commit/push.

Para validar el estado sin otra adquisición:

```powershell
Get-Content .runtime/b13_pipeline/validacion-fuentes-01-20261023-181728/state.json -Raw | ConvertFrom-Json | Select-Object status,coverage_partial_sources,pending,unresolved
```

Resultado esperado: READY, ASFI en cobertura parcial y arrays de tareas pendientes/unresolved vacíos. No hace falta intervención técnica adicional para los dos defectos reparados; la aceptación de otras fuentes y la decisión de cobertura ASFI corresponden a la fase siguiente, que no se ejecutó.

## Paquete corregido generado offline — 23/10/2026

La revisión **`20261023T221057_2179de3cbbaa`** está generada y verificada, sin recrawling. Utiliza las proyecciones y legacy ya existentes en `output/resource-quality/carga-20261023-171007/reprojection-verified/`. El empaquetador B13 ya preservaba paquetes y creaba revisiones; se añadió únicamente el subcomando `package-offline` en su misma entrada, que valida los exports y llama al mismo `build_package`. No ejecuta `run`, `resume`, adquisición, sincronización ni escritura de snapshots/checkpoints/estado de corrida.

| Fuente | RAW de origen | Seleccionados / registros legacy del paquete |
|---|---:|---:|
| BBV | 1.000 | 931 |
| FINRURAL | 203 | 135 |
| Total | 1.203 | **1.066** |

Desde la raíz del repositorio, repetir el empaquetado offline con:

```powershell
.\.venv\Scripts\python.exe -B -m apps.b13_pipeline.main package-offline --run-id carga-20261023-171007 --projection-dir output/resource-quality/carga-20261023-171007/reprojection-verified --catalog-dir output/catalog
```

Cada ejecución crea otra revisión y muestra sus rutas y SHA-256; conserva tanto `latest` como las revisiones previas. El comando exige un paquete de origen coherente con fuentes `DATAX_READY`, exports completos y sin modificaciones, y correspondencia exacta con los snapshots históricos. Al pasar `--catalog-dir`, exige también igualdad con la proyección en memoria del catálogo que sirve la API; si el catálogo cambia, falla antes de crear la revisión. El agrupamiento es el técnico por defecto, igual que el endpoint `/catalog/sources/{source_id}/projection`.

No hace falta repetir la reproyección para este paso. El baseline de la comparación de calidad del antecedente corresponde al árbol previo a estas revisiones; el comando de empaquetado de arriba es la entrada repetible vigente y comprueba el paquete de origen sin exigir que no existan revisiones nuevas.

Artefactos de esta ejecución:

- Manifest: `output/b13-pipeline/carga-20261023-171007/datax-package/revisions/20261023T221057_2179de3cbbaa/manifest.json`
- ZIP: `output/b13-pipeline/carga-20261023-171007/datax-package/datax_package_20261023T221057_2179de3cbbaa.zip`
- SHA-256 del ZIP: **`e836c1c1fe2550a12ec67d52704e0923336d14d9d4c4e196cbd747aacf65d995`**
- Evidencia: [audit/20261023_offline_package.json](audit/20261023_offline_package.json).

El ZIP contiene **12 archivos**: legacy en `sources/*.json`, proyecciones en `projections/*.json`, manifiestos originales de exportación y del paquete anterior en `provenance/`, índice CSV, README, manifiesto y checksums. Se verificaron **11 checksums** (todos los archivos salvo el propio listado), CRC ZIP y coincidencia exacta entre los archivos comprimidos y su directorio. El contrato legacy y el formato previo de manifest se mantienen; la trazabilidad adicional es compatible: `revision`, referencias/hash de snapshot, `crawl_run_id`, `input_resources_hash` y rutas/hash de proyección/export por fuente.

Se comprobó igualdad completa entre las proyecciones incluidas y las respuestas HTTP ASGI de `/catalog/sources/bbv/projection` y `/catalog/sources/finrural/projection` usando `output/catalog`. BBV mantiene **cero oEmbed seleccionados y 423 PDF con `%20`**. Las fuentes mantienen su cobertura parcial y su identidad de adquisición original; no se da por descargado ni disponible ningún documento remoto.

El paquete original de **780** registros conserva el SHA-256 **`8ff69dacabcce41c8f24381aa95617d12ff9acddc546e383d2578f0e32fad8af`**. Se protegieron **72 archivos existentes**, incluidos catálogo, snapshots, artefactos corregidos y estado runtime, sin cambios de bytes. El estado histórico sigue apuntando al paquete anterior: para consumir esta revisión se debe usar el manifest indicado arriba. No se sobrescribió `latest`, no se borraron archivos ni se modificaron frontend, Bridge o Interno; sin commit/push.

Pruebas de los componentes modificados: **41 aprobadas**, incluidas conservación al repetir, bloqueo de red/adquisición y rechazo previo de hashes, conteos, contenido legacy e identidades/snapshots incompatibles. **Regresión global: 752 PASS, 1 SKIP opt-in, 2 avisos, 192,16 s; cobertura 12.362/15.345 = 80,56 % sobre src/ + apps/**, gate ≥80 % aprobado. Solo se añadieron los 12 archivos de la revisión y su ZIP en los árboles protegidos; los 72 archivos previos siguen idénticos después de todas las pruebas. La evidencia citada registra logs, hashes y validaciones; los resultados de la corrección previa se conservan abajo como antecedente.

## Corrección focalizada de recursos y consumo — 23/10/2026

Revisión de `carga-20261023-171007`, sin nueva adquisición ni cierre de entrega. El catálogo, los 780 registros publicados, snapshots, manifiestos y ZIP históricos se conservan. La política corregida se aplicó únicamente a una reproyección offline aislada; servir la API con el código actualizado calcula la selección nueva en memoria, sin reemplazar el paquete anterior.

**Diagnóstico comprobado sobre todo el snapshot, no sobre las doce muestras.** `UrlNormalizer.normalize()` resolvía enlaces y normalizaba la query, pero dejaba espacios literales en el path. El descubrimiento de comentarios heredaba esa URL y calculaba su identidad con espacios. `ResourceProjectionPolicy.classify()` rechazaba cualquier whitespace antes de evaluar el formato. Las **423/423** decisiones `invalid_download_url` corresponden a `.pdf`, método `commented_html_link`, host `www.bbv.com.bo` y espacios ASCII exclusivamente en el path. Otros componentes con whitespace: **0**; otros tipos de whitespace: **0**; de esas 423, siguen rechazadas tras la reparación: **0**. No era fallo de descarga HTTP: el histórico no comprobó esos archivos y su `http_status` es null.

Además se descubrió una referencia antes aceptada con `.../Fichas/..\AGB_BIA_CAR.PDF`, clave `e764cdfcc6a565c5fdf7e13d6cb2141acfdd8d1bb2c9b7640ce790c6ea05f656`. Ahora se excluye por `invalid_download_url`: no se adivina una ruta alternativa ni se reemplaza el backslash por slash.

**Corrección de URL.** La reparación valida una URL HTTP(S) absoluta y codifica el path en UTF-8: espacios → `%20`. Conserva escapes existentes, `%2F`, `%25`, query literal, parámetros repetidos, orden, firma y fragmento durante esa reparación. No decodifica y vuelve a codificar el path ni transforma `+` en espacio. Rechaza controles literales/escapados, backslashes, porcentajes malformados, credenciales, autoridad/puerto inválidos, whitespace fuera del path, whitespace ambiguo y traversal. Las plantillas OpenAPI `{id}` se conservan como documentación pendiente de parámetros, sin convertirlas en URLs concretas.

El normalizador canónico mantiene su política previa de query (ordenación y eliminación de tracking); **no debe confundirse esa identidad con una URL original firmada**. El exportador legacy conserva la query original cuando `raw_url` es absoluta, segura y corresponde al mismo esquema/host/puerto efectivo/path de la representación. También puede codificar su path sin tocar esa query. Una `raw_url` relativa se conserva como procedencia y se usa la URL absoluta resuelta para exportar. La reparación no amplía hosts ni el scope configurado. Las claves históricas, `resources_hash`, `raw_url`, origen y método permanecen intactos; para nuevo discovery, las formas con espacios y `%20` producen la misma clave dentro de cada fuente. La reproyección no presenta colisiones de URL canónica.

**APIs.** El dato «166 APIs» corresponde a `format=api`; había otras **34** con `format=json`. Por `resource_type=api`, BBV tenía **200** referencias: **68 oEmbed** (34 api + 34 json), **95** `api/cargaArchivos/descargarprospecto` y **37** `api/cargaArchivos/descargarinformecalificacion`. Se mantienen las **132** referencias de descarga, incluidas las que no tienen extensión; se excluyen las 68 oEmbed. FINRURAL aporta otras **44** oEmbed, **22** endpoints de páginas CMS, **1** de usuario CMS y **1** índice `/wp-json/` que también se excluyen de la selección técnica.

La exclusión usa rutas estándar concretas de infraestructura, no búsquedas por nombre genérico, extensión o bloqueo de todo WordPress. Se conservan en discovery/RAW todas las referencias excluidas y su decisión auditable. Los endpoints personalizados WordPress y de medios siguen siendo candidatos sujetos a la política pública GET existente. Referencias de semántica: [oEmbed y previews](https://developer.wordpress.org/reference/functions/get_oembed_response_data/), [índice REST WordPress](https://developer.wordpress.org/rest-api/key-concepts/), [páginas CMS](https://developer.wordpress.org/rest-api/reference/pages/). Las referencias de descarga preservadas tienen evidencia de enlace público y metadata GET sin autenticación; `pages_sampled=0` y `http_status=null`. **No se verificó remotamente su disponibilidad ni el contenido de los 423 PDF: selección significa referencia admitida por política, no descarga certificada.**

| Fuente | RAW antes/después | Seleccionados antes | Seleccionados corregidos | Añadidos | Retirados de selección | Registros legacy conservados idénticos |
|---|---:|---:|---:|---:|---:|---:|
| BBV | 1.000 / 1.000 | 577 | 931 | 423 PDF | 68 oEmbed + 1 URL ambigua | 508 |
| FINRURAL | 203 / 203 | 203 | 135 | 0 | 44 oEmbed + 23 CMS + 1 índice | 135 |
| Total | 1.203 / 1.203 | 780 | 1.066 | 423 | 137 | 643 |

Se retirarían **137 de los 780 registros anteriores**, conservando **643 exactamente iguales** y agregando **423** referencias. Son **560 cambios de pertenencia**, saldo **+286**. Ninguna representación retenida cambia de campos. La comparación se hace también sobre los seis campos de cada registro legacy, sin confundir cambios de posición en el árbol con cambios de recurso. BBV mantiene el estado histórico `PARTIAL_SUCCESS`; la reparación no prueba exhaustividad ni completa la frontera pendiente.

**Contrato para frontend, compatible y de solo lectura.**

- `GET /catalog/resources?source_id=bbv` sigue devolviendo el array RAW de **1.000** recursos. Mismos campos `ResourceCandidate`, filtros y paginación opcional `limit=1..500`, `offset>=0`; filtrar antes de paginar, orden estable al paginar. RAW es el catálogo consolidado; puede diferir del último snapshot tras otras rondas parciales.
- **Nuevo:** `GET /catalog/sources/bbv/projection` devuelve el `DataxProjection` existente (`schema_version=datax-projection-1.0`), calculado sobre el último snapshot: `run_id`, `resources_hash`, `total_raw_resources`, `total_selected_resources`, `total_families`, `families` y `decisions`. Los seleccionados están en `families[].periods[].representations[]`; las decisiones contienen `resource_key`, `selected` y `reason_codes`, incluidas las exclusiones. `url` es la referencia absoluta codificada; `raw_url` y `origin_url` permiten seguir la procedencia. La URL canónica puede tener query normalizada; para consumir URLs firmadas originales se debe respetar la query de la `raw_url` absoluta segura, como hace el exportador legacy.
- La proyección completa no lleva paginación. Es selección técnica con agrupamiento por defecto; no aplica contratos opcionales de agrupamiento de una exportación específica ni reproduce un ZIP histórico. No descarga, persiste, migra ni cambia salud/cadencia. Fuente inexistente o sin snapshot: **404**. JSON y Mongo usan los mismos puertos/servicio; Mongo fue validado con fake, sin certificar infraestructura real.
- Se mantienen los contratos legacy `ESTADISTICAS` y los ocho paths REST anteriores. No hay `UnifiedSearchDocument`, endpoint de búsqueda inventado ni cambios del Bridge, frontend o Prospector Interno. Una API descubierta es una referencia; `api.callable_by_policy` no afirma que se haya hecho una solicitud exitosa.

Ejemplos de lectura, con la API reiniciada usando este código y el mismo `--catalog-dir`:

```powershell
$api = 'http://127.0.0.1:8000'
$raw = Invoke-RestMethod "$api/catalog/resources?source_id=bbv&limit=100&offset=0"
$projection = Invoke-RestMethod "$api/catalog/sources/bbv/projection"
$projection | Select-Object run_id, total_raw_resources, total_selected_resources
$selected = @($projection.families | ForEach-Object { $_.periods } | ForEach-Object { $_.representations })
# Esperado en este snapshot: 1.000 RAW y 931 seleccionados; el ZIP histórico conserva 577 BBV.
```

**QA de esta corrección: 173 pruebas específicas aprobadas; regresión global 745 PASS, 1 SKIP, 2 avisos, 169,01 s; cobertura 12.249/15.237 = 80,39 % sobre src/ + apps/, gate ≥80 % aprobado.** El skip corresponde a Chromium real opt-in; los dos avisos son deprecaciones de TestClient/anyio. Se corrigió la regresión inicial de plantillas OpenAPI y se repitió la suite con el código definitivo. La consulta nueva se validó con JSON y Mongo fake; no se certifica un daemon Mongo real.

**Evidencia reproducible:** [audit/20261023_resource_quality.json](audit/20261023_resource_quality.json). Salida aislada: `output/resource-quality/carga-20261023-171007/reprojection-verified/`, con `comparison.json`, clasificación de las 268 referencias API, cambios individuales por clave, proyección/legacy/manifiesto nuevos por fuente y OpenAPI posterior. El baseline previo y los logs de QA están en la carpeta padre. Se compararon los **8 paths y 17 schemas REST anteriores**, sin diferencias; se recorrieron todas las páginas RAW de ambas fuentes y se contrastó la respuesta HTTP ASGI con la proyección offline. Los **50 archivos históricos** protegidos mantienen exactamente sus SHA-256; el ZIP original conserva `8ff69dacabcce41c8f24381aa95617d12ff9acddc546e383d2578f0e32fad8af` y pasa nuevamente la comprobación de integridad.

Para repetir exclusivamente la comparación, usar otra carpeta de salida que todavía no exista:

```powershell
.\.venv\Scripts\python.exe -B ops/reproject_resource_quality.py --run-id carga-20261023-171007 --destination output/resource-quality/carga-20261023-171007/reprojection-repeat --baseline output/resource-quality/carga-20261023-171007/historical_sha256_before.json --openapi-baseline output/resource-quality/carga-20261023-171007/openapi_before.json
```

Archivos de implementación: `domain/normalizer.py`, `domain/api_discovery.py`, `domain/projection.py`, `application/legacy_stats_exporter.py`, `adapters/api/routes.py`, todos bajo `src/prospector_externo/`. Regresiones: `tests/test_resource_quality.py` y actualización de la expectativa de espacios en `tests/test_batch12d_url_safety.py`. Herramienta offline: `ops/reproject_resource_quality.py`. README y este handoff documentan el contrato nuevo. Las validaciones y sus cifras definitivas se registran en la evidencia técnica citada.

## Antecedente operativo preservado

**Cierre operativo del 23/10/2026, America/La_Paz.** Este es el único handoff vigente. Continúa el cierre A–F del 22/10; sus cifras quedan como antecedentes, no como resultados nuevos. Requisitos oficiales y arquitectura preservados. **Entrega funcional verificada en JSON; NO CERTIFICADO PARA PRODUCCIÓN.**

Se implementaron en las entradas existentes: B13 --mode initial/incremental, --source-ids, plan de elegibilidad sin efectos, request durable para resume, índice de entregas verificadas, conservación de intentos/paquetes, resumen y telemetría muestreada por adquisición. El incremental reutiliza SourceStatusGate; no añade --force, no cambia Source/snapshots de omitidas y persiste reportes/observaciones SKIPPED consultables por REST. La API incorpora CLI explícita para el mismo catálogo JSON o base Mongo.

**QA del antecedente: 696 PASS, 1 SKIP, 2 avisos en 126,80 s; 12.174/15.169 = 80,26 % global src/ + apps/, sin exclusiones nuevas.** Chromium real aparte: 1 PASS / 57,15 s, 30 páginas y 350,5 MiB RSS máximo muestreado. Baseline anterior: 687 PASS / 80,34 %. Estos resultados preceden la corrección focalizada documentada arriba.

**Verificación real:** CLI con HTTP loopback y dos físicas (alpha HTML y bravo comentarios), un alias, una retenida y una suspendida; carga parcial → resume completo → incremental con cero adquisiciones → resume idempotente. Se generaron archivos y se verificaron manifiestos, checksums y ZIP. Uvicorn leyó ese mismo catálogo con 200/404/422/405/503. BBV pública, por el mismo B13: 49→73 RAW/DATAX, cuatro requests por intento, PARTIAL_SUCCESS; API HTTP devolvió los 73 recursos, Swagger 200, arranque local 2,005 s. No hubo rastreo masivo, stage/commit/push, borrado histórico ni scheduler activado.

Evidencia actual: [audit/20261023_operational_closure.json](audit/20261023_operational_closure.json). Se preservan [cierre autónomo anterior](audit/20261022_autonomous_execution.json), [comparación FINRURAL](audit/20261022_continuation_measurements.json) y [manifiesto de retiro](audit/20261022_retirement_manifest.json). HU funcionales: **12 PASS / 7 PARTIAL / 2 EXTERNAL_BLOCKER / 1 FAIL**, con sus límites más abajo. Mongo real y Docker siguen bloqueados; fakes no los certifican.

## INICIO RÁPIDO: PRIMERA CARGA / ACTUALIZACIÓN / API

Desde la raíz en Windows PowerShell, con la .venv existente. Cada operación de crawling es **un comando B13** que encadena adquisición, RAW, catálogo, proyección, legacy, reporte y ZIP. B13 escribe JSON; --sync-mongo añade sincronización sin recrawl, previo ping. No requiere ejecutar B9B ni proyección manualmente.

Los ejemplos seleccionan **solo FINRURAL y BBV** y mantienen los presupuestos operativos del YAML. Se validó el flujo con dos físicas de fixture y BBV real acotada; **no se ejecutó el rastreo masivo ni se certifica exhaustividad de esos portales**. No omitir --source-ids sin revisar alcance: omitirlo considera las 45 operativas del plan, incluida FDTA retenida.

**Primera carga** (ID nuevo; initial conserva el comportamiento forzado histórico para las fuentes seleccionadas):

~~~powershell
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main run --mode initial --run-id carga-20261023-01 --source-ids finrural,bbv --catalog-dir .\output\catalog
~~~

**Actualización periódica** (ID nuevo por ronda, mismo catálogo y selección; sin --force):

~~~powershell
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main run --mode incremental --run-id actualizacion-20261023-01 --source-ids finrural,bbv --catalog-dir .\output\catalog
~~~

**Reanudar** (mismo ID; recupera modo/selección del request persistido; puede repetirse sobre una ronda incremental):

~~~powershell
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main run --run-id carga-20261023-01 --resume --catalog-dir .\output\catalog
~~~

**API** (proceso independiente persistente; mismo catálogo; detener con Ctrl+C):

~~~powershell
.\.venv\Scripts\python.exe -m apps.catalog_api.main --backend json --catalog-dir .\output\catalog --host 127.0.0.1 --port 8000
~~~

Swagger: **http://127.0.0.1:8000/docs**. OpenAPI: **http://127.0.0.1:8000/openapi.json**. --backend json prevalece sobre PROSPECTOR_BACKEND=mongo: ese caso se ensayó con Mongo inaccesible. La API rechaza un --catalog-dir explícito sin state/sources.json, evitando servir vacío por un error de ruta. Ejecutar primera carga antes de ese arranque.

**Diagnóstico/plan**, sin solicitudes a portales ni creación de estado/directorios:

~~~powershell
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main plan --mode incremental --source-ids finrural,bbv --catalog-dir .\output\catalog --json
~~~

Muestra inventario 52/45/7, 39 configuraciones físicas, selección lógica/física, elegibles, hold FDTA, salud y motivos NOT_DUE/SUSPENDED. El diagnóstico es una fotografía: no garantiza que un portal responderá. Las fuentes nuevas son elegibles; las no elegibles no se convierten en SUCCESS ni NO_CHANGE.

IDs de ejemplo: usar otros si ya existen. Sin --resume un ID existente se rechaza. Resume rechaza cambiar modo/selección/catálogo; los fingerprints siguen protegiendo cambios de perfil y plan. --max-sources limita intentos del lote sin cambiar su selección: puede dejar PENDING hasta otro resume. --project-dir permite una raíz de configuración/evidencia aislada; no crea otro runner. API/custom reinician desde seeds; HTML/comentarios/JavaScript conservan cursor durable.

## Qué ocurre en cada modo y qué se conserva

- **initial:** fuerza elegibilidad solo de la selección explícita, conservando compatibilidad histórica B13. FDTA sigue retenida y las siete status-only no se adquieren. No usar este modo en un scheduler periódico.
- **incremental:** evalúa salud/cadencia antes de lanzar adquisiciones. Distingue NOT_DUE y SUSPENDED; cero requests de las omitidas. Registra un Run/RunReport con observaciones SKIPPED/NOT_EVALUATED por física, sin aplicar de nuevo salud/cadencia ni sumar ausencias.
- **Entregas omitidas:** state/deliveries.json enlaza la última entrega por lógica. Solo reutiliza artefactos con fuente física, crawl_run_id vigente, fingerprint y SHA-256 compatibles. Se copian al paquete nuevo con execution_status=SKIPPED, artifact_reused=true y run original; no se simula una nueva adquisición. Sin entrega verificable, el manifiesto declara SKIPPED/NO_VERIFIED_PREVIOUS_DELIVERY, sin inventar un archivo. Un cambio de perfil no permite reutilizar una proyección vieja.
- **Fallos/vacíos:** una fuente fallida tiene EXECUTION_FAILED y queda sin artefacto; las otras permanecen en catálogo y pueden entregarse en un ZIP con failed_sources explícito. El comando devuelve 2. SUCCESS vacío produce NO_RESOURCES y manifiesto sin legacy ficticio. Errores de proyección o snapshot incoherente siguen impidiendo certificar esa entrega.
- **Resume:** solo retoma PENDING/PARTIAL/FAILED dentro de la selección; conserva completadas y omisiones ya registradas. Cada intento guarda copia del checkpoint previo, batch_report y state.json en attempts/. El estado activo se actualiza, los snapshots históricos y paquetes anteriores se conservan. El item en vuelo es at-least-once.
- **Salidas:** código 0 sin errores/parcialidad/pendientes; 3 con parcialidad/pendientes; 2 con fallo. READY significa paquete consumible y puede coexistir con PARTIAL_SUCCESS. Consultar coverage_partial_sources, failed_sources y cada fuente. Una excepción previa de configuración, perfil, IO o preflight puede terminar con código 1; tampoco significa éxito. Argumentos CLI inválidos devuelven 2.

B13 permanece secuencial. Mantener **un solo escritor por catálogo**; no se habilitó coordinación distribuida ni paralelismo de subprocesses. El batch directo conserva bulkhead hasta 4 fuentes/2 navegadores; no atribuir esa concurrencia a B13.

El resumen imprime selección, física, estado, páginas, pendientes/fallos, RAW/legacy, requests_this_attempt, stop/skip_reason, evidencia y rutas exactas de state/manifest/ZIP. Los requests del snapshot previo son históricos; requests_this_attempt es 0 al omitir/reusar adquisición. La telemetría del árbol del crawler se muestrea cada ~100 ms: wall_seconds, cpu_seconds_sampled, rss_peak_sampled_bytes, samples. **NO_MEDIDO/null** cuando no hay muestra; no es un techo absoluto ni el CPU total exacto. Timings B13 separan crawl, proyección, paquete y sync; sync no ejecutada es null, no cero ficticio.

## Outputs y resultados generados

| Artefacto | Ruta de operación normal |
|---|---|
| Catálogo para API JSON | output/catalog/state/sources.json, resource_catalog/, snapshots/, observations/, runs/; reportes en output/catalog/reports/ |
| RAW y evidencia de fuente lógica | output/checkpointed/<pipeline-id>/<logical-id>/state/ y reports/ |
| Snapshot | state/snapshots/<physical-id>_<crawl-run-id>.json, tanto en catálogo como en evidencia aislada |
| Mapas compatibles | output/checkpointed/<pipeline-id>/<logical-id>/<physical-id>/mapa_*.json |
| DATAX/legacy/manifest de exportación | output/checkpointed/<pipeline-id>/<logical-id>/<physical-id>/downstream/{datax_projection,legacy_estadisticas,export_manifest}.json |
| Paquete inicial | output/b13-pipeline/<pipeline-id>/datax-package/latest/manifest.json y datax-package/datax_package.zip |
| Paquetes siguientes | datax-package/revisions/<UTC-uuid>/manifest.json y datax_package_<UTC-uuid>.zip; consultar state.package |
| Request, checkpoint, resumen y log | .runtime/b13_pipeline/<pipeline-id>/{request.json,checkpoint.json,state.json,checkpointed_batch.log} |
| Historial de intentos | .runtime/b13_pipeline/<pipeline-id>/attempts/<attempt-id>/{checkpoint_before.json,batch_report.json,state.json} |
| Telemetría de fuente | output/checkpointed/<pipeline-id>/<logical-id>/telemetry/<uuid>.json |

Los JSON downstream son la vista activa del snapshot de esa fuente; cada ZIP conserva su propia copia verificable. El catálogo reconciliado puede conservar recursos anteriores frente a un parcial; la proyección del snapshot no certifica cobertura completa ni debe interpretarse como listado de eliminaciones.

**BBV real ya disponible**, sin repetir crawling:

~~~powershell
.\.venv\Scripts\python.exe -m apps.catalog_api.main --backend json --catalog-dir "$env:TEMP/datax-operational-public-host-i0al94p6/catalog" --host 127.0.0.1 --port 8000
~~~

Este arranque apunta al catálogo de ensayo que contiene 73 recursos BBV. No es output/catalog del quickstart: aquella ruta estable se poblará al ejecutar su primera carga. Los ensayos de infraestructura o canarios usan TEMP y no modificaron los históricos del checkout.

Raíces reales de evidencia:

- BBV: **$env:TEMP\datax-operational-public-host-i0al94p6**. Lote bbv_canary; snapshots por intento, catálogo catalog/, logs console_1.log/console_2.log y .runtime/b13_pipeline/bbv_canary/.
- Fixture automatizado: **$env:TEMP\datax-operational-verified-tests\test_production_cli_initial_re0**. Catálogo catalog/; IDs initial e incremental; operational_evidence.json contiene respuestas HTTP y estados. alpha/bravo son sintéticos, nunca producción.
- Paquete BBV inicial: **$env:TEMP\datax-operational-public-host-i0al94p6\output\b13-pipeline\bbv_canary\datax-package\datax_package.zip**, 49 registros; SHA-256 **ef1b68aa28a548bb2e387c3ebcc8848a09825ab7cf932b572a97da4a77cdc2a4**.
- Paquete BBV reanudado: **$env:TEMP\datax-operational-public-host-i0al94p6\output\b13-pipeline\bbv_canary\datax-package\datax_package_20261023T041938_4db122a8a26a.zip**, 73 registros; SHA-256 **c92716680e0329e1fa74632efd3d06bc562db658827758b99ec709c2833885ba**. Primer ZIP y checksums conservados.

| BBV / intento | Pared CLI | Crawl supervisado | CPU muestreada | RSS máximo muestreado | Requests | RAW/DATAX | Pendientes | Estado |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 11.056 s | 9.971 s | 1.312 s | 66,363,392 B | 4 | 49/49 | 24 | PARTIAL_SUCCESS / RC3 |
| 2 | 21.681 s | 20.409 s | 1.203 s | 70,062,080 B | 4 | 73/73 | 23 | PARTIAL_SUCCESS / RC3 |

Ambos respetaron TLS, robots y rate_limit=1 s; tres páginas por intento, cero fallos URL. No se demuestra aceleración ni exhaustividad. Antes hubo un intento dentro del sandbox con ROBOTS_UNREACHABLE y un resume externo rechazado por ACL del TEMP de sandbox: ambos quedaron preservados. El canario efectivo se ejecutó en una carpeta NUEVA del anfitrión, sin relajar controles.

## API REST y guía para el compañero

La URL base del quickstart es **http://127.0.0.1:8000**. Swagger permite expandir operación → Try it out → Execute. Las listas son arrays, sin total/items. `source_id` es la identidad física del catálogo: no inventar una entrada por cada alias lógico. `resource_key` identifica la referencia dentro de esa física; hash de URL no significa hash de contenido.

En PowerShell asignar directamente Invoke-RestMethod a una variable: envolver la llamada con @(...), en PowerShell 5, puede producir un array anidado y convertir varios run_id en una sola URL inválida. Los siguientes ejemplos se verificaron con HTTP real:

~~~powershell
$base = 'http://127.0.0.1:8000'
Invoke-RestMethod "$base/health"
$sources = Invoke-RestMethod "$base/catalog/sources?limit=20&offset=0"
Invoke-RestMethod "$base/catalog/sources/bbv"
$resources = Invoke-RestMethod "$base/catalog/resources?source_id=bbv&limit=20&offset=0"
$reports = Invoke-RestMethod "$base/runs?limit=10"
$crawlRunId = ($reports | Select-Object -First 1).run_id
Invoke-RestMethod "$base/runs/$crawlRunId"
Invoke-RestMethod "$base/runs/$crawlRunId/report"
Invoke-RestMethod "$base/sources/bbv/observations?limit=10"
$body = @{url='https://www.bbv.com.bo/wp-content/uploads/2026/10/Tarifario_vigente.pdf'} | ConvertTo-Json
Invoke-RestMethod -Method Post "$base/mapping/inspect" -ContentType 'application/json' -Body $body
(Invoke-WebRequest -UseBasicParsing "$base/docs").StatusCode
Invoke-RestMethod "$base/openapi.json"
~~~

| Endpoint | Contrato y ejemplo observable | Errores/control probado |
|---|---|---|
| GET /health | {status:UP, service:catalog-api-facade} | 503 con catálogo corrupto **aislado**; no expone rutas/URI |
| GET /catalog/sources?limit=20&offset=0 | Source[]; orden source_id al paginar; limit 1–500, offset ≥0 | 422 límites inválidos |
| GET /catalog/sources/bbv | Source; workflow=commented_html, salud ACTIVE y última corrida real | 404 desconocida |
| GET /catalog/resources?source_id=bbv&limit=20&offset=0 | ResourceCandidate[]; filtro antes de paginar, orden source_id/resource_key/url | 422; sin resultados devuelve [] |
| GET /runs?limit=10 | RunReport[]; limit 1–100, default20; source_results con execution_status/coverage | 422 |
| GET /runs/<crawl-run-id> | Run; pipeline_run_id separado; disponible durante RUNNING | 404 |
| GET /runs/<crawl-run-id>/report | RunReport; métricas y observaciones de ese intento | 404 si aún no finalizó |
| GET /sources/bbv/observations?limit=10 | SourceRunObservation[]; limit 1–50; incluye parciales y SKIPPED | 422; [] si sin historia |
| POST /mapping/inspect | Normalización, extensión, url_sha256, hash_basis=normalized_url e inspection_mode=offline_url_only | 422; no consulta/descarga el recurso |
| GET /docs y /openapi.json | Swagger y esquema real | 200 probado en fixture y catálogo BBV |
| POST/PUT/DELETE catálogo | No hay escritura REST | 405; export-index permanece 404 por contrato no definido |

**JSON obtenido de BBV**, abreviado solo para lectura (campos/tipos reales, no un esquema Bridge):

~~~json
{
  "source_id": "bbv",
  "resource_key": "0291d871e03e7be00a8ad50d4d578b1dc804f0aac201836f0439cb04fbe5ceab",
  "url": "https://www.bbv.com.bo/wp-content/uploads/2025/07/EEFF-BBV.pdf",
  "file_extension": ".pdf",
  "discovered_from_url": "https://www.bbv.com.bo/acerca-de-la-bolsa/informacion-financiera-2024/",
  "discovery_method": "commented_html_link",
  "change_status": "NEW",
  "discovered_at": "2026-10-23T04:19:23.076403Z",
  "http_status": null
}
~~~

`http_status=null` indica que se descubrió la referencia sin certificar una descarga del PDF. El detalle BBV devolvió `last_run_id=run_20261023_041919_b3dc76`, `catalog_last_run_id` igual y `next_eligible_at=2026-10-23T08:19:37.873474Z`. pipeline_run_id es **bbv_canary**; no usarlo en /runs/{id}.

El reporte de esa corrida devuelve execution_status=PARTIAL_SUCCESS, failure_code=REQUEST_BUDGET_REACHED y coverage con pages_visited=3, requests_total=4, urls_pending=23, urls_failed=0. Un READY B13 no cambia ese resultado. POST mapping devolvió:

~~~json
{
  "normalized_url": "https://www.bbv.com.bo/wp-content/uploads/2026/10/Tarifario_vigente.pdf",
  "file_extension": ".pdf",
  "resource_type": "file",
  "url_sha256": "1bbbfa6a2cdf420a74ce5bf4209fb3f6b4f1846593cc996ceb19e29b5f4611f2",
  "hash_basis": "normalized_url",
  "inspection_mode": "offline_url_only"
}
~~~

### Checklist para el compañero

1. Configurar base URL y comprobar health/docs/OpenAPI del **mismo catálogo**. El ejemplo BBV ya generado usa TEMP; los comandos de operación estable usan output/catalog. No mezclar con alpha/bravo.
2. Leer Source[] y ResourceCandidate[] paginados; conservar source_id/resource_key/URL/procedencia. Parar páginas cuando tengan menos de limit; no hay total/cursor estable frente a escrituras simultáneas. Mongo aplica filtro/orden/skip/limit en servidor; JSON en memoria; legacy conserva fallback compatible.
3. Obtener RunReport[] y usar run_id de crawl para detalle/reporte; mostrar execution_status, coverage, skip_reason y stop_reason. SKIPPED no es NO_CHANGE. Para una fuente concreta, obtener su run_id de /sources/{id}/observations, porque /runs lista corridas globales. No confundir número de recursos conservados con recursos observados en el intento actual.
4. Catálogo REST excluye REMOVED. Las observaciones/snapshots contienen cambios confirmados; no deducir remoción por faltar un elemento de una página. Tres ausencias exitosas comparables, nunca parcial/fallo/resume; reaparición resetea.
5. Consumir legacy/export_manifest o el paquete indicado por state.package. Verificar manifest y checksums; respetar failed_sources, cobertura parcial, NO_RESOURCES y acquisition_jobs TRANSTATS. No descargar binarios como parte de este módulo.
6. Para frontend de otro origen, acordar proxy/CORS y origen permitido; no se habilitó wildcard ni autenticación/HTTPS productiva. Manejar 404/422/405/503 y errores de red sin convertirlos en catálogo vacío válido.
7. Bridge: consumir hoy /catalog/resources y observaciones. UnifiedSearchDocument/export-index requiere contrato del dueño (identidad/campos/versionado/cambios/removidos/cursores/errores); no se implementó esquema supuesto ni el Interno.

### Paso a Mongo cuando esté disponible

B13 mantiene JSON como evidencia y autoridad de selección/cadencia; --sync-mongo publica sus hechos validados sin nuevo rastreo. API Mongo debe usar la **misma URI/base** del espejo; no editar ambos backends independientemente. Si falla ping, B13 aborta antes de consultar portales. Si falla sync, conserva JSON/paquete y devuelve 2; reanudar permite replay sin recrawl de SUCCEEDED.

~~~powershell
.\.venv\Scripts\python.exe -m apps.b13_pipeline.main run --mode incremental --run-id mongo-20261023-01 --source-ids finrural,bbv --catalog-dir .\output\catalog --sync-mongo --mongo-uri mongodb://127.0.0.1:27018 --mongo-database prospector_externo_dev
.\.venv\Scripts\python.exe -m apps.catalog_api.main --backend mongo --mongo-uri mongodb://127.0.0.1:27018 --mongo-database prospector_externo_dev --host 127.0.0.1 --port 8000
~~~

El espejo incluye reportes/observaciones de omisiones; su contrato se probó con un puerto de persistencia simulado, **no Mongo real**. Una base nueva debe recibir antes la primera carga/sync de adquisiciones: una ronda totalmente omitida no inventa snapshots ausentes en un espejo vacío. Para importar JSON existente sin recrawl está apps.catalog_sync.main --evidence-dir <ruta> --check y luego --commit --mongo-uri <URI> --mongo-database <base aislada>.

Docker real sigue inaccesible (consulta fuera del sandbox incluida); no se construyeron imágenes ni certificó <5s de contenedor. Uvicorn nativo ~2s no sustituye esa prueba. DevOps debe habilitar Linux Engine y un proyecto/volumen/puertos/base de ensayo nuevos, validar siete colecciones/índices/replay/cortes y stop/up sin borrar volúmenes. No activar scheduler ni modificar ExecutionPolicy; el wrapper histórico sigue siendo opt-in y no coordina múltiples hosts.

### Validación de infraestructura — solo DevOps, bloqueada en este entorno

Comandos preparados, **no ejecutados** contra un motor operativo. Desde la raíz, usar proyecto/volumen NUEVOS y puertos distintos; la API del override apunta a la misma base prospector_externo_dev que el sync. Si Compose no reconoce !override, detenerse y revisar su versión; no sustituirlo por un mapeo que colisione con servicios existentes.

```powershell
$env:PROSPECTOR_MONGO_HOST_PORT = '27128'
$composeProject = 'datax-validacion-' + (Get-Date -Format 'yyyyMMddHHmmss')
$composeOverride = Join-Path $env:TEMP ($composeProject + '.yaml')
@'
services:
  api:
    ports: !override
      - "127.0.0.1:8018:8000"
    environment:
      PROSPECTOR_MONGO_DATABASE: prospector_externo_dev
'@ | Set-Content -Encoding utf8 $composeOverride
function dc { docker compose -p $composeProject -f docker-compose.yml -f $composeOverride @args }
docker info
dc config --quiet
dc build api batch
dc up -d --wait mongo
$watch = [Diagnostics.Stopwatch]::StartNew()
dc up -d --no-deps api
$healthy = $false
while ($watch.Elapsed.TotalSeconds -lt 30) {
  try { if ((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8018/health).StatusCode -eq 200) { $healthy = $true; break } } catch { }
  Start-Sleep -Milliseconds 100
}
$watch.Stop(); @{healthy=$healthy; seconds=$watch.Elapsed.TotalSeconds}
dc ps
dc logs --tail 80 api mongo
dc run --rm batch --help
# Importación de la evidencia BBV existente: no hace crawling.
$existingEvidence = "$env:TEMP/datax-operational-public-host-i0al94p6/output/checkpointed/bbv_canary/bbv"
.\.venv\Scripts\python.exe -m apps.catalog_sync.main --evidence-dir $existingEvidence --check
.\.venv\Scripts\python.exe -m apps.catalog_sync.main --evidence-dir $existingEvidence --commit --mongo-uri mongodb://127.0.0.1:27128 --mongo-database prospector_externo_dev
# Repetir el MISMO commit para replay; comparar conteos/IDs, no crear otra base.
Invoke-RestMethod 'http://127.0.0.1:8018/catalog/resources?source_id=bbv&limit=2&offset=0'
# Detener/reiniciar sin borrar volumen; los mismos documentos deben seguir presentes.
dc stop
dc up -d --wait mongo api
```

Ver las siete colecciones, índices únicos/secundarios y timestamps después del primer commit y del replay. Este bloque es solo lectura sobre el proyecto aislado:

```powershell
@'
for (const n of ["sources","runs","discovered_urls","resource_candidates","snapshots","source_observations","run_reports"]) {
  print(n+" count="+db[n].countDocuments({})); printjson(db[n].getIndexes());
}
printjson(db.discovered_urls.find({}, {source_id:1,normalized_url:1,discovered_at:1,last_seen_at:1}).limit(3).toArray());
'@ | docker compose -p $composeProject -f docker-compose.yml -f $composeOverride exec -T mongo mongosh prospector_externo_dev --quiet
```

Esperado: sin duplicados tras replay; last_seen_at corresponde al intento importado, no a la hora del replay. El arranque medido excluye build y espera previa de Mongo; <5s sigue siendo criterio pendiente. Faltan corte real entre escrituras, prueba del batch con adquisición dentro de contenedor y recuperación Mongo; no se certifican por --help ni por stop/up. No usar down -v, nombres de proyectos existentes o bases productivas.

## Gate final operativo

Comandos A/B/C/D/Plan son exactamente los del quickstart; E2E = tests/test_operational_single_command_e2e.py, ejecutado por el agente con procesos reales. Las rutas completas reproducibles están en el JSON actual y se muestran arriba.

| Gate | Estado | Comando y resultado real | Evidencia / qué falta |
|---|---|---|---|
| 1. Primera carga | **VERIFICADO acotado** | B13 run --mode initial --source-ids explícitos; fixture dos físicas, BBV real 49 registros/paquete con RC3 por parcialidad | fixture/initial y público/bbv_canary; no global ni portal completo |
| 2. Incremental sin force | **VERIFICADO** | B13 run --mode incremental; E2E cero adquisiciones/requests nuevos, cuatro lógicas omitidas, Source/snapshots intactos y reportes SKIPPED | fixture/.runtime/b13_pipeline/incremental/; fuente due también ensayada; sin scheduler activado |
| 3. Resume | **VERIFICADO** | Mismo ID/request/catálogo; fixture parcial→completo, incremental repetido sin red; BBV 49→73 | attempts/ y revisiones preservadas; incompatibilidad rechazada antes de red |
| 4. Outputs | **VERIFICADO** | RAW→mapas→DATAX→legacy→export_manifest→manifest/checksums→ZIP; lectura como consumidor y SHA-256/CRC correctos | JSON actual, ZIP inicial 49 y revisión 73; fallos/vacíos explícitos sin archivos ficticios |
| 5. API mismo catálogo | **VERIFICADO HTTP real** | CLI API JSON; health/docs/OpenAPI/catálogo/runs/reportes/observaciones/inspect 200, 404/422/405; fixture 503 aislado | público 73 recursos, startup 2,005s; fixture dos fuentes; procesos cerrados |
| 6. Mongo real | **BLOQUEADO** | Ping/puertos no disponibles; contrato de espejo/índices/replay con fakes solamente | DevOps: servidor/base aislada, siete colecciones e índices reales, cortes/replay |
| 7. Docker batch/API | **BLOQUEADO** | docker info / compose ps fallan por pipe Linux inexistente, incluso fuera del sandbox | Build/volumen/arranque API <5s no ejecutados |
| 8. QA/browser | **VERIFICADO** | pytest global: 696 PASS/1 SKIP/2 warnings, 80,26%; Chromium opt-in aparte 1 PASS/30 páginas | $env:TEMP\datax-operational-verified-coverage.json; browser_30_pages.json; 350,5MiB muestreado, no techo absoluto |
| 9. Canarios/fuentes | **PARCIAL** | Una fuente pública BBV, dos intentos de cuatro requests; 49→73/pendientes24→23 | TLS/robots/rate activos; MEFP sigue hold según diagnóstico previo, no revalidada; otras fuentes no recorridas |
| 10. Git/compañero | **VERIFICADO en desarrollo** | Baseline separado, diff/checksums, misma rama/HEAD, sin commits/push/borrados; guía REST y cinco operaciones exactas | Aceptación integrador, decisiones de arquitectura y producción siguen externas |

## Aceptación manual final opcional — solo cinco comandos

El agente ya ejecutó la regresión y los E2E; **no se exige repetir M01–M26**. Para una aceptación propia usar las cinco operaciones del quickstart: **Plan → primera carga → incremental → API → consulta**. Resume está disponible si el comando devuelve 3 y se desea continuar el mismo perfil. No ejecutar primera carga otra vez con un ID existente.

Quinto comando, después de iniciar API en otra terminal:

~~~powershell
Invoke-RestMethod 'http://127.0.0.1:8000/catalog/resources?source_id=bbv&limit=2&offset=0'
~~~

Para revisar resultados ya generados sin más crawling, basta el arranque API BBV de TEMP y esa consulta. Las pruebas Docker/Mongo bloqueadas quedan con DevOps, no como rutina exigida al operador.

## QA, cambios propios y preservación Git

Suite final: **696 passed, 1 skipped, 2 warnings in 126.80s**, gate --cov-fail-under=80 aprobado. Líneas: **12.174/15.169 = 80,255785 % (80,26 %)**; no ramas/exclusiones nuevas, mismo perímetro src/ + apps/. Chromium: **1 passed in 57.15s**, workflow56,143s, dos aperturas, máximo un contexto/cero al terminar, RSS muestreado367.529.984B. El SKIP de la suite es ese opt-in, ejecutado aparte.

La prueba E2E comprueba casos de request incompatible, no reuso de artefacto alterado, refresco de una fuente due, ausencia de REMOVED incorrectos, fallo parcial del lote, vacío sin legacy inventado, espejo de omisiones y todas las rutas HTTP. Los nueve casos nuevos pasaron también por separado (61,19s). Validación pública de checksums/ZIP/API pasó fuera del sandbox sin repetir crawling. Los dos avisos corresponden a Starlette/TestClient (httpx) y alias BlockingPortal de AnyIO; no se instalaron dependencias nuevas ni una toolchain de linter/tipos no configurada.

Cambios propios respecto al baseline operativo: B13, CLI checkpointed/B9, executor y modelo de estados, API CLI, pruebas correspondientes, README y este handoff/JSON de evidencia. No se alteraron configs operativas, requisitos oficiales, límites por defecto, microkernel o puertos REST. No se creó otro runner. Comparación por archivo/hash en la evidencia; no atribuir todo el diff contra HEAD a este trabajo.

Baseline **%TEMP%/datax-operational-baseline-20261022-alw63fp1/**: 349 archivos copiados con SHA-256, status y diff binario; inicio **70 modificados/1.116 no rastreados/índice vacío**. Estado final: **70 versionados modificados / 1118 no rastreados / índice vacío**. Rama fix/prospector-cierre, HEAD 7a7caead651529297737db941d84511b0f6533bf. Sin stage/commit/push, borrado/movimiento histórico o cambio global safe.directory. Pruebas/canarios en TEMP; outputs históricos del checkout conservados. Anexo y ZIP b13-e2e mantienen sus SHA registrados.

El manifiesto de retiro conserva 48 archivos/383.027 bytes: candidatos, referencias y propuesta de respaldo, ninguno retirado. No ejecutar benchmark_runner.py ni apply_batch_*.py. No ampliar ignore para ocultar evidencia. Futuros commits selectivos, no ejecutados: modo incremental/selección/request; API CLI/espejo; E2E/telemetría; documentación. Revisar hunks y secretos antes de stage; no git add -A.

## Bloqueos y límites finales

| Pendiente / responsable | Evidencia y acción concreta |
|---|---|
| Docker/Mongo — **DevOps** | Pipe Linux inexistente; puertos 27017/27018 sin conexión. Habilitar entorno aislado y validar índices/replay/cortes, persistencia de volumen e imágenes batch/API. No certificado por fakes. |
| MEFP / cinco iniciales — **responsable fuente + DevOps** | HTTPx rechazó emisor de robots en el diagnóstico previo; TLS nativo sí validó certificado vigente. Resolver cadena/CA sin verify=False y obtener cinco SUCCESS reales. No se repitió ese diagnóstico en este cierre. |
| FDTA — **responsable de fuentes** | Retención de identidad ICCO vigente. Confirmar autoría/URLs antes de cambiar mapa/hold, usar otro ID/perfil para cualquier promoción. |
| Decisiones y revisión — **arquitectura/product owner** | Pydantic vs dataclasses, cadencia/scheduler y default250 vs criterio500 siguen sin aprobación formal. Mantener límites y revisión por pares. |
| Producción/scheduler — **DevOps** | ExecutionPolicy y alcance de lock histórico; no hay coordinación distribuida de escritores. Acordar propietario único, secretos, TLS/autenticación, CORS/proxy y backups; no registrar tareas automáticamente. |
| Bridge/aceptación — **integrador + dueño Bridge** | REST disponible; falta contrato UnifiedSearchDocument/export-index y aceptación de consumidor. No desarrollar Interno/índice supuesto. |
| Rastreo masivo — **operador** | **NO EJECUTADO por prohibición expresa de este encargo**. Hay telemetría por fuente, pero global requiere autorización independiente y ventana/capacidad/gates verificados; no extrapolar canarios. |

## Resumen para compartir con ChatGPT

B13 ofrece un comando por carga inicial, incremental elegible, resume y plan, con JSON como catálogo coherente y espejo Mongo opcional. API independiente consulta el mismo catálogo por CLI y expone Swagger. 696 PASS/1 SKIP, 80,26% global; Chromium real 30 páginas. Fixture dos físicas + alias/retención/suspensión; incremental cero requests y Source/snapshots conservados; BBV real 49→73, API devuelve73, dos ZIP válidos preservados. Docker/Mongo bloqueados, MEFP/FDTA/decisiones/Bridge pendientes. Sin masivo, commits, push ni destrucción histórica. Entrega funcional de desarrollo; **NO CERTIFICADO PARA PRODUCCIÓN**.

## Resumen para el compañero

Iniciar API con --backend json --catalog-dir de la carga (o usar el catálogo BBV de ensayo ya generado). Swagger /docs, OpenAPI /openapi.json. Consumir arrays Source/ResourceCandidate y RunReport/observaciones; conservar source_id/resource_key/run_id, mostrar parcialidad/SKIPPED y no inferir eliminaciones por página vacía. Usar manifest/checksums de la revisión indicada por state.package. Configurar proxy/origen/seguridad antes de frontend externo. No hay export-index/UnifiedSearchDocument aprobado; puede integrarse el catálogo REST actual sin Interno.

## Cómo funciona, desde cero

El prospector descubre **referencias públicas**, conserva su origen y registra qué pudo recorrer. Un candidato PDF no prueba que el archivo siga disponible ni que contenga estadísticas. No se descarga su contenido a disco. Para conocer nombres internos de ZIP/TAR se usa un buffer temporal en RAM con límites.

1. `config/sources.yaml` define la fuente física, sus seeds, hosts, extensiones y límites.
2. El batch crea una corrida y consulta salud y próxima elegibilidad. El registro resuelve `html`, `commented_html`, `api`, `javascript` o `custom`.
3. El workflow devuelve `ExtractionResult`: URLs, candidatos, cobertura y errores. HTTP reutiliza conexiones; Playwright sirve para contenido que necesita JavaScript.
4. `CatalogApplicationService` compara evidencia, guarda snapshot, catálogo consolidado y observación por fuente. `CadencePolicyEvaluator` actualiza salud y planificación.
5. La proyección offline transforma un snapshot en familias DATAX y en el JSON legacy `ESTADISTICAS`. No necesita repetir el rastreo.
6. MongoDB conserva estado compartido y trazabilidad para procesos separados. La API consulta ese catálogo; el frontend no debe escribir directamente en él.

B13 reúne B9B → crawler → proyección → paquete y, opcionalmente, sincronización Mongo. Su `pipeline_run_id` identifica el lote; cada subprocess de fuente tiene un `run_id` distinto. No intercambiar ambos IDs al consultar `/runs/{id}`.

| Capa | Código real | Responsabilidad |
|---|---|---|
| Dominio | `src/prospector_externo/domain/` | Modelos, URLs, cobertura, salud, cadencia, proyección |
| Kernel | `kernel/registry.py`, `workflow_port.py`, `contracts.py` | Registro, contrato y resultado de extracción |
| Aplicación | `application/orchestrator.py`, `catalog_service.py`, `cadence_evaluator.py` | Corrida, reconciliación y planificación |
| Puertos | `ports/catalog_repository.py`, `run_report_repository.py` | Interfaces de almacenamiento |
| Infraestructura/adaptadores | `infrastructure/`, `adapters/` | HTTP, navegador, archivos en RAM, JSON, Mongo y REST |
| Entradas | `apps/crawler_batch/`, `catalog_api/`, `b13_pipeline/` | CLI, servicio HTTP y pipeline consolidado |
| Checkpoints de lote | `src/execution/`, `src/persistence/` | Estado B9B por fuente lógica y recuperación |

El monolito modular y el registro se conservan. `dispatcher.py` y `kernel/workflow_port.py` movieron sus imports de infraestructura usados solo para tipado bajo `TYPE_CHECKING`; la persistencia del cursor se inyecta por el puerto existente. Aún hay desviaciones: `application/orchestrator.py` importa YAML y clases concretas de infraestructura. Las fábricas existentes siguen siendo el punto de composición; no se añadió otra arquitectura ni otro runner. El cliente HTTP síncrono histórico y los runners de auditoría permanecen.

## Matriz HU-01 a HU-22

Rutas de código abreviadas bajo `src/prospector_externo/`; pruebas bajo `tests/`. **PASS se limita al comportamiento funcional probado**; no significa DoD global completado ni aprobación de producción. El backlog conserva sus estados originales y contiene decisiones de arquitectura aún pendientes.

| HU | Requisito real | Código | Prueba/evidencia | Estado | Remediación o límite exacto |
|---|---|---|---|---|---|
| 01 | Entidades, normalización y SHA-256 determinista | `domain/models.py`, `normalizer.py` | `test_domain_models.py`, `test_url_normalizer.py` | PARTIAL | Función probada; usa Pydantic, mientras el DoD particular pide stdlib/dataclasses y el transversal acepta Pydantic. Acordar esta contradicción, sin reescribir modelos. |
| 02 | Suspender con 3 fallos; separar NO_CHANGE de fallo | `domain/health.py`, `cadence.py`, `application/status_gate.py` | `test_domain_health_and_cadence.py`, `test_continuation_catalog_safety.py` | PASS | Añadido primer fallo persistido y suspensión al confirmar otro tras >24 h; éxito completo resetea. No se inventa fallo por tiempo transcurrido. Clasificación legal/crítica propuesta no certificada. |
| 03 | Registro de estrategias y WorkflowNotFoundError | `kernel/registry.py`, `workflow_port.py` | `test_workflow_registry.py` | PASS | Se añadió la excepción nombrada, compatible con ValueError. |
| 04 | Seeds HTML, PDFs FINRURAL, exclusiones sin binarios | `workflows/html_workflow.py`, `domain/discovery.py` | `test_discovery_checkpoint_resume.py`; FINRURAL actual 203 RAW / 135 seleccionados (R0/revisión) | PARTIAL | Reanuda cola y conserva RAW con presupuesto idéntico; en R0 quedan 2 pendientes y 2 fallos; los 142 del canario son antecedente histórico. No prueba todos los PDFs del portal. |
| 05 | Extraer y normalizar enlaces de comentarios HTML | `workflows/commented_html.py` | `test_async_discovery_integration.py`, `test_batch12d_url_safety.py` | PASS | BBV actual: 1.000 RAW / 931 seleccionados, 6 pendientes y 6 fallos; el canario 49→73 queda como antecedente. No certifica el portal completo. |
| 06 | API y custom registrados y extensibles | `workflows/api_workflow.py`, `custom_workflow.py` | `test_api_workflow_batch3a.py`, `test_api_workflow_batch3b.py`, `test_workflow_registry.py` | PASS | TRANSTATS conserva trabajo de adquisición; no se simula una descarga de formulario. |
| 07 | Hijos ZIP/TAR en RAM, cero extracción a disco | `infrastructure/archive_extractor.py`, `workflows/base.py` | `test_archive_extractor.py`, `test_async_discovery_integration.py` | PASS | Inspección limitada por bytes/entradas; hijos sin URL directa siguen en RAW. |
| 08 | Bulkhead asíncrono; ≤2 navegadores simultáneos | `infrastructure/concurrency.py`, `application/orchestrator.py` | `test_async_worker_pool.py`, `test_orchestrator_hexagonal.py` | PASS | El batch aplica slots; B13 invoca una fuente por subprocess secuencialmente. No atribuirle concurrencia entre fuentes. |
| 09 | HTTP resiliente, robots, HEAD→GET truncado | `infrastructure/async_http_client.py`, `robots_policy.py`, `host_politeness.py` | `test_async_http_client.py`, `test_robots_policy.py`, `test_real_smoke_hardening.py` | PASS | Cliente síncrono antiguo no equivale al runtime efectivo. No se habilitó override de robots. |
| 10 | Snapshot y observación inmediatamente por fuente | `application/catalog_service.py`, adaptadores | `test_local_json_adapter.py`, `test_resilience_interruption.py` | PASS | Evidencia JSON; transacción multifichero y Mongo real quedan fuera de esta certificación. |
| 11 | Reporte batch con desglose y sin notificaciones | `application/orchestrator.py`, `aggregator.py`, `report_builder.py` | `test_orchestrator_hexagonal.py` | PASS | REST usa `execution_status` y métricas bajo `coverage`; documentar esta forma frente a ejemplos conceptuales `status`. |
| 12 | API de consulta; POST/PUT/DELETE catálogo →405 | `adapters/api/routes.py`, `apps/catalog_api/main.py` | `test_catalog_api.py`, `test_operational_single_command_e2e.py`; HTTP real 200/404/422/405/503 | PARTIAL | Contrato local probado. Autenticación/TLS de producción y `/search/export-index` pendientes; no crear el esquema del Bridge. |
| 13 | 30 páginas, reciclaje 15 páginas/1 GiB, bloqueo de assets | `infrastructure/browser_driver.py`, `workflows/javascript_workflow.py` | `test_browser_real_30_pages.py`: evidencia histórica local; omitido opt-in en la suite 24/10; Chromium real, 30 recursos/páginas, 2 aperturas, 350,5 MiB RSS muestreado (cierre operativo 23/10) | PASS | PASS funcional del fixture local: un contexto máximo, assets y host externo bloqueados, método javascript. Muestreo entre páginas no demuestra un techo absoluto ni certifica OOM en producción. |
| 14 | Mongo real, 7 colecciones, índices e idempotencia | `adapters/persistence/mongo_adapter.py` | `test_mongo_persistence_adapter.py` usa fakes | EXTERNAL_BLOCKER | Motor/servidor no disponible. Ejecutar pruebas reales de índices, colisiones, replay y corte a mitad de escrituras en base aislada. |
| 15 | Imágenes duales, API <5 s y batch efímero | Dockerfiles y Compose | `test_docker_deployment_hu15.py`; Compose válido | EXTERNAL_BLOCKER | Build/arranque y tiempo hasta /health requieren Docker activo; no fueron medidos. |
| 16 | Scheduler sin solapamiento | `ops/run_scheduled_batch.ps1`, `register_windows_scheduler.ps1` | `test_scheduler_hu16.py` estático | PARTIAL | DryRun real bloqueado por política de ejecución de Windows. Resolver firma/aprobación del script y decisión DevOps; bloqueo solo mismo archivo/host. |
| 17 | 5 NO_CHANGE →WEEKLY; 4 semanas →MONTHLY; reset por cambio | `domain/cadence.py`, `application/cadence_evaluator.py` | `test_continuation_catalog_safety.py`, `test_domain_health_and_cadence.py` | PARTIAL | Corregido: fallo/parcial/vacío rompe la secuencia; exige cinco éxitos nuevos. Umbrales/28 días y replay probados. DoR: aprobación de valores de la Decisión 1.1 sigue pendiente. |
| 18 | Depth 2, 500 URLs, 900 s, cancelación wait_for y parcialidad | `domain/discovery.py`, `domain/models.py`, `application/dispatcher.py`, workflows | `test_source_runtime_deadline.py`, `test_discovery_frontier.py` | PARTIAL | Añadido wait_for, RAW al timeout y cancelación externa diferenciada. Default conservado: 250 URLs, depth 2, 900 s; falta aprobar Decisión 1.3/500 URLs. Limpieza de browser puede extender el deadline hasta sus timeouts de I/O. |
| 19 | REMOVED solo con 3 ausencias verificadas; reaparición resetea | `application/catalog_service.py`, `domain/observations.py` | `test_continuation_catalog_safety.py`, `test_local_json_adapter.py`, `test_discovery_checkpoint_resume.py` | PASS | Fingerprint en Source/snapshot/observación; cambio de alcance resetea ausencias. Histórico sin perfil establece baseline sin sumar. Parciales/fallos/reanudados no confirman ausencias. |
| 20 | /mapping/inspect normalización, tipo y hash; 422 inválidas | `adapters/api/routes.py` | `test_catalog_api.py`, smoke ASGI | PASS | Offline, hash de URL; no de contenido ni prueba de existencia. Aprobación del consumidor pendiente como DoR. |
| 21 | FINRURAL, BBV, ASFI, BCB y MEFP con SUCCESS | `config/sources.yaml`, planes y mapeo | `test_sources_config.py`, fixtures FINRURAL/BCB, canario FINRURAL | FAIL | MEFP sigue fuera de las 39 físicas: HTTPx rechaza emisor al consultar robots; TLS nativo valida certificado vigente. FINRURAL/BBV/ASFI son PARTIAL_SUCCESS, BCB SUCCESS. No hay cinco SUCCESS actuales. |
| 22 | Kill durante fuente 3; fuentes 1 y 2 consultables al reiniciar | `application/orchestrator.py`, JSON y API | `test_resilience_interruption.py`: TerminateProcess real y nueva API | PARTIAL | Escenario JSON probado. Repetir con Mongo y contenedor; owners de otros hosts no se recuperan por suposición. |

## Inventario completo y cobertura conservada

Matriz actualizada el **24/10/2026** desde los siete estados, manifests y reportes existentes. [Evidencia estructurada y hashes](audit/20261024_delivery_closure.json). **52 lógicas = 39 físicas + 5 alias + 1 hold + 7 status-only**. Las 45 operativas incluyen FDTA retenida. No hubo adquisiciones nuevas.

DATAX es el legacy del paquete vigente de esa corrida/revisión; API es la selección técnica actual sin contrato de agrupamiento de exportación. En alias, resultado/pendientes/fallos son referencia física, no una exportación lógica nueva. En status-only/hold la fecha es mtime de configuración (documental); las demás son finished_at UTC del reporte físico. R0–R6 se definen en la tabla de paquetes; cada enlace apunta a evidencia exacta, con crawl_run_id y SHA en el JSON.

| # | Lógica → física | Rol / workflow efectivo | Resultado / clasificación | DATAX / API | Pend. / fallos | Motivo o límite | Fecha UTC; corrida/evidencia |
|---:|---|---|---|---:|---:|---|---|
| 1 | `anapo → anapo` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 99 / 99 | 59 / 0 | REQUEST_BUDGET_REACHED | 2026-10-23T22:52:06Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/anapo/reports/run_run_20261023_224755_573e85.json) |
| 2 | `aps → aps` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 160 / 160 | 1 / 1 | QUEUE_EXHAUSTED | 2026-10-23T22:53:20Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/aps/reports/run_run_20261023_225207_38f970.json) |
| 3 | `asfi_valores → asfi_valores` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 1000 / 1000 | 25 / 3 | REQUEST_BUDGET_REACHED | 2026-10-23T22:38:50Z; [R1](../output/checkpointed/validacion-fuentes-01-20261023-181728/asfi_valores/reports/run_run_20261023_223437_e635aa.json) |
| 4 | `bcb → bcb` | PHYSICAL / html | SUCCESS / DATAX_READY | 537 / 585 | 0 / 0 | QUEUE_EXHAUSTED | 2026-10-23T22:18:29Z; [R1](../output/checkpointed/validacion-fuentes-01-20261023-181728/bcb/reports/run_run_20261023_221733_4fbdab.json) |
| 5 | `bm → bm` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 922 / 922 | 111 / 1 | REQUEST_BUDGET_REACHED | 2026-10-24T00:45:45Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/bm/reports/run_run_20261024_004131_723791.json) |
| 6 | `cepal → cepal` | PHYSICAL / html | FAILED / EXECUTION_FAILED | 0 / — | 1 / 1 | HTTP_403 | 2026-10-23T22:53:24Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/cepal/reports/run_run_20261023_225321_8ab2ee.json) |
| 7 | `fegasacruz → —` | STATUS_ONLY / estado | NO_PUBLIC_DATA_EVIDENCE | — / — | — / — | NO_PUBLIC_DATA_EVIDENCE | 2026-10-21T06:14:06Z; [config](../config/source_operational_plan.yaml) |
| 8 | `finrural → finrural` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 135 / 135 | 2 / 2 | QUEUE_EXHAUSTED | 2026-10-23T21:13:08Z; [R0](../output/checkpointed/carga-20261023-171007/finrural/reports/run_run_20261023_211011_2f85bf.json) |
| 9 | `fmi → fmi` | PHYSICAL / html | SUCCESS / DATAX_READY | 9 / 9 | 0 / 0 | QUEUE_EXHAUSTED | 2026-10-23T22:53:47Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/fmi/reports/run_run_20261023_225325_114bbf.json) |
| 10 | `mefp → —` | STATUS_ONLY / estado | TLS_TECHNICAL_HOLD | — / — | — / — | TLS_TECHNICAL_HOLD | 2026-10-21T06:14:06Z; [config](../config/source_operational_plan.yaml) |
| 11 | `mmym → mmym` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 171 / 171 | 11 / 1 | REQUEST_BUDGET_REACHED | 2026-10-23T22:58:07Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/mmym/reports/run_run_20261023_225348_46aaed.json) |
| 12 | `omc → omc` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 103 / 103 | 2 / 2 | QUEUE_EXHAUSTED | 2026-10-24T00:49:17Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/omc/reports/run_run_20261024_004547_1d014b.json) |
| 13 | `statistics_denmark → statistics_denmark` | PHYSICAL / api | SUCCESS / DATAX_READY | 1 / 1 | 0 / 0 | API_SEEDS_EXHAUSTED | 2026-10-23T22:38:55Z; [R1](../output/checkpointed/validacion-fuentes-01-20261023-181728/statistics_denmark/reports/run_run_20261023_223851_549dcb.json) |
| 14 | `vipfe → vipfe` | PHYSICAL / javascript | SUCCESS / DATAX_READY | 3 / 3 | 0 / 0 | QUEUE_EXHAUSTED | 2026-10-23T22:18:43Z; [R1](../output/checkpointed/validacion-fuentes-01-20261023-181728/vipfe/reports/run_run_20261023_221836_5c55af.json) |
| 15 | `ada → ada` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 1000 / 1000 | 4 / 2 | REQUEST_BUDGET_REACHED | 2026-10-24T00:53:41Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/ada/reports/run_run_20261024_004918_a33b81.json) |
| 16 | `atc → —` | STATUS_ONLY / estado | NO_PUBLIC_DATA_SCOPE | — / — | — / — | NO_PUBLIC_DATA_SCOPE | 2026-10-21T06:14:06Z; [config](../config/source_operational_plan.yaml) |
| 17 | `cadexco → cadexco` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 16 / 16 | 5 / 8 | REQUEST_BUDGET_REACHED | 2026-10-24T01:01:56Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/cadexco/reports/run_run_20261024_005343_387932.json) |
| 18 | `cndc → cndc` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 146 / 146 | 12 / 1 | REQUEST_BUDGET_REACHED | 2026-10-24T01:06:09Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/cndc/reports/run_run_20261024_010157_020785.json) |
| 19 | `dgac → dgac` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 571 / 571 | 29 / 0 | MAX_RUNTIME | 2026-10-24T01:21:12Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/dgac/reports/run_run_20261024_010611_f5d6b1.json) |
| 20 | `fam → fam` | PHYSICAL / html | SUCCESS / DATAX_READY | 111 / 111 | 0 / 0 | QUEUE_EXHAUSTED | 2026-10-23T22:58:35Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/fam/reports/run_run_20261023_225808_71af79.json) |
| 21 | `fifa → fifa` | PHYSICAL / custom | SUCCESS / DATAX_READY | 1 / 1 | 0 / 0 | API_SEEDS_EXHAUSTED | 2026-10-24T01:21:15Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/fifa/reports/run_run_20261024_012113_28a6c0.json) |
| 22 | `fundempresa → —` | STATUS_ONLY / estado | RETIRED_HISTORICAL_SOURCE | — / — | — / — | RETIRED_HISTORICAL_SOURCE | 2026-10-21T06:14:06Z; [config](../config/source_operational_plan.yaml) |
| 23 | `icco → icco` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 54 / 54 | 5 / 0 | REQUEST_BUDGET_REACHED | 2026-10-24T03:01:54Z; [R5](../output/checkpointed/aceptacion-lote-05-20261023-225630/icco/reports/run_run_20261024_025631_930c1b.json) |
| 24 | `itu → itu` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 83 / 83 | 197 / 7 | MAX_CONSECUTIVE_ERRORS | 2026-10-24T01:23:04Z; [R3](../output/checkpointed/aceptacion-lote-03-20261023-204129/itu/reports/run_run_20261024_012117_794050.json) |
| 25 | `mdryt_oap → mdryt_oap` | PHYSICAL / html | FAILED / EXECUTION_FAILED | 0 / — | 1 / 1 | ROBOTS_UNREACHABLE | 2026-10-24T03:02:00Z; [R5](../output/checkpointed/aceptacion-lote-05-20261023-225630/mdryt_oap/reports/run_run_20261024_030156_2c6cb7.json) |
| 26 | `otros_historicos → otros_historicos` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 3 / 3 | 0 / 0 | QUEUE_EXHAUSTED; archive_inspection_errors=2 | 2026-10-23T22:59:09Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/otros_historicos/reports/run_run_20261023_225835_d78458.json) |
| 27 | `sabsa → —` | STATUS_ONLY / estado | RETIRED_HISTORICAL_SOURCE | — / — | — / — | RETIRED_HISTORICAL_SOURCE | 2026-10-21T06:14:06Z; [config](../config/source_operational_plan.yaml) |
| 28 | `sicoes → sicoes` | PHYSICAL / javascript | SUCCESS / NO_RESOURCES | 0 / 0 | 0 / 0 | QUEUE_EXHAUSTED; EMPTY_RESULT | 2026-10-24T02:12:32Z; [R4](../output/checkpointed/aceptacion-lote-04-20261023-221223/sicoes/reports/run_run_20261024_021225_773735.json) |
| 29 | `aps_soat → aps` | ALIAS / html | PARTIAL_SUCCESS / DATAX_READY | — / — | 1 / 1 | QUEUE_EXHAUSTED; ref. física, sin export lógico nuevo | 2026-10-23T22:53:20Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/aps/reports/run_run_20261023_225207_38f970.json) |
| 30 | `asfi → asfi_valores` | ALIAS / html | PARTIAL_SUCCESS / DATAX_READY | — / — | 25 / 3 | REQUEST_BUDGET_REACHED; ref. física, sin export lógico nuevo | 2026-10-23T22:38:50Z; [R1](../output/checkpointed/validacion-fuentes-01-20261023-181728/asfi_valores/reports/run_run_20261023_223437_e635aa.json) |
| 31 | `asfi_finrural → asfi_valores` | ALIAS / html | PARTIAL_SUCCESS / DATAX_READY | — / — | 25 / 3 | REQUEST_BUDGET_REACHED; ref. física, sin export lógico nuevo | 2026-10-23T22:38:50Z; [R1](../output/checkpointed/validacion-fuentes-01-20261023-181728/asfi_valores/reports/run_run_20261023_223437_e635aa.json) |
| 32 | `bolcereales → —` | STATUS_ONLY / estado | IDENTITY_UNRESOLVED | — / — | — / — | IDENTITY_UNRESOLVED | 2026-10-21T06:14:06Z; [config](../config/source_operational_plan.yaml) |
| 33 | `ceprobol → —` | STATUS_ONLY / estado | RETIRED_HISTORICAL_SOURCE | — / — | — / — | RETIRED_HISTORICAL_SOURCE | 2026-10-21T06:14:06Z; [config](../config/source_operational_plan.yaml) |
| 34 | `fdta_valles → icco` | IDENTITY_HOLD / html | EXTERNAL_BLOCKER | — / — | — / — | IDENTITY_UNVERIFIED_FDTA_ICCO; no copiar ICCO | 2026-10-23T01:02:47Z; [config](../config/source_execution_map.yaml) |
| 35 | `ibch → ibch` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 37 / 37 | 1 / 2 | QUEUE_EXHAUSTED | 2026-10-23T23:01:27Z; [R2](../output/checkpointed/aceptacion-lote-02-20261023-184753/ibch/reports/run_run_20261023_225910_a226f4.json) |
| 36 | `ine → ine` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 537 / 537 | 46 / 1 | REQUEST_BUDGET_REACHED | 2026-10-24T03:08:26Z; [R5](../output/checkpointed/aceptacion-lote-05-20261023-225630/ine/reports/run_run_20261024_030201_8c5070.json) |
| 37 | `mhe → mhe` | PHYSICAL / javascript | EXTERNAL_BLOCKER / EXTERNAL_BLOCKER | 0 / — | 1 / 1 | ROBOTS_UNREACHABLE | 2026-10-24T03:44:33Z; [R6](../output/checkpointed/aceptacion-lote-06-20261023-234425/mhe/reports/run_run_20261024_034429_2671a3.json) |
| 38 | `seprec → seprec` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 205 / 205 | 1 / 1 | QUEUE_EXHAUSTED | 2026-10-24T03:10:33Z; [R5](../output/checkpointed/aceptacion-lote-05-20261023-225630/seprec/reports/run_run_20261024_030827_3d64c6.json) |
| 39 | `undata → undata` | PHYSICAL / api | PARTIAL_SUCCESS / DATAX_READY | 2 / 2 | 0 / 0 | CURATED_API_SEEDS_EXHAUSTED | 2026-10-24T02:12:36Z; [R4](../output/checkpointed/aceptacion-lote-04-20261023-221223/undata/reports/run_run_20261024_021233_82d95c.json) |
| 40 | `ae → ae` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 300 / 300 | 0 / 0 | QUEUE_EXHAUSTED; pagination_families_stopped=1 | 2026-10-24T02:15:27Z; [R4](../output/checkpointed/aceptacion-lote-04-20261023-221223/ae/reports/run_run_20261024_021237_460e0b.json) |
| 41 | `asfi_bcb → bcb` | ALIAS / html | SUCCESS / DATAX_READY | — / — | 0 / 0 | QUEUE_EXHAUSTED; ref. física, sin export lógico nuevo | 2026-10-23T22:18:29Z; [R1](../output/checkpointed/validacion-fuentes-01-20261023-181728/bcb/reports/run_run_20261023_221733_4fbdab.json) |
| 42 | `asofin → asofin` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 168 / 168 | 29 / 3 | REQUEST_BUDGET_REACHED | 2026-10-24T03:16:45Z; [R5](../output/checkpointed/aceptacion-lote-05-20261023-225630/asofin/reports/run_run_20261024_031034_5b06f9.json) |
| 43 | `att → att` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 1000 / 1000 | 3 / 1 | REQUEST_BUDGET_REACHED | 2026-10-24T03:20:58Z; [R5](../output/checkpointed/aceptacion-lote-05-20261023-225630/att/reports/run_run_20261024_031646_78d657.json) |
| 44 | `bbv → bbv` | PHYSICAL / commented_html | PARTIAL_SUCCESS / DATAX_READY | 931 / 931 | 6 / 6 | QUEUE_EXHAUSTED | 2026-10-23T21:22:31Z; [R0](../output/checkpointed/carga-20261023-171007/bbv/reports/run_run_20261023_211309_f27290.json) |
| 45 | `data_gov → data_gov` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 218 / 218 | 109 / 5 | MAX_CONSECUTIVE_ERRORS | 2026-10-24T03:47:30Z; [R6](../output/checkpointed/aceptacion-lote-06-20261023-234425/data_gov/reports/run_run_20261024_034435_63b125.json) |
| 46 | `ibce_cao → ibce_cao` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 88 / 88 | 160 / 3 | REQUEST_BUDGET_REACHED | 2026-10-24T03:50:24Z; [R6](../output/checkpointed/aceptacion-lote-06-20261023-234425/ibce_cao/reports/run_run_20261024_034733_f8be77.json) |
| 47 | `mdryt → mdryt_oap` | ALIAS / html | FAILED / EXECUTION_FAILED | — / — | 1 / 1 | ROBOTS_UNREACHABLE; ref. física, sin export lógico nuevo | 2026-10-24T03:02:00Z; [R5](../output/checkpointed/aceptacion-lote-05-20261023-225630/mdryt_oap/reports/run_run_20261024_030156_2c6cb7.json) |
| 48 | `min_educacion → min_educacion` | PHYSICAL / html | FAILED / EXECUTION_FAILED | 0 / — | 1 / 1 | TIMEOUT | 2026-10-24T02:16:00Z; [R4](../output/checkpointed/aceptacion-lote-04-20261023-221223/min_educacion/reports/run_run_20261024_021527_7d0053.json) |
| 49 | `senamhi → senamhi` | PHYSICAL / html | FAILED / EXECUTION_FAILED | 0 / — | 1 / 1 | ROBOTS_UNREACHABLE | 2026-10-24T02:16:20Z; [R4](../output/checkpointed/aceptacion-lote-04-20261023-221223/senamhi/reports/run_run_20261024_021601_904793.json) |
| 50 | `sigma → sigma` | PHYSICAL / javascript | EXTERNAL_BLOCKER / EXTERNAL_BLOCKER | 0 / — | 1 / 1 | ROBOTS_UNREACHABLE | 2026-10-24T03:50:31Z; [R6](../output/checkpointed/aceptacion-lote-06-20261023-234425/sigma/reports/run_run_20261024_035025_174415.json) |
| 51 | `snis → snis` | PHYSICAL / html | PARTIAL_SUCCESS / DATAX_READY | 35 / 35 | 2 / 3 | QUEUE_EXHAUSTED | 2026-10-24T03:56:04Z; [R6](../output/checkpointed/aceptacion-lote-06-20261023-234425/snis/reports/run_run_20261024_035032_b50ef8.json) |
| 52 | `transtats → transtats` | PHYSICAL / custom | SUCCESS / ACQUISITION_JOB_READY | 0 / 0 | 0 / 0 | CUSTOM_FORM_CONFIRMED; job sin fichero | 2026-10-24T02:16:45Z; [R4](../output/checkpointed/aceptacion-lote-04-20261023-221223/transtats/reports/run_run_20261024_021620_058d5f.json) |

Los cinco alias habilitados son `aps_soat→aps`, `asfi/asfi_finrural→asfi_valores`, `asfi_bcb→bcb`, `mdryt→mdryt_oap`. Conservan identidad/procedencia mediante reuse_acquisition; entre invocaciones solo se reutiliza SUCCESS coherente, dentro del intento puede compartirse parcial. No hay una entrega lógica nueva demostrada para esos alias en las siete corridas, ni cinco fuentes extra en la API. FDTA no hereda autoría ICCO. Las siete status-only conservan restricciones y las entidades retiradas no se sustituyen por homónimos.

Los workflows provienen del YAML físico actual, no de etiquetas históricas del plan. TRANSTATS representa un trabajo GET/formulario sin enviar POST. Otros históricos/Doing Business conserva su alcance histórico y límites de inspección. MHE/SIGMA mantienen ROBOTS_UNREACHABLE y clasificación EXTERNAL_BLOCKER; no se alteraron robots/TLS.

## RAW, proyección, almacenamiento y recuperación

Los modelos efectivos están en `domain/models.py`, `domain/observations.py` y `domain/projection.py`. `schemas/source-map.schema.json` es un esquema histórico: exige `schema_version/crawl_run/datasets`, mientras el mapa estándar actual tiene `version/source/run/summary/resources`. No usar ese esquema antiguo para validar el formato nuevo.

Ejemplo abreviado de candidato RAW (ilustrativo; no afirma que esa URL exista):

```json
{
  "source_id": "ejemplo",
  "resource_key": "sha256(source_id|normalized_url)",
  "url": "https://example.org/publicaciones/informe.pdf",
  "discovered_from_url": "https://example.org/publicaciones/",
  "discovery_method": "html_link",
  "file_extension": ".pdf",
  "change_status": "NEW",
  "consecutive_absences": 0
}
```

El snapshot envuelve recursos y URLs con `source_id/run_id/captured_at/resources_hash`. El hash de URL identifica la referencia; un `content_hash` solo representa contenido cuando el mecanismo lo calculó. ETag/Last-Modified son evidencia de cambio, no garantía de equivalencia binaria.

La proyección tiene `schema_version: datax-projection-1.0`, `source_id`, `run_id`, `total_raw_resources`, `total_selected_resources`, `families` y `decisions`. Un registro legacy conserva seis campos:

```json
{
  "ESTADISTICAS": {
    "files": {
      "Informe": {
        "Descargar.csv": {
          "descripcion": "Informe",
          "url_descarga": "https://example.org/publicaciones/informe.pdf",
          "fecha_actualizacion": "",
          "tipo_archivo": "PDF",
          "url_origen": "https://example.org/publicaciones/",
          "metodo_deteccion": "html_link"
        }
      }
    }
  }
}
```

El nombre histórico `Descargar.csv` no determina el formato real: manda `tipo_archivo`. El ejemplo muestra campos; para validar tipos y rutas exactas usar `LegacyStatsContractValidator` y fixtures de `tests/fixtures/`.

**Cambio heredado de la continuación previa:** se eliminó de la política por defecto la exclusión de documentos por palabras administrativas y de ZIP por ausencia de vocabulario estadístico. Se mantienen exclusiones técnicas: URL inválida, miembro de archivo sin URL directa, API no GET/privada/documentación, formato desconocido y contratos explícitos de agrupamiento. El filtrado de negocio queda en downstream. Nuevas exportaciones pueden contener más registros y hashes distintos, con el mismo esquema.

La evidencia histórica ANAPO de 21/10 conserva **311 RAW →302 DATAX**: nueve decisiones eran `administrative_document`. La reproyección de la continuación previa, **solo en memoria**, produjo **311 seleccionados, 311 registros y 308 familias**. No se modificaron los 302 históricos. FINRURAL del canario de auditoría produjo 40→40 y Data.gov 15→14 **antes** de ese ajuste. Esos ensayos son antecedentes, distintos de BBV y la regresión de esta ejecución. El ZIP histórico b13-e2e conserva el hash registrado en el anexo.

| Artefacto | Ruta |
|---|---|
| Checkpoint B13 nuevo por lote | `.runtime/b13_pipeline/<pipeline-id>/checkpoint.json` |
| Configuración del checkpoint | `.runtime/b13_pipeline/<pipeline-id>/checkpointing.json` |
| Estado y logs B13 | `.runtime/b13_pipeline/<pipeline-id>/state.json`, `logs/`, `checkpointed_batch.log` |
| Evidencia aislada de fuente lógica | `output/checkpointed/<pipeline-id>/<logical-id>/state/`, `reports/` |
| Snapshot RAW | `state/snapshots/<physical-id>_<crawl-run-id>.json` |
| Catálogo consolidado | `state/resource_catalog/<physical-id>.json` |
| JSON estándar/árbol/compacto | `<physical-id>/mapa_<physical-id>*.json` |
| Proyección y DATAX | `<physical-id>/downstream/{datax_projection,legacy_estadisticas,export_manifest}.json` |
| Paquete inicial | `output/b13-pipeline/<pipeline-id>/datax-package/latest/` y `output/b13-pipeline/<pipeline-id>/datax-package/datax_package.zip` |
| Revisiones siguientes | `datax-package/revisions/<UTC-uuid>/` y `datax_package_<UTC-uuid>.zip`; consultar state.package.package_dir. Los primeros artefactos permanecen intactos. |
| Catálogo JSON compartido | `output/state/` y `output/reports/` |

El snapshot describe esa corrida; el catálogo consolidado conserva recursos antiguos en recorridos parciales. Una ausencia solo suma con cierre completo, continuo y perfil comparable; REMOVED requiere tres ausencias. Replay no suma otra. Cambiar seeds, scope, profundidad o límites de admisión cambia el fingerprint y resetea los contadores anteriores. Un catálogo histórico sin perfil establece baseline sin sumar ausencia. Un ciclo reunido mediante varios intentos conserva RAW, pero no confirma ausencia ni simultaneidad de todas las referencias.

B9B guarda PENDING/RUNNING/SUCCEEDED/PARTIAL/FAILED/SKIPPED_STATUS. `--resume` omite SUCCEEDED y vuelve a intentar PARTIAL/FAILED/interrumpidas. Para **html/commented_html/javascript**, el executor pasa `--resume-discovery` y `--pipeline-run-id`: conserva frontera, visitadas, pendientes, profundidad, deduplicación, política de paginación, inventario RAW y comprimidos ya inspeccionados. El elemento en vuelo vuelve a pendientes al reiniciar: replay at-least-once, sin prometer cero repeticiones tras un kill. El estado se guarda antes de solicitudes y al terminar; los errores reintentables se difieren al siguiente intento. El cursor vive en `<salida-aislada>/state/discovery_progress/<physical-id>.json`; cada intento conserva además `<physical-id>/<run_id>.json`. No se coloca el cursor en el catálogo global al usar `--catalog-dir`.

API/custom reinician desde seeds: API mantiene paginación/semántica específica de estrategia y custom puede representar endpoints o trabajos. Serializar indiscriminadamente ese estado podría saltar páginas o falsear completitud. Se conserva replay con presupuestos acotados, sin atribuir recuperación interna ni ahorro medido. JavaScript sí reanuda frontera/RAW; no conserva DOM/contextos entre procesos. El batch directo requiere `--resume-discovery`; sin esa opción hace un recorrido nuevo respetando elegibilidad. Presupuesto de requests y tiempo pueden renovarse sin cambiar perfil; cambios de alcance producen `PROFILE_CHANGED_RESTART`, otro pipeline `PIPELINE_CHANGED_RESTART` y checkpoint antiguo `LEGACY_CHECKPOINT_RESTART`. Un `SUCCEEDED` B9B con fingerprint distinto exige un ID nuevo antes de escribir configuraciones. Los éxitos históricos sin fingerprint recuperable se enumeran en `legacy_config_unverified`; no se reutilizan como adquisiciones demostradas.

Un cursor cuyo traversal terminó pero no fue materializado en catálogo se reproduce sin red. Tras confirmar el catálogo se guarda `materialized_run_id`; un ciclo terminado y materializado inicia uno nuevo. Si un intento reanudado falla en todas sus solicitudes o vence el deadline sin observar nada nuevo, informa **FAILED**, aunque conserve RAW anterior en cursor y catálogo; ese RAW no cuenta como éxito actual y salud registra el fallo. Las sustituciones JSON son atómicas por archivo, con fsync; **no son transacciones multifichero ni locks de varios escritores**. Mantener un solo escritor por salida. B13 sigue secuencial: no se habilitó paralelismo entre subprocesses sin coordinación de catálogo, locks y cortesía por host. Mantener un único escritor por catálogo, también entre comandos directos de procesos distintos.

B13 ahora aísla checkpoints por ID, rechaza sobrescritura sin `--resume` y copia un checkpoint global antiguo solamente si su ID coincide. El original `.runtime/checkpoints/latest.json` se conserva. Si el checkpoint antiguo de otro lote ya lo sustituyó, no se reconstruye éxito por encontrar archivos: hace falta recuperación supervisada en una nueva ubicación. B9B invocado directamente conserva su configuración histórica global; no mezclarlo con B13 sin revisar `--checkpoint-config`.

El sincronizador selecciona el reporte más reciente por fecha y exige coherencia con fuente, corrida finalizada, observación, snapshot y catálogo reconciliado. Rechaza un intento nuevo sin reporte y catálogos de otra corrida. Conserva los intentos previos en disco; la importación publica el intento seleccionado, no rellena automáticamente todos los históricos. `b13-12g-smoke-20261021-01` pasa preflight con 311 recursos ANAPO. Los lotes antiguos `b13-e2e` y `b13-anapo-docs-001` tienen evidencia incompleta para el contrato nuevo (faltan estados de corrida/catálogos): no se fabricaron.

Mongo usa siete colecciones e índices únicos: sources(source_id), runs(run_id), discovered_urls(source_id,normalized_url), resource_candidates(source_id,resource_key), snapshots(source_id,run_id), source_observations(source_id,run_id), run_reports(run_id). El cursor se guarda en un campo privado `_discovery_progress` de sources, sin octava colección ni exposición por Source/REST. Sus pruebas son fakes: replay, aislamiento del estado Source e inserción antes del primer catálogo. Hay índices adicionales para fecha, salud y catálogo vigente. Las escrituras de varias colecciones **no son una transacción**; faltan Mongo real, corte entre escrituras y recuperación. No se certificaron los conteos previos BCB=584/FAM=111.


## Referencia técnica histórica ampliada (opcional)

<details>
<summary>M01–M26 del cierre previo, conservadas; no son tareas rutinarias pendientes del operador</summary>

### Pruebas técnicas ampliadas del cierre anterior — M01 a M26

**Estado indicado = evidencia ejecutada por el agente**, no firma de aceptación del usuario. PASS se limita al fixture/alcance descrito. Los ensayos de infraestructura no disponibles llevan **PENDIENTE DE PRUEBA MANUAL**; no se convirtieron en PASS por usar mocks. El usuario debe registrar fecha/resultado propio en su copia de evidencia.

Preparación común, desde la raíz en PowerShell. Crea una carpeta NUEVA para cada sesión; la función rechaza reutilizar una carpeta de test. Las pruebas que simulan corrupción/kill/ausencias usan TEMP, nunca Mongo/output productivos:

```powershell
$py = '.venv/Scripts/python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$manualRoot = Join-Path $env:TEMP ('datax-aceptacion-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $manualRoot | Out-Null
function Test-Datax {
  param([string]$Id, [string[]]$Cases)
  $target = Join-Path $manualRoot $Id
  if (Test-Path -LiteralPath $target) { throw 'Evidencia existente: crear otra sesión manualRoot' }
  & $py -B -m pytest -q -s -p no:cacheprovider --basetemp $target @Cases 2>&1 |
    Tee-Object -FilePath (Join-Path $manualRoot ($Id + '.log'))
  if ($LASTEXITCODE -ne 0) { throw "FAIL $Id; revisar su log antes de continuar con dependientes" }
}
```

| ID / HU | Prerrequisitos y datos | Comandos o clics exactos | Resultado observable esperado | Evidencia / efecto en datos | Estado y paso ante error |
|---|---|---|---|---|---|
| M01 / entorno | Python ≥3.9; probado 3.13.2, .venv con extras dev/browser/mongo | `& $py --version`; `& $py -m pip check`; `& $py -m apps.crawler_batch.main --help`; `& $py -m apps.b13_pipeline.main run --help`; `& $py -m apps.catalog_sync.main --help` | No broken requirements; ayuda incluye resume-discovery, catalog-dir, mongo-database en los CLI correspondientes | Terminal; solo lectura. Instalación opcional en bloque Preparación, requiere red | **PASS** entorno actual; si falta módulo, instalar extras en .venv, no Python global |
| M02 / inventario | Config real, sin red | `& $py -m apps.b13_pipeline.main plan`; `Test-Datax M02 @('tests/test_offline_configuration_cli_flows.py')` | 52/45/7 y 39 físicas; diferencias de etiquetas históricas explícitas; FDTA tiene hold en el mapa | M02.log y TEMP; no altera configs reales | **PASS**; si conteos difieren, revisar cambios de plan/mapa, no editar números para ocultarlo |
| M03 / HU-04,10 | Fixture y canario FINRURAL separado | `Test-Datax M03 @('tests/test_discovery_checkpoint_resume.py','tests/test_continuation_cli_flows.py')`; para portal real, ejecutar el bloque «Canario reproducible» anterior | RAW con PDFs/URLs/origen; snapshot, observaciones y legacy; PARTIAL con pendientes es válido y debe verse | M03.log, TEMP; canario crea DATAX_CANARY_DIR nuevo y usa red, nunca output real | **PASS fixture y canario previo** (129→132); si portal falla, conservar reporte y detener ese canario, no relajar TLS/robots |
| M04 / resume | MISMA salida del canario M03; fixtures de kill y perfiles | `Test-Datax M04 @('tests/test_discovery_checkpoint_resume.py')`; repetir exactamente el segundo comando del canario con --resume-discovery y --force | Avanza frontera, conserva RAW; at-least-once de URL en vuelo; perfil/pipeline incompatible reinicia con motivo; corrupción rechazada antes de red | M04.log, cursor state/discovery_progress y archivos por intento; solo TEMP | **PASS**; si repite todo, comprobar profile/pipeline/cycle; no borrar cursor histórico |
| M05 / HU-05 | BBV comentado y fixture HTTP | `Test-Datax M05 @('tests/test_async_discovery_integration.py','tests/test_batch12d_url_safety.py')`; abrir audit/20261022_autonomous_execution.json → public_canaries | Comentarios normalizados, discovery_method=commented_html_link; BBV real 49→73, pendientes 24→23, no binarios | M05.log y JSON público preservado; fixtures solo TEMP | **PASS** contrato/canario; no inferir 100 % del portal ni modificar BBV por un comentario inválido |
| M06 / HU-06 | API/custom sintéticos y manifiesto TRANSTATS | `Test-Datax M06 @('tests/test_api_workflow_batch3a.py','tests/test_api_workflow_batch3b.py','tests/test_source_probe_cli_contracts.py','tests/test_b13_local_process_e2e.py')` | API GET acotada; custom genera trabajo DL_SelectFields sin POST ni archivo inventado; se documenta reinicio desde seeds | M06.log, TEMP; HTTP simulado o loopback | **PASS**; si requiere autenticación/POST, conservar estado pendiente, no enviar formulario |
| M07 / HU-13 | Playwright + Chromium instalado; loopback | `$env:DATAX_BROWSER_INTEGRATION='1'`; `Test-Datax M07 @('tests/test_browser_real_30_pages.py','tests/test_javascript_discovery_resume.py')`; `Remove-Item Env:DATAX_BROWSER_INTEGRATION` | 30 páginas/recursos, dos aperturas, un contexto máximo y cero al cerrar; RSS muestreado <1 GiB; assets/scope bloqueados; resume JS avanza | M07.log y browser_30_pages.json en TEMP; sin portales | **PASS real** 57,67 s, 349,8 MiB; si falta binario, instalar Chromium; no omitir test y llamarlo PASS |
| M08 / HU-07 | ZIP/TAR fixture en RAM, límites pequeños | `Test-Datax M08 @('tests/test_archive_extractor.py','tests/test_async_discovery_integration.py')` | Detecta hijos/zip-bomb/traversal sin extraer archivos binarios; solo URLs/metadatos y JSON | M08.log, TEMP; comprimidos fixture en memoria | **PASS**; ante límite, conservar motivo y no ampliar presupuesto ni extraer a disco |
| M09 / HU-01,20 | API JSON local o TestClient | `Test-Datax M09 @('tests/test_url_normalizer.py','tests/test_catalog_api.py')`; ejecutar POST mapping mostrado arriba | URL sin fragmento/tracking, SHA-256 de URL estable, modo offline; payload inválido 422 | M09.log; no red al recurso ni mutación de catálogo | **PASS funcional**; contradicción dataclasses/Pydantic sigue PARTIAL en HU-01 |
| M10 / HU-02 | Reloj simulado y secuencias controladas | `Test-Datax M10 @('tests/test_domain_health_and_cadence.py','tests/test_continuation_catalog_safety.py')` | 3 fallos suspenden; tiempo solo no inventa fallo; parcial/FAILED/vacío rompe racha; elegibilidad respetada | M10.log, TEMP; no causa fallos públicos | **PASS**; revisar estados/fechas de observación, no forzar ACTIVE manualmente |
| M11 / HU-17 | Mismo fixture, sin esperar semanas reales | `Test-Datax M11 @('tests/test_domain_health_and_cadence.py','tests/test_continuation_catalog_safety.py')` | Cinco SUCCESS+NO_CHANGE → semanal; cuatro semanas → mensual; CHANGED resetea; replay no suma | M11.log; reloj fixture | **PASS funcional / aprobación formal pendiente**; decisiones 1.1/1.2 no se suponen aprobadas |
| M12 / HU-19 | Recursos sintéticos con perfiles comparables | `Test-Datax M12 @('tests/test_continuation_catalog_safety.py','tests/test_local_json_adapter.py','tests/test_discovery_checkpoint_resume.py')` | NEW/UNCHANGED/MODIFIED/REMOVED; tres ausencias completas comparables; replay, parcial, fallo, resume y cambio de scope no eliminan; reaparición resetea | M12.log, snapshots/observaciones TEMP | **PASS**; inspeccionar fingerprint/coverage antes de atribuir una eliminación |
| M13 / HU-18 | Transporte lento/errores simulados | `Test-Datax M13 @('tests/test_source_runtime_deadline.py','tests/test_async_http_client.py','tests/test_async_discovery_integration.py')` | Deadline/presupuesto preserve RAW; PARTIAL si hubo evidencia actual, FAILED si solo RAW viejo; cancelación externa no se disfraza | M13.log, cursores TEMP; no agota portales | **PASS funcional / defaults PARTIAL**; mantener 250/depth2/900s hasta decisión sobre 500; cierre browser tiene límites I/O propios |
| M14 / alias | Dos alias compatibles, hold FDTA y evidencia vieja sintética | `Test-Datax M14 @('tests/test_physical_acquisition_reuse.py','tests/test_b13_preserved_packages_and_identity.py','tests/test_remediation_and_bulk_projection_cli.py')` | Menos adquisiciones solo con evidencia compatible; source/run originales conservados; FDTA no ejecuta/copia/proyecta aunque tenga históricos | M14.log, TEMP; ningún portal ICCO consultado | **PASS**; identidad FDTA requiere dueño de fuentes, no eliminar hold por conveniencia |
| M15 / HU-14 | **Aislar primero**: M17 proyecto nuevo y M16 base manualDb nueva, nunca base productiva | Ejecutar bloque «Mongo real: índices y duplicados» debajo; guardar stdout antes/después del replay M16 | Siete colecciones; índices únicos/secundarios; cada duplicado da 11000; last_seen_at=fecha de observación y conteos estables tras replay | Mongo nuevo y M15.log; intenta insert duplicado en base de ensayo, no borra nada | **PENDIENTE DE PRUEBA MANUAL — BLOCKED**; si no hay daemon/ping, registrar error y continuar independientes; fakes aprobados no sustituyen esto |
| M16 / JSON+Mongo | **Aislar primero** M17 y $manualDb nueva; fixture M19 | Bloque «Sync JSON → Mongo» anterior, --check y dos --commit iguales; ejecutar M15 entre commits | Sin requests web; segunda importación idempotente, mismos IDs/conteos/last_seen_at; JSON original sin cambios | Mongo aislado, consola y archivos alpha intactos | **PASS offline; Mongo PENDIENTE DE PRUEBA MANUAL**; si preflight rechaza, revisar coherencia, no fabricar run/snapshot |
| M17 / HU-15 | Docker Desktop Linux operativo y puertos libres; aislamiento anterior | Ejecutar bloque Docker completo; abrir `http://127.0.0.1:8018/docs` y `/openapi.json`; comparar documentos tras stop/up | mongo healthy, API 200, build separado; medir <5s; batch --help termina; volumen conserva datos | Compose proyecto nuevo/volumen propio, logs/cronómetro; nunca down -v | **PENDIENTE DE PRUEBA MANUAL — BLOCKED**; error actual pipe inexistente; iniciar Docker Linux con DevOps, no resetear volúmenes |
| M18 / HU-12,20 | M19 + API local o fixtures | `Test-Datax M18 @('tests/test_catalog_api.py','tests/test_mongo_query_and_replay_contract.py')`; bloque consultas REST anterior | Arrays y filtros antes de paginar; 200/404/422/405, health 503 por repositorio corrupto aislado; sin fuga de URI/path | M18.log y respuestas HTTP; 503 solo fixture, no corromper catálogo real | **PASS** ASGI/JSON; fallo de servicio requiere revisar logs, no tratar como [] válido |
| M19 / B13 integral | Loopback libre, .venv; **solo fixture aislado** | `Test-Datax M19 @('tests/test_b13_local_process_e2e.py')`; usar rutas FIXTURE_CATALOG/FIXTURE_PIPELINE_STATE impresas | Primer RC3, resume RC0/READY, RAW→legacy→ZIP, FDTA retenida, TRANSTATS job, ids separados y catálogo fuera de output | M19.log y $manualRoot/M19; subprocess reales sin sitios externos | **PASS**; ante fallo conservar TEMP/logs, no reutilizar b13-e2e ni su checkpoint |
| M20 / HU-16,17 | Fixture de elegibilidad; scheduler solo DryRun sin tarea | `Test-Datax M20 @('tests/test_continuation_cli_flows.py','tests/test_scheduler_hu16.py')`; `powershell -NoProfile -File ./ops/run_scheduled_batch.ps1 -SourceId finrural -DryRun` | Batch sin --force omite no elegibles; DryRun no llama Docker; si permitido, dos terminales con -HoldLockSeconds 10 deben mostrar HU16_ALREADY_RUNNING en segunda | M20.log; DryRun crea logs/scheduler solamente; no registrar tarea | **PASS elegibilidad/estático; DryRun PENDIENTE DE PRUEBA MANUAL** por ExecutionPolicy; no cambiar política de máquina ni registrar scheduler |
| M21 / HU-22 | **Aislar primero** helper TEMP; tres fuentes fixture, sin portal | `Test-Datax M21 @('tests/test_resilience_interruption.py','tests/test_discovery_checkpoint_resume.py')` | Test mata proceso durante fuente 3; 1 y 2 siguen consultables en nueva API; cursor en vuelo se reencola, nunca SUCCESS inventado | M21.log, JSON TEMP, proceso hijo terminado por test | **PASS JSON; Mongo/contenedor PENDIENTE DE PRUEBA MANUAL**; no ejecutar kill contra servicios/DB históricas |
| M22 / HU-21 | Evidencia pública ya capturada, configs reales | `Test-Datax M22 @('tests/test_sources_config.py')`; abrir audit/20261022_autonomous_execution.json → mefp_tls y mefp_http_diagnostic; revisar las cinco iniciales en inventario | FINRURAL/BBV canarios parciales; ASFI/BCB histórico, no éxito actual supuesto; MEFP robots/TLS bloqueado y status-only conservado | M22.log y JSON; solo lectura de evidencia | **FAIL HU-21 / bloqueo externo**; responsable fuente/DevOps debe resolver confianza TLS y validar cinco, nunca verify=False |
| M23 / QA global | dev extras y carpeta nueva para cobertura | Ejecutar bloque «QA final» anterior con --cov=src --cov=apps --cov-fail-under=80 | 687 PASS, 1 SKIP y dos warnings en esta revisión; global 80,34 %; browser opt-in separado | Coverage JSON/term-missing en TEMP; no excluir apps ni confundir núcleo | **PASS gate**; si falla tras otro cambio, revisar líneas/casos, no bajar umbral |
| M24 / Git/no destrucción | Baseline y manifiesto existentes | `git status --short`; `git diff --check`; `git diff --cached --name-only`; `git log -1 --format=%H`; comparar hashes del manifiesto | Misma rama/HEAD, índice vacío; históricos y 48 candidatos intactos, sin borrados; nombres listados no son basura | Consola y audit JSON; solo lectura | **PASS**; safe.directory solo por -c si es necesario; no clean/reset/add-A/commit/push |
| M25 / integración | M19 y API JSON local; compañero con Swagger | Seguir los diez pasos de «Checklist para el compañero» y bloque REST exacto; `Test-Datax M25 @('tests/test_b13_local_process_e2e.py','tests/test_catalog_api.py','tests/test_continuation_catalog_safety.py')` | Fuente, dos recursos alpha, runs/reportes, observaciones parcial/completa; REMOVED visible en fixture de reconciliación; legacy y job diferenciados | M25.log, JSON y REST; lecturas, sin frontend/Bridge inventado | **PASS contrato; aceptación integrador pendiente**; si frontend tiene CORS, coordinar proxy/origen antes de exponer servicio |
| M26 / global | Todos los prerrequisitos del Batch E del mandato, telemetría por fuente y ventana/capacidad verificadas | **No ejecutar un global como parte de este checklist.** Revisar global_validation en audit/20261022_autonomous_execution.json y la tabla final | NO EJECUTADA; no hay tiempo total nuevo ni 45 resultados inventados. Los ~113 min del anexo son históricos | Solo lectura; permiso global no utilizado | **PENDIENTE DE PRUEBA MANUAL — BLOCKED por prerrequisitos**; preparar medición/ventana y revisar los gates antes de cualquier corrida global |

Conectar las rutas M19 con los bloques anteriores, sin adivinar el nombre truncado de pytest:

```powershell
$fixtureCatalog = (Select-String -Path (Join-Path $manualRoot 'M19.log') -Pattern '^FIXTURE_CATALOG=(.+)$').Matches[0].Groups[1].Value
$fixtureState = (Select-String -Path (Join-Path $manualRoot 'M19.log') -Pattern '^FIXTURE_PIPELINE_STATE=(.+)$').Matches[0].Groups[1].Value
# La carpeta aislada y output son hermanas en el directorio del fixture.
$fixtureTestDir = Split-Path $fixtureCatalog -Parent
$evidenceDir = Join-Path $fixtureTestDir 'output/checkpointed/local_e2e/alpha'
$env:PROSPECTOR_OUTPUT_DIR = $fixtureCatalog
Get-Content -LiteralPath $fixtureState -Raw | ConvertFrom-Json
& $py -m apps.catalog_sync.main --evidence-dir $evidenceDir --check
```

Este bloque obtiene las rutas exactas de cada sesión M19. Ejecutarlo antes de los bloques API/sync, en la misma sesión PowerShell. Si pytest no imprimió esas dos líneas, M19 no terminó correctamente: revisar M19.log. No iniciar la API con una variable vacía, porque usaría el catálogo por defecto.

**Mongo real: índices, timestamps y duplicados (M15).** Mutación limitada a la base nueva `$manualDb` de M16, sobre el proyecto `dc` aislado. Ejecutar después del primer commit M16 y repetir consultas después del segundo. No se ejecutó en este entorno:

```powershell
@'
const names=["sources","runs","discovered_urls","resource_candidates","snapshots","source_observations","run_reports"];
for (const n of names) {
  print(n+" count="+db[n].countDocuments({})); printjson(db[n].getIndexes());
  const x=db[n].findOne({}); if(!x) throw Error("Sin fixture en "+n); delete x._id;
  try {db[n].insertOne(x); throw Error("No se rechazó duplicado en "+n)}
  catch(e) {if(e.code!==11000) throw e; print("DUPLICATE_11000 "+n)}
}
printjson(db.discovered_urls.find({}, {source_id:1,normalized_url:1,discovered_at:1,last_seen_at:1}).toArray());
printjson(db.resource_candidates.find({}, {source_id:1,resource_key:1,catalog_current:1,change_status:1}).toArray());
'@ | docker compose -p $composeProject -f docker-compose.yml -f $composeOverride exec -T mongo mongosh $manualDb --quiet |
  Tee-Object -FilePath (Join-Path $manualRoot 'M15.log')
```

Verificar last_seen_at contra discovered_at del intento importado, no contra la hora del replay. La API del contenedor usa su base por defecto `prospector_externo`; M16 importa deliberadamente a `$manualDb` distinta para evitar mutación accidental. No esperar ver esa base alternativa en `/catalog` sin configurar un servicio de ensayo para ella; usar mongosh para M15/M16. Aún falta el ensayo real de corte entre escrituras Mongo y recuperación; el fixture JSON M21 no lo certifica.


</details>

## Anexo: cierre histórico conservado

<details>
<summary>Entrega b13-e2e anterior; cifras y afirmaciones históricas, no certificación actual</summary>

### Entrega histórica del Prospector Externo DATAX


## Estado final

La ejecución reproducible `b13-e2e` quedó cerrada correctamente.

- Inventario: **52 fuentes lógicas**
- Operacionales: **41**
- Status-only: **11**
- `DATAX_READY`: **38**
- `ACQUISITION_JOB_READY`: **1** (TRANSTATS)
- `EXTERNAL_BLOCKER`: **2** (MHE y SIGMA)
- Pendientes: **0**
- Unresolved: **0**
- Registros legacy producidos: **12104**
- JSON DATAX finales: **38**
- Duplicados cross-physical: **0**
- ZIP SHA-256: `f52bf8d1f62d887420517f3d60dd8911f9d3ca5ebf258d5cf58f8369f7326e70`

## Tiempo de la corrida final

- Tiempo activo acumulado de crawl: **1 h 51 min 21 s**
- Ventana desde el primer inicio hasta el último fin de crawl: **1 h 52 min 13 s**
- Ventana desde el primer crawl hasta el estado B13 `READY`: **1 h 52 min 59 s**
- Proyección/empaquetado final posterior al último crawl: **45 s**

Para el informe, la métrica más representativa del tiempo de corrida completa
es **1 h 52 min 59 s**, mientras que la suma del
tiempo efectivo dedicado por las fuentes al crawling fue
**1 h 51 min 21 s**.

## Alcance real del crawling

La ejecución final **no fue un rastreo ilimitado de todas las páginas de todos
los sitios**. El Prospector utiliza límites operacionales para evitar spider
traps, calendarios infinitos, explosión de parámetros y consumo de red sin
control.

Fuentes que terminaron por un `MAX_*`: **8**.

Stop reasons observados:

`{"API_SEEDS_EXHAUSTED": 2, "BROWSER_PAGE_LIMIT": 1, "CUSTOM_FORM_CONFIRMED": 1, "MAX_CONSECUTIVE_ERRORS": 7, "MAX_URLS": 1, "QUEUE_EXHAUSTED": 14, "REQUEST_BUDGET_REACHED": 15}`

La redacción recomendada para el informe es:

> Se ejecutó un crawleo final reproducible sobre las 41 fuentes operacionales,
> aplicando las políticas de cobertura, seguridad y límites de exploración
> configuradas en el Prospector.

## Fuentes con mayor duración

- `dgac`: 7 min 29 s (`REQUEST_BUDGET_REACHED`)
- `cadexco`: 6 min 11 s (`MAX_CONSECUTIVE_ERRORS`)
- `asofin`: 5 min 45 s (`REQUEST_BUDGET_REACHED`)
- `ine`: 5 min 40 s (`REQUEST_BUDGET_REACHED`)
- `snis`: 5 min 28 s (`QUEUE_EXHAUSTED`)
- `ada`: 4 min 20 s (`REQUEST_BUDGET_REACHED`)
- `mdryt`: 4 min 17 s (`REQUEST_BUDGET_REACHED`)
- `data_gov`: 4 min 16 s (`REQUEST_BUDGET_REACHED`)
- `mdryt_oap`: 4 min 14 s (`REQUEST_BUDGET_REACHED`)
- `cndc`: 4 min 12 s (`REQUEST_BUDGET_REACHED`)

## Artefactos definitivos

Resultados DATAX:

`output/b13-pipeline/b13-e2e/datax-package/latest/`

Paquete comprimido:

`output/b13-pipeline/b13-e2e/datax-package/datax_package.zip`

Evidencia raw completa:

`output/checkpointed/b13-e2e/`

Carpeta simplificada de entrega:

`output/final-delivery/`

El entry point empresarial es `manifest.json`; para una fuente
`DATAX_READY`, el artefacto legacy está en
`sources/<logical_source_id>.json`.

</details>
