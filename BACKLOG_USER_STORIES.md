# Backlog Oficial del Proyecto: Prospector Externo DATAX

> **Documento Base:** [`documento-arquitectura-prospector-externo.md`](documento-arquitectura-prospector-externo.md)  
> **Estándar Ágil:** Atlassian Agile User Stories + Criterios INVEST + DoR / DoD transversales  
> **Roles / Personas del Sistema:**  
> - 🧑‍💻 **Analista de Datos / Equipo DATAX:** Necesita inventario bruto confiable, sin binarios en disco y con trazabilidad de fuentes financieras/regulatorias.  
> - 🔄 **Sistema Consumidor "Reportes Perdidos":** Necesita consultar recursos descubiertos, reportes de corrida y observar cambios históricos vía API REST.  
> - 🛡️ **Operador de Infraestructura / SRE:** Requiere resiliencia, protección contra caídas por falta de memoria (OOM), límites de concurrencia y despliegues reproducibles.  
> - ⚖️ **Oficial de Cumplimiento / Auditor:** Requiere justificación auditable de omisiones de `robots.txt` y trazabilidad completa de cada corrida (`run_id`).

---

## Definiciones Transversales

### Definition of Ready (DoR) Estándar
Toda historia de usuario debe cumplir las siguientes condiciones antes de ingresar a un Sprint:
1. Formato canónico explícito (`Como [persona] quiero [acción] para [beneficio]`).
2. Persona identificada con su necesidad de negocio o técnica justificada.
3. Evaluación de criterios **INVEST** superada (Independiente, Negociable, Valiosa, Estimable, Pequeña, Testeable).
4. Criterios de Aceptación no ambiguos y testeables (Gherkin o checklist de validación).
5. Contratos de datos (DTOs, schemas JSON o firmas de interfaces) acordados.
6. Dependencias técnicas identificadas y bloqueos resueltos.
7. Estimación de esfuerzo/complejidad acordada por el equipo técnico.

### Definition of Done (DoD) Estándar
Toda historia de usuario debe cumplir las siguientes condiciones para considerarse terminada:
1. Código desarrollado conforme al estilo arquitectónico (Monolito Modular con Arquitectura Hexagonal y Microkernel).
2. Cobertura de pruebas unitarias y de integración mínima (>= 80%) en verde sin regresiones.
3. Sin almacenamiento persistente de binarios en disco (PDF, XLSX, ZIP) — solo inspección transitoria en memoria RAM.
4. Trazabilidad garantizada mediante inclusión de `source_id`, `run_id` y timestamps en los eventos registrados.
5. Tipado estricto con Python (`dataclasses` / `Pydantic`) y manejo explícito de errores y excepciones.
6. Documentación técnica y esquemas actualizados en el repositorio.
7. Pull Request revisado y aprobado por pares técnicos (Arquitecto / Team Lead).

---

## Resumen del Backlog

| ID | Título de la Historia de Usuario | Épica / Capa | Estado |
|---|---|---|:---:|
| **HU-01** | Modelado de Dominio e Invariantes del Prospector | Dominio Puro | ✅ IMPLEMENTADA |
| **HU-02** | Máquina de Estados de Salud y Cadencia Adaptativa | Dominio Puro | ✅ IMPLEMENTADA |
| **HU-03** | Microkernel de Workflows y Registro de Estrategias | Kernel / Workflows | ✅ IMPLEMENTADA |
| **HU-04** | Estrategia de Descubrimiento para Portales HTML Estáticos | Workflows | ✅ IMPLEMENTADA |
| **HU-05** | Extracción de Referencias en Comentarios HTML | Workflows | ✅ IMPLEMENTADA |
| **HU-06** | Workflows Base para APIs y Procedimientos Atípicos | Workflows | ✅ IMPLEMENTADA |
| **HU-07** | Inspección Transitoria de Archivos Comprimidos en Memoria | Infraestructura | ✅ IMPLEMENTADA |
| **HU-08** | Concurrencia Asíncrona con Aislamiento Bulkhead | Infraestructura | ✅ IMPLEMENTADA |
| **HU-09** | Cliente HTTP Resiliente con Robots.txt y Fallback HEAD→GET | Adaptadores HTTP | ✅ IMPLEMENTADA |
| **HU-10** | Servicio de Catálogo y Checkpointing Incremental | Aplicación / Catálogo | ✅ IMPLEMENTADA |
| **HU-11** | Orquestador de Corridas Batch y Constructor de Reportes | Aplicación / Batch | ✅ IMPLEMENTADA |
| **HU-12** | Fachada de Consulta REST de Solo Lectura | Aplicación / API | ✅ IMPLEMENTADA |
| **HU-13** | Driver Playwright Anti-OOM y Workflow de Contenido Dinámico | Workflows / Browser | ⏳ PENDIENTE |
| **HU-14** | Persistencia en MongoDB de Producción con Indexación | Persistencia / BD | ⏳ PENDIENTE |
| **HU-15** | Contenedorización Dual (Batch Efímero y API Persistente) | Infraestructura / DevOps | ⏳ PENDIENTE |
| **HU-16** | Scheduler Técnico para Corridas Batch Oportunistas | Infraestructura / Batch | ⏳ PENDIENTE |
| **HU-17** | Política Numérica de Transición Dinámica de Cadencia | Aplicación / Cadencia | ⏳ PENDIENTE |
| **HU-18** | Límites Globales de Recorrido y Timeouts de Seguridad | Dominio / Aplicación | ⏳ PENDIENTE |
| **HU-19** | Política de Confirmación para Recursos Removidos | Aplicación / Catálogo | ⏳ PENDIENTE |
| **HU-20** | Estandarización de Contrato para `/mapping/inspect` | API / Integración | ⏳ PENDIENTE |
| **HU-21** | Configuración e Integración de Fuentes del Backlog Inicial | Configuración / Operaciones | ⏳ PENDIENTE |
| **HU-22** | Prueba de Resiliencia ante Caída Forzada de Proceso | QA / Resiliencia | ⏳ PENDIENTE |

