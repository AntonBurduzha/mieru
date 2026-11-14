import re
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import make_organization, make_site

from mieru.models import Organization, Site
from mieru.tools.create_site import parse_args, run

pytestmark = pytest.mark.integration

OUTPUT = re.compile(r"site_id=\d+ organization_id=[0-9a-f-]{36} domain=(\S+)\n")


async def organizations_named(session: AsyncSession, name: str) -> int:
  query = select(func.count()).select_from(Organization).where(Organization.name == name)
  return (await session.scalar(query)) or 0


async def test_creates_an_organization_and_a_site(
  db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
  args = parse_args(["--domain", "WWW.Tool.test", "--org-name", "Tool", "--timezone", "UTC"])
  assert await run(args, db_session) == 0
  match = OUTPUT.fullmatch(capsys.readouterr().out)
  assert match is not None
  assert match.group(1) == "tool.test"
  assert await organizations_named(db_session, "Tool") == 1


async def test_adds_a_site_to_an_existing_organization(
  db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
  organization = await make_organization(db_session)
  await db_session.commit()
  args = parse_args(["--domain", "second.test", "--org-id", str(organization.id)])
  assert await run(args, db_session) == 0
  site = await db_session.scalar(select(Site).where(Site.domain == "second.test"))
  assert site is not None
  assert (site.organization_id, site.timezone) == (organization.id, "UTC")


@pytest.mark.parametrize(
  ("argv", "code"),
  [
    (["--domain", "taken.test", "--org-name", "Late"], "conflict"),
    (
      ["--domain", "new.test", "--org-name", "Late", "--timezone", "Mars/Olympus"],
      "validation_failed",
    ),
    (["--domain", "new.test", "--org-id", str(uuid.UUID(int=1))], "not_found"),
  ],
)
async def test_an_error_exits_1_and_writes_nothing(
  db_session: AsyncSession, capsys: pytest.CaptureFixture[str], argv: list[str], code: str
) -> None:
  await make_site(db_session, domain="taken.test")
  # Committed, so the tool's rollback can't undo this setup by accident
  await db_session.commit()
  assert await run(parse_args(argv), db_session) == 1
  output = capsys.readouterr()
  assert output.out == ""
  assert output.err.startswith(f"{code}: ")
  assert await organizations_named(db_session, "Late") == 0
  assert await db_session.scalar(select(Site).where(Site.domain == "new.test")) is None


@pytest.mark.parametrize(
  "argv",
  [
    ["--domain", "a.test"],
    ["--domain", "a.test", "--org-name", "A", "--org-id", str(uuid.UUID(int=1))],
    ["--domain", "a.test", "--org-id", "not-a-uuid"],
  ],
)
def test_bad_arguments_exit_2(argv: list[str]) -> None:
  with pytest.raises(SystemExit) as exit_info:
    parse_args(argv)
  assert exit_info.value.code == 2
