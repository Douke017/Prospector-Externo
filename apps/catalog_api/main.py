"""
Servicio HTTP persistente Catalog API Facade (FastAPI).
Expone consultas de solo lectura para DATAX y Reportes Perdidos sobre el inventario bruto.
"""

import sys
import os
import argparse
from pathlib import Path
from fastapi import FastAPI, HTTPException

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.adapters.api.routes import create_catalog_router

output_dir = Path(os.environ.get("PROSPECTOR_OUTPUT_DIR", str(project_root / "output")))
backend = os.environ.get("PROSPECTOR_BACKEND", "json")
mongo_uri = os.environ.get("PROSPECTOR_MONGO_URI", "mongodb://localhost:27017")

repo = None
# python -m debe resolver los argumentos ANTES de abrir un backend configurado
# por entorno. Uvicorn conserva la aplicación ASGI configurable por variables.
if __name__ != "__main__":
    if backend == "mongo":
        from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
        repo = MongoPersistenceAdapter(connection_uri=mongo_uri,
            database_name=os.environ.get("PROSPECTOR_MONGO_DATABASE", "prospector_externo"))
    else:
        repo = LocalJsonRepositoryAdapter(base_output_dir=output_dir)

app = FastAPI(
    title="Prospector Externo — Catalog API Facade",
    description="API REST de solo lectura para consulta de catálogo, snapshots, observaciones y reportes de corrida.",
    version="1.0.0"
)

if repo is not None:
    router = create_catalog_router(catalog_repo=repo, report_repo=repo)
    app.include_router(router)


@app.get("/health", tags=["Health"])
def health_check():
    try:
        repo.check_health()
    except Exception as exc:
        # No incluir la ruta física, URI Mongo ni detalles sensibles del error.
        raise HTTPException(status_code=503, detail="Catálogo no disponible") from exc
    return {"status": "UP", "service": "catalog-api-facade"}


def main(argv=None):
    """Entrada compartida por python -m y el script instalado prospector-api."""
    global app, repo
    parser = argparse.ArgumentParser(description="API REST del mismo catálogo B13; Swagger en /docs")
    parser.add_argument("--backend", choices=["json", "mongo"], default=None)
    parser.add_argument("--catalog-dir", type=Path, default=None)
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--mongo-database", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if repo is None or any(value is not None for value in (args.backend, args.catalog_dir, args.mongo_uri, args.mongo_database)):
        selected_backend = args.backend or backend
        if selected_backend == "mongo":
            from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
            repo = MongoPersistenceAdapter(connection_uri=args.mongo_uri or mongo_uri,
                database_name=args.mongo_database or os.environ.get("PROSPECTOR_MONGO_DATABASE", "prospector_externo"))
        else:
            selected_dir = args.catalog_dir or output_dir
            if args.catalog_dir and not (selected_dir / "state/sources.json").is_file():
                parser.error("--catalog-dir no contiene state/sources.json; ejecute primero B13 o corrija la ruta")
            repo = LocalJsonRepositoryAdapter(selected_dir)
        app = FastAPI(title="Prospector Externo — Catalog API Facade", version="1.0.0")
        app.include_router(create_catalog_router(catalog_repo=repo, report_repo=repo))
        app.add_api_route("/health", health_check, methods=["GET"], tags=["Health"])
    print(f"Backend: {args.backend or backend}; catálogo JSON: {args.catalog_dir or output_dir}")
    print(f"Swagger: http://{args.host}:{args.port}/docs")
    print(f"OpenAPI: http://{args.host}:{args.port}/openapi.json")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