---

# ÉPICA 1: Núcleo de Dominio y Puertos Hexagonales (Arquitectura Base)

### HU-01: Modelado de Dominio e Invariantes del Prospector
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Totalmente desacoplada de bases de datos o frameworks web.
  * **N:** Atributos abiertos a optimización de nombres y validaciones de tipos.
  * **V:** Garantiza la integridad de datos de URLs, recursos candidatos y snapshots.
  * **E:** Estimación baja (1 sprint), centrada en entidades puras de dominio.
  * **S:** Modelos y normalizador encapsulados en 2 archivos.
  * **T:** Verificable mediante tests unitarios de modelos y normalización.
* **Descripción:**
  > **Como** Analista de Datos de DATAX,  
  > **Quiero** que el sistema disponga de entidades de dominio estandarizadas con normalización de URLs y generación de hashes SHA-256 de mapeos,  
  > **Para** asegurar que todo recurso o URL descubierta sea unívocamente identificable, trazable por corrida y libre de variaciones cosméticas (fragmentos, protocolos inconsistentes o rutas relativas).
* **Definition of Ready (DoR):**
  - [x] Especificación de entidades de la Sección 10.3 del documento de arquitectura.
  - [x] Reglas de normalización de URLs definidas (eliminación de `#`, estandarización de esquemas y dominio).
* **Definition of Done (DoD):**
  - [x] Modelos creados (`Source`, `Run`, `DiscoveredUrl`, `ResourceCandidate`, `Snapshot`, `RunReport`).
  - [x] Implementación pura sin dependencias externas (solo standard library / `dataclasses`).
  - [x] Tests unitarios en `test_domain_models.py` y `test_url_normalizer.py` pasando al 100%.
* **Criterios de Aceptación:**
  - **Criterio 1:** Dadas dos URLs idénticas pero una con `#seccion` o protocolo mixto, `UrlNormalizer` debe retornar exactamente la misma URL normalizada.
  - **Criterio 2:** El cálculo de hash SHA-256 sobre el mapeo de un recurso debe ser determinista e independiente de metadatos volátiles.

---

### HU-02: Máquina de Estados de Salud y Cadencia Adaptativa
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Gestiona el estado de una fuente sin requerir la ejecución de scraping.
  * **N:** Las reglas de transición pueden ajustarse según la tolerancia del negocio.
  * **V:** Evita la sobrecarga de fuentes con fallos persistentes y programa ejecuciones según la vigencia esperada.
  * **E:** Modelado matemático claro con enums y lógica de transición.
  * **S:** Alcance acotado a máquinas de estado en Python puro.
  * **T:** Pruebas unitarias de transiciones y cálculo de fechas.
* **Descripción:**
  > **Como** Operador de Infraestructura,  
  > **Quiero** una máquina de estados para la salud de las fuentes (`ACTIVE`, `WARNING`, `SUSPENDED`) y un gestor de cadencia (`DAILY`, `WEEKLY`, `MONTHLY`),  
  > **Para** aislar fallos recurrentes (marcando fuentes como `SUSPENDED` tras 3 errores consecutivos) y asegurar que solo las fuentes elegibles consuman tiempo de CPU en cada corrida.
* **Definition of Ready (DoR):**
  - [x] Reglas de transición de salud definidas en la Sección 11.2 del documento de arquitectura.
  - [x] Distinción obligatoria entre estado de salud y resultado de contenido.
* **Definition of Done (DoD):**
  - [x] Entidades `HealthState` y `CadenceState` implementadas en `domain/health.py` y `domain/cadence.py`.
  - [x] Tests unitarios exhaustivos en `test_domain_health_and_cadence.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Si una fuente acumula 3 fallos consecutivos de ejecución, su estado de salud pasa automáticamente a `SUSPENDED`.
  - **Criterio 2:** Un resultado `SUCCESS` con estado de contenido `NO_CHANGE` no debe considerarse como fallo de salud.

---

# ÉPICA 2: Microkernel de Workflows y Estrategias de Extracción

### HU-03: Microkernel de Workflows y Registro de Estrategias
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** El registro no depende de las implementaciones concretas de cada workflow.
  * **N:** Permite registrar nuevos workflows en runtime o configuración.
  * **V:** Habilita el desacoplamiento de estrategias de exploración (Microkernel Pattern).
  * **E:** Patrón Factory / Registry simple y claro.
  * **S:** Un puerto abstracto (`SourceWorkflow`) y un `WorkflowRegistry`.
  * **T:** Test de registro y resolución por string.
* **Descripción:**
  > **Como** Arquitecto de Software,  
  > **Quiero** un contrato abstracto `SourceWorkflow` y un registro `WorkflowRegistry`,  
  > **Para** que cada fuente ejecute su estrategia de exploración declarada en YAML sin acoplar el orquestador principal a detalles de parsing de sitios web.
* **Definition of Ready (DoR):**
  - [x] Definición del contrato `run(source_config) -> ExtractionResult` en Sección 5.3 y 7.1.
* **Definition of Done (DoD):**
  - [x] Puerto `SourceWorkflow` implementado en `kernel/workflow_port.py`.
  - [x] `WorkflowRegistry` implementado en `kernel/registry.py`.
  - [x] Tests unitarios en `test_workflow_registry.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Al solicitar un workflow registrado por su clave (`html`, `javascript`), el registro debe instanciar y devolver la estrategia correcta.
  - **Criterio 2:** Si se solicita un workflow desconocido, el registro debe lanzar una excepción controlada `WorkflowNotFoundError`.

---

