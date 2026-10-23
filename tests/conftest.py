import json
import uuid
from pathlib import Path

import psycopg
import pytest

from jijilens import db
from jijilens.config import load_settings

FIXTURES = Path(__file__).parent / "fixtures"


class Wrap:
    """Marks a value Nuxt wraps in a Vue reducer, e.g. Wrap("Reactive", {...})."""

    def __init__(self, tag, value):
        self.tag, self.value = tag, value


def devalue_encode(root) -> list:
    """A minimal devalue encoder, enough to build Nuxt payloads for tests."""
    payload: list = []

    def add(value) -> int:
        index = len(payload)
        payload.append(None)
        if isinstance(value, Wrap):
            payload[index] = [value.tag, add(value.value)]
        elif isinstance(value, dict):
            payload[index] = {k: add(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            payload[index] = [add(v) for v in value]
        else:
            payload[index] = value
        return index

    add(root)
    return payload


def ad_page_html(advert: dict) -> str:
    """An ad page the way jiji.ug serves it: the advert inside Nuxt's payload."""
    root = Wrap(
        "ShallowReactive",
        {
            "data": Wrap("ShallowReactive", {f"advert-item-{advert['guid']}": {"advert": {"advert": advert}}}),
            "state": {},
            "pinia": {"AdvertsStore": Wrap("Reactive", {"advertItem": advert})},
        },
    )
    payload = json.dumps(devalue_encode(root))
    return f'<html><body><h1>x</h1><script type="application/json" id="__NUXT_DATA__" data-ssr="true">{payload}</script></body></html>'


@pytest.fixture
def advert() -> dict:
    return json.loads((FIXTURES / "advert.json").read_text(encoding="utf-8"))


@pytest.fixture
def listing_page() -> dict:
    return json.loads((FIXTURES / "listing_page.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def new_database():
    """Makes empty, migrated databases on the compose (or CI) server; all are dropped at the end.

    Returns the new database's URL. Skips the test when the server is down.
    """
    url = load_settings().database_url
    try:
        admin = psycopg.connect(url, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"database not reachable: {exc}")
    created: list[str] = []

    def make() -> str:
        name = f"jijilens_test_{uuid.uuid4().hex[:8]}"
        admin.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        new_url = url.rsplit("/", 1)[0] + "/" + name
        with db.connect(new_url) as conn:
            db.migrate(conn)
        return new_url

    yield make
    for name in created:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()
