"""
uv run python -m mieru.tools.create_site
--domain example.com
--org-name "Acme"
--timezone "Europe/Lisbon"
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mieru.core.config import get_settings
from mieru.core.errors import AppError
from mieru.db.session import make_engine, make_sessionmaker
from mieru.repositories.organizations import insert_organization
from mieru.services.sites import create_site


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
  """Read the command line. On bad arguments argparse prints usage and exits with 2."""
  parser = argparse.ArgumentParser(
    prog="python -m mieru.tools.create_site",
    description="Create a site for a new or an existing organization.",
  )
  parser.add_argument("--domain", required=True, help="stored lowercase, without www.")
  parser.add_argument("--timezone", default="UTC", help="IANA name, e.g. Europe/Kyiv")
  owner = parser.add_mutually_exclusive_group(required=True)
  owner.add_argument("--org-name", help="create a new organization with this name")
  owner.add_argument("--org-id", type=UUID, help="add the site to this organization")
  return parser.parse_args(argv)


async def run(args: argparse.Namespace, session: AsyncSession) -> int:
  """Create the rows and commit, or roll everything back and report. Returns the exit code."""
  try:
    if args.org_name is not None:
      org_id = (await insert_organization(session, args.org_name)).id
    else:
      org_id = args.org_id
    site = await create_site(session, org_id, args.domain, args.timezone)
    await session.commit()
  except AppError as exc:
    await session.rollback()
    print(f"{exc.code}: {exc.message}", file=sys.stderr)
    return 1
  print(f"site_id={site.id} organization_id={site.organization_id} domain={site.domain}")
  return 0


async def run_once(args: argparse.Namespace) -> int:
  """Open a connection pool for this one run, and close it afterwards."""
  engine = make_engine(get_settings())
  try:
    async with make_sessionmaker(engine)() as session:
      return await run(args, session)
  finally:
    await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
  return asyncio.run(run_once(parse_args(argv)))


if __name__ == "__main__":
  sys.exit(main())