### HU-04: Estrategia de Descubrimiento para Portales HTML Estáticos (`HtmlWorkflow`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Funciona independientemente de navegadores pesados usando solicitudes HTTP directas.
  * **N:** Profundidad y selectores de filtrado parametrizables.
  * **V:** Cubre la gran mayoría de portales financieros e institucionales con bajo costo de cómputo.
  * **E:** Parsing con BeautifulSoup / lxml estándar.
  * **S:** Flujo de exploración recursiva acotada.
  * **T:** Testable con mocks HTTP y con el portal real de FINRURAL.
* **Descripción:**
  > **Como** Analista de Datos de DATAX,  
  > **Quiero** un workflow especializado en HTML estático que descubra enlaces, páginas y recursos candidatos (.pdf, .xlsx, etc.) navegando a través de semillas focalizadas (`seeds`),  
  > **Para** registrar el inventario documental completo de fuentes como FINRURAL sin incurrir en la sobrecarga de memoria de un navegador completo.
* **Definition of Ready (DoR):**
  - [x] Caracterización de FINRURAL y patrón documental identificado en `document_project.md`.
  - [x] Soporte de `seeds` en el esquema de configuración de fuentes.
* **Definition of Done (DoD):**
  - [x] `HtmlWorkflow` implementado en `workflows/html_workflow.py`.
  - [x] Integración con `ResilientHttpClient` y `ArchiveExtractor`.
  - [x] Validado mediante ejecución de benchmark contra FINRURAL.
* **Criterios de Aceptación:**
  - **Criterio 1:** Dado el seed de FINRURAL, el workflow debe descubrir todos los enlaces a PDFs mensuales sin almacenar ningún archivo binario en disco.
  - **Criterio 2:** Las rutas excluidas configuradas en `excluded_path_keywords` no deben ser añadidas a la cola de navegación.

---

### HU-05: Extracción de Referencias en Comentarios HTML (`CommentedHtmlWorkflow`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Especialización de workflow utilizable en cualquier fuente que oculte URLs en comentarios.
  * **N:** Estrategia de regex y extracción en comentarios ajustable.
  * **V:** Recupera enlaces a información financiera que otros crawlers convencionales ignoran (ej. BBV).
  * **E:** Requiere regex y parser DOM sobre nodos `Comment`.
  * **S:** Hereda o reutiliza la lógica base de exploración HTML.
  * **T:** Test unitario con HTML fixture conteniendo comentarios con etiquetas `<a>`.
* **Descripción:**
  > **Como** Analista de Datos de DATAX,  
  > **Quiero** un workflow que analice tanto el DOM visible como los comentarios HTML (`<!-- ... -->`),  
  > **Para** descubrir URLs de publicaciones financieras y reportes que portales como la Bolsa Boliviana de Valores (BBV) mantienen temporalmente comentados.
* **Definition of Ready (DoR):**
  - [x] Análisis del comportamiento de la BBV documentado en la Sección 7.2 y `config/sources.yaml`.
* **Definition of Done (DoD):**
  - [x] `CommentedHtmlWorkflow` implementado en `workflows/commented_html.py`.
  - [x] Extracción de enlaces en comentarios y asociación de metadata `discovery_method="commented_html"`.
  - [x] Pruebas automatizadas y validación real contra BBV ejecutadas.
* **Criterios de Aceptación:**
  - **Criterio 1:** Todo enlace encontrado dentro de un bloque `<!-- <a href="..."> -->` debe normalizarse e incorporarse a los recursos descubiertos marcando su evidencia.

---

### HU-06: Workflows Base para APIs y Procedimientos Atípicos (`ApiWorkflow` & `CustomWorkflow`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** No afectan la operativa de los workflows HTML estándar.
  * **N:** Métodos plantilla (`Template Method`) extensibles para casos de esquina.
  * **V:** Garantiza que el sistema sea extensible ante fuentes modernas (APIs REST) o legadas (ASP.NET).
  * **E:** Clases base y contratos de extensión limpios.
  * **S:** Estructuras mínimas implementadas en el microkernel.
  * **T:** Tests unitarios de herencia y despacho.
* **Descripción:**
  > **Como** Arquitecto de Software,  
  > **Quiero** disponer de las estrategias `ApiWorkflow` y `CustomWorkflow` en el registro,  
  > **Para** soportar fuentes cuyos catálogos se expongan mediante endpoints JSON o requieran secuencias procedimentales atípicas (ej. tokens de sesión o flujos ASP.NET).
* **Definition of Ready (DoR):**
  - [x] Secciones 7.2 y 7.3 del documento de arquitectura.
* **Definition of Done (DoD):**
  - [x] Implementación en `workflows/api_workflow.py` y `workflows/custom_workflow.py`.
  - [x] Cumplimiento del contrato `SourceWorkflow`.

---

# ÉPICA 3: Resiliencia, Concurrencia Bulkhead e Inspección en Memoria

### HU-07: Inspección Transitoria de Archivos Comprimidos en Memoria (`ArchiveExtractor`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Componente de utilidad desacoplado del protocolo de transporte.
  * **N:** Tipos de compresión soportados ampliables (`.zip`, `.tar`, `.gz`).
  * **V:** Permite inventariar los documentos reales (.pdf, .xlsx) contenidos en zips sin almacenar binarios en disco.
  * **E:** Uso de `io.BytesIO`, `zipfile` y `tarfile` en memoria.
  * **S:** Clase única con interfaz puntual.
  * **T:** Testable mediante fixtures en memoria de archivos comprimidos.
* **Descripción:**
  > **Como** Oficial de Cumplimiento y Administrador de Almacenamiento,  
  > **Quiero** que cuando se descubra un recurso `.zip` o `.tar`, el sistema descargue transitoriamente el flujo de bytes a la memoria RAM para listar sus archivos internos e inmediatamente liberar la memoria,  
  > **Para** registrar el inventario granular de documentos contenidos sin violar el principio arquitectónico de "cero almacenamiento de binarios en disco".
