"""
Servicio HTTP persistente Catalog API Facade (FastAPI).
Expone consultas y acciones operativas de catálogo, exploración abierta,
resiliencia y monitoreo estructural para DATAX.
"""

import argparse
import os
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import (
    LocalJsonRepositoryAdapter,
)
from prospector_externo.application.catalog_bootstrapper import (
    bootstrap_official_catalog_if_needed,
)

output_dir = Path(os.environ.get("PROSPECTOR_OUTPUT_DIR", str(project_root / "output")))
backend = os.environ.get("PROSPECTOR_BACKEND", "json")
mongo_uri = os.environ.get("PROSPECTOR_MONGO_URI", "mongodb://localhost:27017")

repo = None
if __name__ != "__main__":
    if backend == "mongo":
        from prospector_externo.adapters.persistence.mongo_adapter import (
            MongoPersistenceAdapter,
        )

        repo = MongoPersistenceAdapter(
            connection_uri=mongo_uri,
            database_name=os.environ.get(
                "PROSPECTOR_MONGO_DATABASE", "prospector_externo"
            ),
        )
    else:
        repo = LocalJsonRepositoryAdapter(base_output_dir=output_dir)

    if repo is not None and os.environ.get("PROSPECTOR_SEED_ON_BOOT") == "1":
        bootstrap_official_catalog_if_needed(repo, project_root)

app = FastAPI(
    title="Prospector Externo — Catalog API Facade",
    description="API REST para gestión de catálogo, exploración abierta, contingencia y monitoreo estructural.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if repo is not None:
    router = create_catalog_router(
        catalog_repo=repo,
        report_repo=repo,
        config_path=project_root / "config" / "sources.yaml",
    )
    app.include_router(router)


@app.get("/health", tags=["Health"])
def health_check():
    try:
        repo.check_health()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Catálogo no disponible") from exc
    return {"status": "UP", "service": "catalog-api-facade"}


def main(argv=None):
    """Entrada compartida por python -m y el script instalado prospector-api."""
    global app, repo
    parser = argparse.ArgumentParser(
        description="API REST del catálogo Prospector Externo; Swagger en /docs"
    )
    parser.add_argument("--backend", choices=["json", "mongo"], default=None)
    parser.add_argument("--catalog-dir", type=Path, default=None)
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--mongo-database", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if repo is None or any(
        value is not None
        for value in (args.backend, args.catalog_dir, args.mongo_uri, args.mongo_database)
    ):
        selected_backend = args.backend or backend
        if selected_backend == "mongo":
            from prospector_externo.adapters.persistence.mongo_adapter import (
                MongoPersistenceAdapter,
            )

            repo = MongoPersistenceAdapter(
                connection_uri=args.mongo_uri or mongo_uri,
                database_name=args.mongo_database
                or os.environ.get("PROSPECTOR_MONGO_DATABASE", "prospector_externo"),
            )
        else:
            selected_dir = args.catalog_dir or output_dir
            if args.catalog_dir and not (selected_dir / "state/sources.json").is_file():
                parser.error(
                    "--catalog-dir no contiene state/sources.json; ejecute primero B13 o corrija la ruta"
                )
            repo = LocalJsonRepositoryAdapter(selected_dir)
        if os.environ.get("PROSPECTOR_SEED_ON_BOOT") == "1":
            bootstrap_official_catalog_if_needed(repo, project_root)
        app = FastAPI(title="Prospector Externo — Catalog API Facade", version="1.0.0")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        app.include_router(
            create_catalog_router(
                catalog_repo=repo,
                report_repo=repo,
                config_path=project_root / "config" / "sources.yaml",
            )
        )
        app.add_api_route("/health", health_check, methods=["GET"], tags=["Health"])
    print(
        f"Backend: {args.backend or backend}; catálogo JSON: {args.catalog_dir or output_dir}"
    )
    print(f"Swagger: http://{args.host}:{args.port}/docs")
    print(f"OpenAPI: http://{args.host}:{args.port}/openapi.json")
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
