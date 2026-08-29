from pathlib import Path


def test_dev_compose_keeps_the_forked_mem0_package() -> None:
    server = Path(__file__).parents[1] / "server"
    compose = (server / "docker-compose.yaml").read_text()
    dockerfile = (server / "dev.Dockerfile").read_text()

    assert "pip install" not in compose
    assert "RUN pip install --no-cache-dir -e ." in dockerfile
    assert "../mem0:/app/packages/mem0" in compose
