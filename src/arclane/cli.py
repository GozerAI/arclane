"""Arclane CLI."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="arclane", description="Arclane — Autonomous business engine")
    sub = parser.add_subparsers(dest="command")

    # serve
    serve_cmd = sub.add_parser("serve", help="Start the Arclane API server")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8012)
    serve_cmd.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only)")

    # provision
    prov_cmd = sub.add_parser("provision", help="Provision a new business")
    prov_cmd.add_argument("name", help="Business name")
    prov_cmd.add_argument("--description", "-d", required=True, help="Business description")
    prov_cmd.add_argument("--email", "-e", required=True, help="Owner email")
    prov_cmd.add_argument("--template", "-t", default="content-site")

    # health
    sub.add_parser("health", help="Check service health")

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        uvicorn.run("arclane.api.app:app", host=args.host, port=args.port, reload=args.reload)

    elif args.command == "provision":
        import asyncio
        asyncio.run(_provision(args))

    elif args.command == "health":
        import asyncio
        asyncio.run(_health())

    else:
        parser.print_help()
        sys.exit(1)


async def _provision(args):
    from arclane.core.database import init_db, async_session
    from arclane.models.tables import Business
    from arclane.provisioning.service import provision_business
    from arclane.api.routes.intake import _slugify

    await init_db()

    async with async_session() as session:
        business = Business(
            slug=_slugify(args.name),
            name=args.name,
            description=args.description,
            owner_email=args.email,
            template=args.template,
        )
        session.add(business)
        await session.commit()
        await session.refresh(business)

    await provision_business(business)
    print(f"Business provisioned: {business.slug}.arclane.cloud")


async def _health():
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8012/health", timeout=5.0)
            print(resp.json())
    except httpx.RequestError:
        print("Arclane is not running")
        sys.exit(1)


if __name__ == "__main__":
    main()
