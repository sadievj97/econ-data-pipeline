"""Guards that keep secrets and data out of git."""

from pipeline.envfile import PLACEHOLDER
from pipeline.paths import ENV_EXAMPLE_FILE, PROJECT_ROOT


def test_gitignore_keeps_secrets_environment_and_data_out():
    ignored = {line.strip() for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for entry in (".env", ".venv/", "data/raw/", "out/"):
        assert entry in ignored, f".gitignore is missing {entry}"


def test_env_example_holds_only_placeholders():
    # Report only the names of offending lines, never their values.
    offending = []
    for raw in ENV_EXAMPLE_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if value.strip() not in ("", PLACEHOLDER):
            offending.append(name.strip())
    assert not offending, f".env.example has a real-looking value on: {', '.join(offending)}"
