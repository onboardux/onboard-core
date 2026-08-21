"""Extractor fixtures -- one purpose-built tree per behaviour worth protecting.

Every test here names its defect. What is deliberately *not* tested: that an
extractor finds a particular count on a particular framework. Counts are the
recall floor's job (`--check-expected`, invariant #1, on real repositories), and
asserting them here would produce a suite that fails whenever someone improves
an extractor -- which is how a test stops meaning anything and starts being
edited to match the code.
"""

from pathlib import Path

import pytest
from adopt_map import SourceTree
from adopt_map.packs import generic, web


def _tree(tmp_path: Path, files: dict[str, str]) -> SourceTree:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return SourceTree.scan(tmp_path)


@pytest.mark.unit
def test_an_endpoint_carries_method_path_and_parameter_names(tmp_path: Path) -> None:
    """*Fails when* the endpoint attribute set drifts from v6.1 §6's "method +
    path + parameter names". *Matters because* those three are the digest input,
    so anything missing here is a change Build 6 can never detect and anything
    extra is a false change it will report forever. *No other instrument catches
    it because* the URI is built from method and path alone -- parameters are
    invisible in it, and a test asserting the URI would pass with them dropped."""
    tree = _tree(
        tmp_path,
        {
            "app.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "\n"
                "@app.post('/v1/orders')\n"
                "def create(payload: dict, idempotency_key: str):\n"
                "    return {}\n"
            )
        },
    )

    found = list(web.EndpointExtractor().extract(tree))

    assert len(found) == 1
    assert found[0].kind == "endpoint"
    assert list(found[0].key) == ["POST /v1/orders"], "the slash is data, not structure"
    assert found[0].attributes == {
        "method": "POST",
        "path": "/v1/orders",
        "parameters": ["payload", "idempotency_key"],
    }
    assert found[0].span.path == "app.py"


@pytest.mark.unit
def test_a_flask_route_without_methods_is_the_same_endpoint_as_an_explicit_get(
    tmp_path: Path,
) -> None:
    """*Fails when* an omitted `methods=` produces a different identity from an
    explicit `methods=['GET']`. *Matters because* they are the same endpoint, and
    two URIs for one referent is the defect Build 0's whole URI grammar exists to
    prevent -- one of them would accumulate the knowledge and the other would
    read as uncovered forever. *No other instrument catches it because* both
    forms extract successfully, so nothing fails; there are simply two rows."""
    implicit = _tree(
        tmp_path / "a", {"a.py": "@app.route('/health')\ndef health():\n    return 'ok'\n"}
    )
    explicit = _tree(
        tmp_path / "b",
        {"b.py": "@app.route('/health', methods=['GET'])\ndef health():\n    return 'ok'\n"},
    )

    extractor = web.EndpointExtractor()
    first = next(iter(extractor.extract(implicit)))
    second = next(iter(extractor.extract(explicit)))

    assert list(first.key) == list(second.key) == ["GET /health"]
    assert first.attributes == second.attributes


@pytest.mark.unit
def test_a_router_prefix_is_part_of_the_endpoint_path(tmp_path: Path) -> None:
    """Found on the first real repository, not by a fixture.

    *Fails when* `APIRouter(prefix=...)` stops being applied. *Matters because*
    the decorator's literal is not the served path: with a prefix, `@router.get
    ('/')` serves `GET /items/`, so recording the literal names an endpoint that
    does not exist -- permanently, since a URI is never rewritten -- and two
    routers that both declare `/` collapse into **one** identity, silently
    merging two referents. *No other instrument catches it because* both forms
    extract cleanly and produce well-formed URIs; the map simply describes a
    different API from the one that is running."""
    tree = _tree(
        tmp_path,
        {
            "items.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter(prefix='/items', tags=['items'])\n"
                "\n"
                "@router.get('/')\n"
                "def read_items():\n"
                "    return []\n"
            ),
            "users.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter(prefix='/users')\n"
                "\n"
                "@router.get('/')\n"
                "def read_users():\n"
                "    return []\n"
            ),
        },
    )

    found = sorted(observation.key[0] for observation in web.EndpointExtractor().extract(tree))

    assert found == ["GET /items/", "GET /users/"], (
        "two routers declaring '/' must stay two endpoints"
    )


