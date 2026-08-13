"""Deterministic local HTTP dependency used by Agent Chaos examples."""

from __future__ import annotations

import argparse

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def customer(request: Request) -> JSONResponse:
    customer_id = request.path_params["customer_id"]
    return JSONResponse({"id": customer_id, "name": "Ada Lovelace", "tier": "gold"})


async def order(request: Request) -> JSONResponse:
    order_id = request.path_params["order_id"]
    return JSONResponse({"id": order_id, "status": "ready", "total": 42.0})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/customer/{customer_id}", customer),
        Route("/orders/{order_id}", order),
    ]
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()
