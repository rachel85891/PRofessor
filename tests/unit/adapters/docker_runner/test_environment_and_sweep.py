import io
import logging
import tarfile

import pytest

from hw_checker.adapters.docker_runner.engine import ContainerSpec, EngineError
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.domain.errors import ExecutionEnvironmentError
from hw_checker.ports import CodeRunner
from tests.unit.adapters.docker_runner.fake_engine import FakeClock, FakeEngine
from tests.unit.adapters.docker_runner.test_engine_and_config import config

PREFIX = "hw-checker-test"


def runner(engine: FakeEngine) -> DockerCodeRunner:
    clock = FakeClock()
    return DockerCodeRunner(config(), engine, clock=clock, sleep=clock.sleep)


def labelled(kind: str, run: str = "old-run") -> dict[str, str]:
    return {PREFIX: "1", f"{PREFIX}.run": run, f"{PREFIX}.kind": kind}


def test_runner_satisfies_the_port() -> None:
    port: CodeRunner = runner(FakeEngine())

    assert isinstance(port, DockerCodeRunner)


def test_check_environment_passes_when_everything_is_ready() -> None:
    engine = FakeEngine()

    runner(engine).check_environment()

    assert engine.pulled == []
    assert engine.leftovers == 0


def test_check_environment_reports_docker_not_running() -> None:
    with pytest.raises(ExecutionEnvironmentError, match="Docker Desktop is not running"):
        runner(FakeEngine(reachable=False)).check_environment()


def test_check_environment_pulls_missing_images_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine(images_present=False)

    with caplog.at_level(logging.INFO, logger="hw_checker.adapters.docker_runner.runner"):
        runner(engine).check_environment()

    assert engine.pulled == list(config().images)
    # Two images per framework (8, 9 and 10) and the probe image.
    assert sum("Pulling missing image" in r.getMessage() for r in caplog.records) == 7


def test_check_environment_reports_an_image_that_cannot_be_pulled() -> None:
    engine = FakeEngine(images_present=False, fail_on={"pull": EngineError("manifest unknown")})

    with pytest.raises(ExecutionEnvironmentError, match=r"cannot pull mcr\.microsoft\.com"):
        runner(engine).check_environment()


def test_check_environment_creates_the_cache_volume_labelled_as_cache() -> None:
    engine = FakeEngine(cache_present=False)

    runner(engine).check_environment()

    assert engine.volumes["hw-checker-test-nuget"] == {PREFIX: "1", f"{PREFIX}.kind": "cache"}


def test_check_environment_gives_the_packages_folder_to_the_app_user() -> None:
    engine = FakeEngine()

    runner(engine).check_environment()

    ((_, path, _),) = engine.archives
    assert path == "/nuget"
    (init,) = engine.specs("init")
    assert init.mounts[0].volume == "hw-checker-test-nuget"
    assert init.user == "1654:1654"
    assert engine.leftovers == 0


def test_check_environment_reports_a_cache_that_cannot_be_created() -> None:
    engine = FakeEngine(cache_present=False, fail_on={"create_volume": EngineError("no space")})

    with pytest.raises(ExecutionEnvironmentError, match="cannot prepare the package cache"):
        runner(engine).check_environment()


def test_sweep_removes_leftovers_but_keeps_the_cache(caplog: pytest.LogCaptureFixture) -> None:
    engine = FakeEngine()
    engine.volumes["hw-checker-test-nuget"] = labelled("cache")
    engine.volumes["old-work"] = labelled("work")
    engine.networks["old-net"] = (labelled("network"), True)
    engine.create_container(
        ContainerSpec("old-app", "img", (), labelled("app"), "1654:1654", 64, 1, 10)
    )
    engine.volumes["someone-elses"] = {"other": "1"}

    with caplog.at_level(logging.INFO, logger="hw_checker.adapters.docker_runner.runner"):
        removed = runner(engine).sweep_leftovers()

    assert removed == 3
    assert set(engine.volumes) == {"hw-checker-test-nuget", "someone-elses"}
    assert engine.networks == {}
    assert engine.containers == {}
    assert any("Swept 3 leftover" in r.getMessage() for r in caplog.records)


def test_sweep_removes_containers_before_networks_and_volumes() -> None:
    engine = FakeEngine()
    engine.volumes["old-work"] = labelled("work")
    engine.networks["old-net"] = (labelled("network"), True)
    engine.create_container(
        ContainerSpec("old-app", "img", (), labelled("app"), "1654:1654", 64, 1, 10)
    )

    runner(engine).sweep_leftovers()

    removals = [call.split()[0] for call in engine.calls if call.startswith("remove")]
    assert removals == ["remove_container", "remove_network", "remove_volume"]


def test_sweep_continues_when_one_removal_fails() -> None:
    engine = FakeEngine(fail_on={"remove_network": EngineError("in use")})
    engine.volumes["old-work"] = labelled("work")
    engine.networks["old-net"] = (labelled("network"), True)

    assert runner(engine).sweep_leftovers() == 1
    assert engine.volumes == {}


def test_sweep_reports_an_unreachable_engine() -> None:
    engine = FakeEngine(fail_on={"list_labelled": EngineError("pipe not found")})

    with pytest.raises(ExecutionEnvironmentError, match="cannot list leftovers"):
        runner(engine).sweep_leftovers()


def test_directory_archive_is_owned_by_the_app_user() -> None:
    from hw_checker.adapters.docker_runner.archive import directory_archive

    with tarfile.open(fileobj=io.BytesIO(directory_archive("packages", 1654, 1654))) as archive:
        (member,) = archive.getmembers()

    assert (member.name, member.isdir(), member.uid, member.gid) == ("packages", True, 1654, 1654)