@pytest.mark.unit
def test_a_route_inside_a_docstring_is_not_an_endpoint(tmp_path: Path) -> None:
    """*Fails when* extraction goes back to matching text instead of structure.
    *Matters because* a documented example route would become a real identity,
    and a false identity is worse than a missing one: it reads as uncovered
    knowledge forever and can never be satisfied. *No other instrument catches it
    because* a false positive raises nothing and looks exactly like a find."""
    tree = _tree(
        tmp_path,
        {
            "docs.py": (
                '"""Usage:\n'
                "\n"
                "    @app.post('/v1/imaginary')\n"
                "    def example(): ...\n"
                '"""\n'
                "VERSION = 1\n"
            )
        },
    )

    assert list(web.EndpointExtractor().extract(tree)) == []


@pytest.mark.unit
def test_django_model_fields_are_namespaced_by_their_model(tmp_path: Path) -> None:
    """*Fails when* the model name stops being the namespace. *Matters because*
    `id` on `Order` and `id` on `Customer` would collapse into one identity, and
    whichever was observed second would silently inherit the first's coverage and
    bindings. *No other instrument catches it because* both extract fine and the
    store accepts both -- `observe` is keyed on the URI, so a colliding URI is a
    successful re-observation, not an error."""
    tree = _tree(
        tmp_path,
        {
            "models.py": (
                "from django.db import models\n"
                "class Order(models.Model):\n"
                "    id = models.AutoField(primary_key=True)\n"
                "class Customer(models.Model):\n"
                "    id = models.AutoField(primary_key=True)\n"
            )
        },
    )

    found = list(web.SchemaFieldExtractor().extract(tree))

    assert {observation.namespace for observation in found} == {"Order", "Customer"}
    assert all(list(observation.key) == ["id"] for observation in found)


@pytest.mark.unit
def test_middleware_order_is_an_attribute(tmp_path: Path) -> None:
    """*Fails when* position stops being part of the digest. *Matters because*
    middleware order is behaviour -- authentication after CSRF is a different
    system from authentication before it -- so a reordering that changed nothing
    else would be invisible to Build 6. *No other instrument catches it because*
    the identities are unchanged by a reorder; only the attributes move."""
    tree = _tree(
        tmp_path,
        {
            "settings.py": (
                "MIDDLEWARE = [\n"
                "    'django.middleware.security.SecurityMiddleware',\n"
                "    'django.middleware.csrf.CsrfViewMiddleware',\n"
                "]\n"
            )
        },
    )

    found = list(web.MiddlewareExtractor().extract(tree))

    assert [observation.attributes["order"] for observation in found] == [0, 1]


@pytest.mark.unit
def test_an_unparseable_python_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    """*Fails when* a syntax error ends the run. *Matters because* client
    repositories contain Python 2, templates with placeholders and deliberately
    broken fixtures, and a tool that refuses such a tree is a tool an FDE cannot
    point at real work. *No other instrument catches it because* every
    purpose-built fixture parses."""
    tree = _tree(
        tmp_path,
        {
            "broken.py": "def oops(:\n",
            "fine.py": "@app.get('/ok')\ndef ok():\n    return 1\n",
        },
    )

    found = list(web.EndpointExtractor().extract(tree))

    assert [list(observation.key) for observation in found] == [["GET /ok"]]