* **Definition of Ready (DoR):**
  - [x] Principio fundamental definido en Sección 2.3 y 7.4.
  - [x] Límite seguro de descompresión para prevenir bombas de compresión (*zip bombs*).
* **Definition of Done (DoD):**
  - [x] `ArchiveExtractor` implementado en `infrastructure/archive_extractor.py`.
  - [x] Soporte para `.zip`, `.tar`, `.tgz`, `.tar.gz`.
  - [x] Tests unitarios exhaustivos en `test_archive_extractor.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Al procesar un ZIP en memoria con 5 PDFs adentro, deben crearse 5 `ResourceCandidate` hijos con su metadata interna.
  - **Criterio 2:** En ningún momento del ciclo de vida de la descompresión debe escribirse ningún archivo en el sistema de archivos del host.

---

### HU-08: Concurrencia Asíncrona con Aislamiento Bulkhead (`AsyncWorkerPool`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Controla la infraestructura de ejecución independientemente del negocio.
  * **N:** Capacidad de workers HTTP y Browser configurable en YAML o variables de entorno.
  * **V:** Previene que tareas lentas de navegación bloqueen la exploración masiva de páginas HTTP y evita el agotamiento de CPU/RAM.
  * **E:** Patrón Bulkhead sobre `asyncio.Semaphore` y `asyncio.Queue`.
  * **S:** Pool de concurrencia acotado.
  * **T:** Tests asíncronos de límites de concurrencia.
* **Descripción:**
  > **Como** Operador de Infraestructura,  
  > **Quiero** un pool asíncrono con separación estricta (Bulkhead) entre tareas HTTP livianas y workers de navegador pesados,  
  > **Para** garantizar que la ejecución paralela sea estable y no sature la memoria del contenedor batch.
* **Definition of Ready (DoR):**
  - [x] Sección 8 y 12.2 del documento de arquitectura.
* **Definition of Done (DoD):**
  - [x] `AsyncWorkerPool` implementado en `infrastructure/concurrency.py`.
  - [x] Semáforos independientes para workers HTTP y de navegador.
  - [x] Tests de concurrencia en `test_async_worker_pool.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Si se envían 10 tareas de navegador con un límite de 2, nunca deben ejecutarse más de 2 simultáneamente.

---

### HU-09: Cliente HTTP Resiliente con Robots.txt y Fallback HEAD→GET (`ResilientHttpClient`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Adaptador saliente estándar para llamadas HTTP.
  * **N:** Políticas de reintentos y timeouts ajustables.
  * **V:** Protege a los portales estatales contra denegación de servicio y maneja servidores con implementaciones HTTP defectuosas.
  * **E:** Basado en `httpx` asíncrono con middlewares de rate limiting.
  * **S:** Cliente HTTP encapsulado.
  * **T:** Pruebas con servidor mock simulando errores 405, 429, timeouts y robots.txt.
* **Descripción:**
  > **Como** Operador de Infraestructura y Oficial de Cumplimiento,  
  > **Quiero** un cliente HTTP con rate limiting por dominio, reintentos exponenciales, respeto a `robots.txt` (con soporte de override justificado) y fallback de `HEAD` a streaming `GET`,  
  > **Para** extraer metadatos de recursos de forma ética y tolerar portales estatales que bloquean indebidamente métodos HEAD (error 405/501).
* **Definition of Ready (DoR):**
  - [x] Especificación de resiliencia de la Sección 12.2 y códigos de fallo de la Sección 12.4.
* **Definition of Done (DoD):**
  - [x] `ResilientHttpClient` implementado en `infrastructure/http_client.py`.
  - [x] Parser de robots.txt con bypass auditable si `ignore_robots_txt: true` con `robots_override_reason`.
  - [x] Fallback automático de `HEAD` a streaming mínimo `GET` ante 405/501.
* **Criterios de Aceptación:**
  - **Criterio 1:** Si un servidor responde `405 Method Not Allowed` a un `HEAD`, el cliente debe realizar un `GET` en modo streaming para capturar cabeceras y abortar la descarga inmediatamente.
  - **Criterio 2:** Si la fuente tiene `ignore_robots_txt: false` y robots.txt bloquea la ruta, la petición debe rechazarse con código `ROBOTS_DISALLOWED`.

---

# ÉPICA 4: Orquestación Batch, Checkpointing Incremental y Reportes de Corrida

### HU-10: Servicio de Catálogo y Checkpointing Incremental (`CatalogApplicationService`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Orquesta la actualización del catálogo fuente por fuente de manera autónoma.
  * **N:** Reglas de comparación de hashes y detección de cambios negociables.
  * **V:** Garantiza que ante una falla o interrupción forzada del batch, no se pierda el progreso de las fuentes previamente finalizadas.
  * **E:** Lógica de comparación de conjuntos y persistencia por unidad de trabajo.
  * **S:** Servicio de aplicación centrado en una sola responsabilidad.
  * **T:** Test de interrupción simulada y verificación de snapshots persistidos.
* **Descripción:**
  > **Como** Operador del Sistema,  
  > **Quiero** que el servicio de catálogo compare el inventario descubierto contra el histórico y persista un checkpoint inmediatamente después de finalizar cada fuente,  
  > **Para** que los datos procesados queden salvados en la base de datos sin depender de que la corrida completa finalice con éxito.
* **Definition of Ready (DoR):**
  - [x] Secciones 10.1 y 10.2 del documento de arquitectura.
  - [x] Estados de cambio definidos: `NEW`, `UNCHANGED`, `MODIFIED`, `REMOVED`.
* **Definition of Done (DoD):**
  - [x] `CatalogApplicationService` implementado en `application/catalog_service.py`.
  - [x] Generación de `SourceRunObservation` por fuente.
  - [x] Tests de checkpointing y catálogo.
