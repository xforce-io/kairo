import pytest


def seed_existing_datasource(
    serve,
    project_id: str,
    *,
    url: str,
    reader: str,
    kind: str,
    connection_id: str | None = None,
    purpose: str = "",
    name: str = "",
    ds_id: str | None = None,
):
    """Write a pre-existing Data Source row, bypassing the new-add allowlist."""
    from kairo.projects import DataSource, get_project, save_project

    project = get_project(serve, project_id)
    ds = DataSource(
        id=ds_id or f"ds-seed-{len(project.datasources):04d}",
        connection_id=connection_id or reader,
        url=url,
        kind=kind,
        purpose=purpose,
        name=name,
        reader=reader,
    )
    project.datasources.append(ds)
    save_project(serve, project)
    return ds


@pytest.fixture(autouse=True)
def _isolate_machine_asr(monkeypatch, tmp_path_factory):
    """守卫:测试默认不读本机 ASR 配置(env / ~/.config/kairo),避免误触发真实 whisper。

    需要本机配置的测试自行 setenv 覆盖即可。
    """
    monkeypatch.delenv("KAIRO_ASR_CMD", raising=False)
    monkeypatch.delenv("KAIRO_ASR_ORIGIN", raising=False)
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-isolated"))
    )