@pytest.mark.unit
def test_environment_variables_are_found_where_they_are_read(tmp_path: Path) -> None:
    """*Fails when* the read-site pattern stops matching a real form.
    *Matters because* a variable the system fails without is exactly the
    knowledge a handover needs, and the template file only says what someone
    remembered to document. *No other instrument catches it because* a regex that
    matches nothing yields an empty iterator and a green run."""
    tree = _tree(
        tmp_path,
        {
            "settings.py": (
                "import os\n"
                "DB = os.environ['DATABASE_URL']\n"
                "KEY = os.environ.get('SECRET_KEY')\n"
                "PORT = os.getenv('PORT')\n"
            )
        },
    )

    found = {observation.key[0] for observation in generic.EnvVarExtractor().extract(tree)}

    assert found == {"DATABASE_URL", "SECRET_KEY", "PORT"}


@pytest.mark.unit
def test_a_settings_class_declares_environment_variables(tmp_path: Path) -> None:
    """Found by the recall floor on reference repository #1.

    *Fails when* `BaseSettings` subclasses stop being read. *Matters because* a
    modern FastAPI application deliberately never calls `os.environ` -- every
    variable it needs, `SECRET_KEY` included, is an annotated attribute on a
    settings class. Without this, the map of such a system contains no
    environment variables at all, and "what does this need to run" is the first
    question a handover has to answer. *No other instrument catches it because*
    the run succeeds and reports identities; it simply reports none of these,
    and only a human who knew the app would notice."""
    tree = _tree(
        tmp_path,
        {
            "config.py": (
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n"
                "    model_config = SettingsConfigDict(env_file='.env')\n"
                "    SECRET_KEY: str\n"
                "    PROJECT_NAME: str = 'demo'\n"
            )
        },
    )

    found = {
        observation.key[0]: observation.attributes
        for observation in generic.SettingsClassExtractor().extract(tree)
    }

    assert set(found) == {"SECRET_KEY", "PROJECT_NAME"}, "model_config is plumbing, not a setting"
    assert found["SECRET_KEY"]["required"] is True
    assert found["PROJECT_NAME"]["required"] is False


@pytest.mark.unit
def test_a_bare_annotation_is_a_column_only_inside_a_table_class(tmp_path: Path) -> None:
    """Also found by the recall floor: `hashed_password: str` is a real column.

    *Fails when* bare annotations are either both ignored (losing real columns)
    or accepted everywhere (minting a `db_field` for every dataclass, Protocol
    and TypedDict attribute in the repository). *Matters because* both errors are
    silent: one leaves the most security-relevant column in the schema invisible,
    the other floods the inventory with things that are not persisted at all. *No
    other instrument catches it because* neither produces a failure -- only a
    different, plausible-looking map."""
    tree = _tree(
        tmp_path,
        {
            "models.py": (
                "from sqlmodel import SQLModel\n"
                "class User(UserBase, table=True):\n"
                "    hashed_password: str\n"
                "class LoginPayload(SQLModel):\n"
                "    password: str\n"
            )
        },
    )

    found = {
        (observation.namespace, observation.key[0])
        for observation in web.SchemaFieldExtractor().extract(tree)
    }

    assert found == {("User", "hashed_password")}


@pytest.mark.unit
def test_a_ci_workflow_reports_its_triggers_despite_yaml_treating_on_as_true(
    tmp_path: Path,
) -> None:
    """*Fails when* the `on:`-is-`True` trap is reintroduced. *Matters because*
    YAML 1.1 parses an unquoted `on` as the boolean `True`, so reading only the
    string key reports every workflow in existence as having no triggers -- and
    "what runs on merge" is among the first questions a handover answers. *No
    other instrument catches it because* the workflow identity is still created
    correctly; only its attributes are quietly empty."""
    tree = _tree(
        tmp_path,
        {
            ".github/workflows/ci.yml": (
                "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            )
        },
    )

    found = list(generic.CiWorkflowExtractor().extract(tree))

    assert len(found) == 1
    assert found[0].attributes["triggers"] == ["pull_request", "push"]
    assert found[0].attributes["jobs"] == ["test"]