* **Criterios de Aceptación:**
  - **Criterio 1:** Si una corrida procesa la Fuente 1 exitosamente y luego se aborta en la Fuente 2, el snapshot y la observación de la Fuente 1 deben encontrarse persistidos e íntegros.

---

### HU-11: Orquestador de Corridas Batch y Constructor de Reportes (`RunOrchestrator`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Depende solo de los puertos de aplicación y del microkernel.
  * **N:** Formato del reporte estructurado adaptable.
  * **V:** Produce la evidencia final auditable de cada corrida (`RunReport`) requerida por DATAX y Reportes Perdidos.
  * **E:** Coordinación de componentes existentes mediante patrón Builder.
  * **S:** Flujo lineal de orquestación batch.
  * **T:** Test E2E de corrida batch con adaptadores en memoria.
* **Descripción:**
  > **Como** Sistema Consumidor "Reportes Perdidos",  
  > **Quiero** que el orquestador genere un `RunReport` estructurado en JSON al concluir una corrida batch oportunista,  
  > **Para** conocer exactamente cuántas fuentes fueron seleccionadas, cuántas fallaron, cuántas presentaron novedades (`CHANGED`) y cuántas no tuvieron cambios (`NO_CHANGE`).
* **Definition of Ready (DoR):**
  - [x] Flujo de corrida definido en la Sección 9 y estructura de reporte de la Sección 13.
* **Definition of Done (DoD):**
  - [x] Componentes `RunOrchestrator`, `ResultAggregator`, `RunReportBuilder` implementados.
  - [x] `apps/crawler_batch` ejecutable mediante CLI (`python -m apps.crawler_batch.main`).
  - [x] Tests unitarios y de orquestación en `test_orchestrator_hexagonal.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** El reporte de corrida debe incluir el desglose por fuente con `status`, `content_status`, `urls_discovered` y código de fallo si aplicara.
  - **Criterio 2:** No debe emitirse ninguna notificación por Slack o correo electrónico; el reporte debe quedar persistido en el repositorio correspondiente.

---

# ÉPICA 5: Exposición del Catálogo y API Facade

### HU-12: Fachada de Consulta REST de Solo Lectura (`Catalog API Facade`)
* **Estado:** ✅ **IMPLEMENTADA**
* **Criterios INVEST:**
  * **I:** Servicio desacoplado del batch, operable de forma 24/7.
  * **N:** Parámetros de paginación y filtros de consulta ajustables.
  * **V:** Permite la integración directa de DATAX y Reportes Perdidos para consultar el inventario sin acceder directamente a la base de datos.
  * **E:** Endpoints estándar de FastAPI.
  * **S:** Microservicio de solo lectura.
  * **T:** Tests automatizados con `TestClient` de FastAPI.
* **Descripción:**
  > **Como** Sistema Consumidor "Reportes Perdidos",  
  > **Quiero** consultar el catálogo de recursos, fuentes y reportes de corrida mediante una API REST protegida y de solo lectura,  
  > **Para** consumir inventarios actualizados sin capacidad de alterar o borrar datos del catálogo.
* **Definition of Ready (DoR):**
  - [x] Especificación de endpoints de la Sección 14.2 del documento de arquitectura.
  - [x] Principio de solo lectura estricto (Sección 14.3).
* **Definition of Done (DoD):**
  - [x] Rutas implementadas en `adapters/api/routes.py` y aplicación en `apps/catalog_api/main.py`.
  - [x] Endpoints implementados: `/catalog/resources`, `/catalog/sources`, `/catalog/sources/{id}`, `/runs`, `/runs/{id}`, `/runs/{id}/report`.
  - [x] Documentación interactiva OpenAPI (Swagger) operativa.
  - [x] Tests de API en `test_catalog_api.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Cualquier intento de enviar peticiones `POST`, `PUT` o `DELETE` a los endpoints de catálogo debe ser rechazado con `405 Method Not Allowed`.

---

# ÉPICA 6: Pendientes Técnicos, Infraestructura y Validaciones Reales

### HU-13: Driver Playwright Anti-OOM y Workflow de Contenido Dinámico (`JavascriptWorkflow`)
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Se ejecuta como un plugin aislado dentro del microkernel, sin alterar los flujos HTTP existentes.
  * **N:** Umbrales de reciclaje de proceso (15 páginas o 1 GB RAM) negociables según la memoria del servidor.
  * **V:** Es indispensable para extraer información de portales gubernamentales dinámicos basados en SPAs/JavaScript (ej. ASFI Boletines).
  * **E:** Requiere configurar Playwright en modo headless, intercepción de rutas y reciclaje de browser context. Estimable en 3-5 días.
  * **S:** Enfocada exclusivamente en el adaptador de navegador y la estrategia `JavascriptWorkflow`.
  * **T:** Testeable mediante suite de integración con servidor local que simule renderizado dinámico y mediciones de consumo de RAM.
* **Descripción:**
  > **Como** Operador de Infraestructura y Analista de DATAX,  
  > **Quiero** que el `JavascriptWorkflow` ejecute Chromium headless con aislamiento estricto de memoria (máximo 1-2 contextos concurrentes, bloqueo de imágenes/fuentes/multimedia y reciclaje de proceso tras 15 páginas o 1 GB de RAM),  
  > **Para** explorar exhaustivamente portales dinámicos (como ASFI) garantizando que el contenedor batch no sufra caídas por Out Of Memory (OOM).
* **Definition of Ready (DoR):**
  - [ ] Especificación de límites de memoria de la Sección 12.2 y Criterio de Aceptación 18.2 del documento de arquitectura.
  - [ ] Binarios de Chromium instalados en el entorno de pruebas (`playwright install chromium`).
  - [ ] Portal dinámico de prueba identificado con selectores y semillas en `config/sources.yaml`.
* **Definition of Done (DoD):**
  - [ ] `PlaywrightDriver` en `infrastructure/browser_driver.py` con bloqueo activo de requests para imágenes, stylesheets y fuentes.
  - [ ] Contador de páginas y verificador de RAM que fuerce `browser.close()` y reapertura limpia al alcanzar los umbrales.
  - [ ] `JavascriptWorkflow` en `workflows/javascript_workflow.py` validado end-to-end extrayendo URLs de páginas dinámicas.
  - [ ] Pruebas automatizadas de integración en `tests/test_javascript_workflow.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Durante la navegación de 30 páginas continuas, el consumo de memoria del proceso de Chromium no debe superar 1 GB de RAM ni generar excepciones OOM.
  - **Criterio 2:** El driver debe interceptar el tráfico y abortar solicitudes de tipo `image`, `media`, `font` y hojas de estilo no esenciales.
  - **Criterio 3:** Las URLs descubiertas tras eventos de renderizado JS deben normalizarse y registrarse con `discovery_method="javascript"`.

---

### HU-14: Persistencia en MongoDB de Producción con Indexación Optimizada
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Implementa el puerto existente `CatalogRepositoryPort` sin alterar el dominio ni la API.
  * **N:** Criterios de retención y opciones de réplica configurables mediante URI de conexión.
  * **V:** Habilita el almacenamiento duradero, concurrente y auditable de todo el inventario histórico del Prospector.
  * **E:** Basado en `Motor` / `PyMongo` con índices ya especificados en el documento de arquitectura.
  * **S:** Acotado al adaptador de persistencia y scripts de migración/indexación.
  * **T:** Pruebas de integración contra una instancia de MongoDB (Docker o testcontainer).
* **Descripción:**
  > **Como** Operador de Infraestructura y Desarrollador,  
  > **Quiero** conectar y validar el `MongoPersistenceAdapter` contra una base de datos MongoDB real con los índices únicos y de consulta aprovisionados,  
  > **Para** garantizar transacciones rápidas, evitar duplicados a nivel de base de datos y dar soporte a consultas de millones de URLs descubiertas.
* **Definition of Ready (DoR):**
  - [ ] Esquema de colecciones e índices de la Sección 16.1 del documento de arquitectura.
  - [ ] Instancia de MongoDB accesible en entorno de desarrollo/staging.
* **Definition of Done (DoD):**
  - [ ] `MongoPersistenceAdapter` en `adapters/persistence/mongo_adapter.py` completamente funcional para todas las operaciones de repositorio.
  - [ ] Script o método de inicialización que garantice la creación de índices obligatorios (`sources`, `runs`, `discovered_urls`, `resource_candidates`, `snapshots`, `source_observations`, `run_reports`).
  - [ ] Tests de integración en `tests/test_mongo_persistence_adapter.py` ejecutándose con éxito.
* **Criterios de Aceptación:**
  - **Criterio 1:** Al registrar una URL ya existente para la misma fuente, la base de datos debe actualizar el timestamp `last_seen_at` sin duplicar el registro en `discovered_urls`.
  - **Criterio 2:** El índice único en `runs.run_id` y `sources.source_id` debe rechazar colisiones de identificadores con error controlado.

---

### HU-15: Contenedorización Dual (Batch Efímero y API Persistente)
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** El empaquetado Docker es independiente del código fuente de los casos de uso.
  * **N:** La distribución base (ej. `python:3.11-slim`) y dependencias del sistema pueden optimizarse.
  * **V:** Permite desplegar el sistema en cualquier entorno de nube/on-premise de forma reproducible.
  * **E:** Dos Dockerfiles estándar y un `docker-compose.yml`.
  * **S:** Configuración de infraestructura como código.
  * **T:** Verificable mediante build, levantamiento de contenedores y ejecución de healthchecks.
* **Descripción:**
  > **Como** Operador de Infraestructura / SRE,  
  > **Quiero** imágenes Docker independientes para `Crawler Batch App` y `Catalog API Facade`,  
  > **Para** ejecutar el proceso batch bajo demanda como contenedor efímero y mantener la API de consultas operando 24/7 con consumo mínimo de recursos.
* **Definition of Ready (DoR):**
  - [ ] Arquitectura de contenedores de la Sección 4.1 y 5.2.
  - [ ] Lista consolidada de dependencias en `pyproject.toml` (separando dependencias ligeras de Playwright).
* **Definition of Done (DoD):**
  - [ ] `Dockerfile.batch` con soporte para Python y dependencias de navegadores Playwright.
  - [ ] `Dockerfile.api` optimizado y ligero para el servicio FastAPI.
  - [ ] `docker-compose.yml` que orqueste la API, MongoDB y permita correr el batch con un comando.
  - [ ] Healthcheck HTTP activo en la API (`GET /health`).
* **Criterios de Aceptación:**
  - **Criterio 1:** El contenedor de la API debe levantar en menos de 5 segundos y responder `200 OK` en `/health`.
  - **Criterio 2:** El contenedor batch debe finalizar su ejecución y cerrarse limpiamente retornando código de salida 0 tras completar las fuentes elegibles.

---

### HU-16: Scheduler Técnico para Corridas Batch Oportunistas
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Dispara el batch sin modificar la lógica interna del orquestador.
  * **N:** Mecanismo seleccionable (Cron en host, GitHub Actions, o disparador externo) según la Decisión 1.2 de la Sección 20.
  * **V:** Automatiza la ejecución periódica del sistema sin requerir intervención manual constante.
  * **E:** Configuración de cron / workflow YAML de scheduling.
  * **S:** Configuración puntual de automatización.
  * **T:** Verificable simulando una ejecución programada y comprobando la creación de una nueva corrida en el catálogo.
* **Descripción:**
  > **Como** Operador de Infraestructura,  
  > **Quiero** un mecanismo de scheduling técnico automatizado para invocar el contenedor `Crawler Batch App`,  
  > **Para** que el sistema evalúe oportunistamente la elegibilidad de las fuentes y recolecte inventario sin requerir disparos manuales.
* **Definition of Ready (DoR):**
  - [ ] Decisión 1.2 de la Sección 20 acordada entre el equipo de arquitectura y DevOps.
* **Definition of Done (DoD):**
  - [ ] Script de invocación con manejo de variables de entorno y captura de logs.
  - [ ] Archivo de configuración del scheduler (ej. GitHub Action workflow o crontab documentado).
  - [ ] Documentación operativa en el `README.md`.
* **Criterios de Aceptación:**
  - **Criterio 1:** El scheduler no debe iniciar una nueva corrida si existe otra instancia del batch ejecutándose actualmente para las mismas fuentes (evitar condiciones de carrera).

---

### HU-17: Política Numérica de Transición Dinámica de Cadencia
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Se aísla completamente en `CadencePolicyEvaluator`.
  * **N:** Umbrales numéricos parametrizables (ej. 3 o 5 corridas consecutivas sin novedades).
  * **V:** Optimiza drásticamente los recursos de red y CPU reduciendo la frecuencia de exploración en fuentes que publican con baja periodicidad.
  * **E:** Lógica de evaluación condicional sobre el historial de observaciones.
  * **S:** Módulo puntual de aplicación.
  * **T:** Tests unitarios simulando series de observaciones `NO_CHANGE` y `CHANGED`.
* **Descripción:**
  > **Como** Analista de Datos y Operador de Infraestructura,  
  > **Quiero** que el `CadencePolicyEvaluator` ajuste automáticamente la categoría de una fuente de `DAILY` a `WEEKLY` tras 5 observaciones exitosas consecutivas con `NO_CHANGE`, y a `MONTHLY` tras 4 semanas sin cambios,  
  > **Para** no sobrecargar fuentes que no han actualizado contenido y concentrar los recursos en fuentes de alta frecuencia.
* **Definition of Ready (DoR):**
  - [ ] Decisión 1.1 de la Sección 20 resuelta con valores umbral aprobados por el Team Lead.
* **Definition of Done (DoD):**
  - [ ] Implementación de la regla en `application/cadence_evaluator.py`.
  - [ ] Si una fuente en `MONTHLY` o `WEEKLY` detecta un cambio (`CHANGED`), debe resetearse inmediatamente a su cadencia base o de mayor frecuencia.
  - [ ] Tests unitarios en `test_cadence_policy_evaluator.py`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Tras registrar exactamente 5 observaciones `SUCCESS` + `NO_CHANGE`, el campo `update_category` debe transicionar a la categoría inmediata de menor frecuencia.
  - **Criterio 2:** En ningún caso una ejecución fallida (`FAILED`) debe provocar una reducción de cadencia.

---

### HU-18: Límites Globales de Recorrido y Timeouts de Seguridad
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Políticas transversales de seguridad aplicadas a cualquier workflow.
  * **N:** Límites numéricos ajustables por fuente o variables de entorno.
  * **V:** Evita bucles infinitos de navegación en sitios web mal estructurados y protege el presupuesto de tiempo del sistema.
  * **E:** Control de contadores en colas de descubrimiento (`DiscoveryQueue`).
  * **S:** Reglas de guarda en el ciclo de exploración.
  * **T:** Test unitario con mock de árbol web infinito.
* **Descripción:**
  > **Como** Operador de Infraestructura,  
  > **Quiero** límites de seguridad globales por defecto (profundidad máxima `max_depth: 2`, máximo 500 URLs por fuente y timeout de 15 minutos por corrida de fuente),  
  > **Para** evitar que el crawler quede atrapado en trampas de araña (*spider traps*) o calendarios infinitos en portales estatales.
* **Definition of Ready (DoR):**
  - [ ] Decisión 1.3 de la Sección 20 aprobada.
* **Definition of Done (DoD):**
  - [ ] Controles de `depth` y `max_urls_per_source` integrados en los workflows base.
  - [ ] Timeout de cancelación asíncrona mediante `asyncio.wait_for`.
  - [ ] Si se alcanza el límite de URLs, el estado de ejecución se marca como `PARTIAL_SUCCESS`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Si un sitio web genera URLs dinámicas infinitas, el crawler debe detener la exploración al alcanzar la URL número 500 y reportar `PARTIAL_SUCCESS`.
  - **Criterio 2:** Las URLs con nivel de profundidad mayor a 2 desde el entrypoint no deben agregarse a la cola de pendientes.

---

### HU-19: Política de Confirmación para Recursos Removidos (`REMOVED`)
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Lógica interna de reconciliación en `CatalogApplicationService`.
  * **N:** Cantidad de confirmaciones requeridas (ej. K=3 corridas) configurable.
  * **V:** Previene marcar falsos positivos de recursos eliminados debido a intermitencias temporales de red en el portal de origen.
  * **E:** Lógica de comparación basada en contadores de ausencia.
  * **S:** Extensión de la lógica de snapshot en el servicio de catálogo.
  * **T:** Test simulando ausencias intermitentes vs. ausencia confirmada.
* **Descripción:**
  > **Como** Analista de Datos de DATAX,  
  > **Quiero** que un recurso previamente registrado solo se marque con estado `REMOVED` cuando su ausencia sea confirmada en 3 corridas exitosas consecutivas de la fuente,  
  > **Para** evitar emitir falsas alarmas de eliminación cuando un portal estatal sufra una caída temporal o una página de descarga no responda esporádicamente.
* **Definition of Ready (DoR):**
  - [ ] Decisión 1.4 de la Sección 20 aprobada.
* **Definition of Done (DoD):**
  - [ ] Atributo `consecutive_absences` en el modelo de seguimiento de recursos.
  - [ ] Transición a `REMOVED` únicamente cuando `consecutive_absences >= 3`.
  - [ ] Si el recurso reaparece antes de la tercera confirmación, el contador se reinicia a 0 y mantiene estado `UNCHANGED` o `MODIFIED`.
* **Criterios de Aceptación:**
  - **Criterio 1:** Un recurso que no aparezca en una sola corrida exitosa debe marcarse con advertencia de ausencia pero permanecer activo en el catálogo.
  - **Criterio 2:** En la tercera corrida consecutiva sin detectarse el recurso, su estado se actualiza a `REMOVED`.

---

### HU-20: Estandarización de Contrato para `/mapping/inspect`
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Endpoint autónomo dentro de la fachada de API.
  * **N:** Formato del payload JSON negociable entre DATAX y Reportes Perdidos.
  * **V:** Permite a otros sistemas invocar bajo demanda la inspección rápida de un recurso o estructura de mapeo.
  * **E:** Basado en modelos Pydantic de entrada y salida en FastAPI.
  * **S:** Endpoint único con controlador y servicio asociado.
  * **T:** Pruebas de contrato y validación con `pytest` y `TestClient`.
* **Descripción:**
  > **Como** Sistema Consumidor "Reportes Perdidos",  
  > **Quiero** un endpoint `POST /mapping/inspect` que reciba una URL o estructura de recurso y devuelva su normalización, tipo detectado y hash representativo,  
  > **Para** contrastar rápidamente recursos dudosos contra las reglas de extracción del prospector sin tener que esperar una corrida completa.
* **Definition of Ready (DoR):**
  - [ ] Decisión 1.6 de la Sección 20 acordada.
  - [ ] Esquema de request y response JSON formalizado.
* **Definition of Done (DoD):**
  - [ ] Schema Pydantic `MappingInspectRequest` y `MappingInspectResponse`.
  - [ ] Endpoint `POST /mapping/inspect` documentado en OpenAPI.
  - [ ] Tests de contrato verificando respuestas válidas y manejo de URLs malformadas (`422 Unprocessable Entity`).
* **Criterios de Aceptación:**
  - **Criterio 1:** Ante un envío de URL válida, el endpoint debe responder `200 OK` con la URL normalizada, extensión detectada y hash SHA-256 preliminar.

---

### HU-21: Configuración e Integración de Fuentes del Backlog Inicial
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Cada entrada de fuente en YAML se prueba independientemente.
  * **N:** Semillas focalizadas y exclusiones afinables según cambios en los sitios.
  * **V:** Entrega el valor comprometido en el backlog original (5 fuentes iniciales activas y monitoreadas).
  * **E:** Análisis de portales y configuración declarativa en YAML.
  * **S:** Configuración de las 3 fuentes restantes (ASFI, BCB y otra entidad financiera/estatal).
  * **T:** Ejecución exitosa de corridas batch contra cada fuente configurada.
* **Descripción:**
  > **Como** Analista de Datos de DATAX,  
  > **Quiero** completar la configuración de las 5 fuentes iniciales del backlog oficial en `config/sources.yaml` (FINRURAL, BBV, ASFI, BCB y Ministerio de Economía),  
  > **Para** que el Prospector Externo cubra el espectro completo de información regulatoria y financiera inicial requerida para producción.
* **Definition of Ready (DoR):**
  - [ ] Reportes técnicos de las fuentes provistos por el equipo DATAX.
  - [ ] Selección de workflow asignada a cada fuente (`html`, `javascript` o `commented_html`).
* **Definition of Done (DoD):**
  - [ ] Archivo `config/sources.yaml` con las 5 fuentes declaradas con sus `seeds` y extensiones permitidas.
  - [ ] Corridas de prueba ejecutadas para cada fuente registrando evidencias de descubrimiento.
  - [ ] Reportes de corrida generados y verificados.
* **Criterios de Aceptación:**
  - **Criterio 1:** Cada una de las 5 fuentes debe ejecutar su workflow respectivo y culminar con estado `SUCCESS` (`CHANGED` o `NO_CHANGE`).

---

### HU-22: Prueba de Resiliencia ante Caída Forzada de Proceso (Chaos/Fault Testing)
* **Estado:** ⏳ **PENDIENTE**
* **Criterios INVEST:**
  * **I:** Prueba de resiliencia ejecutada en suite de testing de integración.
  * **N:** Señal de corte simulada (`SIGINT`, `SIGTERM` o desconexión).
  * **V:** Valida formalmente el criterio de aceptación 18.2 del documento de arquitectura antes de salir a producción.
  * **E:** Script de testing con procesos concurrentes en Python.
  * **S:** Escenario de prueba acotado.
  * **T:** Verificación de base de datos post-mortem comprobando consistencia de datos.
* **Descripción:**
  > **Como** Arquitecto de Software y SRE,  
  > **Quiero** una prueba automatizada que interrumpa forzosamente la corrida batch (mediante señal `SIGINT` o kill de proceso) en medio de la ejecución de múltiples fuentes,  
  > **Para** certificar que todas las fuentes que concluyeron antes del corte tienen sus checkpoints, snapshots y observaciones persistidos e íntegros en la base de datos.
* **Definition of Ready (DoR):**
  - [ ] Criterio de aceptación 18.2 del documento de arquitectura.
  - [ ] `CatalogApplicationService` con checkpointing incremental operativo.
* **Definition of Done (DoD):**
  - [ ] Script de prueba en `tests/test_resilience_interruption.py`.
  - [ ] Aserción de que los datos de las fuentes concluidas son consultables vía API tras el reinicio.
  - [ ] Evidencia documentada del resultado de la prueba.
* **Criterios de Aceptación:**
  - **Criterio 1:** Si una corrida de 3 fuentes es interrumpida durante el procesamiento de la tercera fuente, las dos primeras fuentes deben tener sus snapshots e inventarios intactos en la base de datos.
