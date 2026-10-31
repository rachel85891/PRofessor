"""Parallel executions share one package cache: only their restore steps are serialized."""

import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import ContainerSpec
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.domain import ExecutionResult, StepStatus
from tests.unit.adapters.docker_runner.fake_engine import FakeEngine
from tests.unit.adapters.docker_runner.test_runner import WEB_PROJECT, request, runner

WAIT_SECONDS = 5.0
"""Upper bound for anything a test waits on, so a broken lock fails instead of hanging."""
SECOND_RESTORE_GRACE_SECONDS = 0.3
"""How long a test gives a second restore to start while the first one still runs."""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Shop.Api.csproj").write_text(WEB_PROJECT, encoding="utf-8")
    return root


@dataclass
class BlockingRestoreEngine(FakeEngine):
    """A FakeEngine for two threads: restores block until released, and the two
    executions' prepare steps must meet (copy their code at the same time) to go on.
    """

    release_restores: threading.Event = field(default_factory=threading.Event)
    restore_started: threading.Event = field(default_factory=threading.Event)
    prepares_met: threading.Barrier = field(
        default_factory=lambda: threading.Barrier(2, timeout=WAIT_SECONDS)
    )
    active_restores: int = 0
    most_active_restores: int = 0
    restores_started: int = 0
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def create_container(self, spec: ContainerSpec) -> str:
        with self._guard:
            return super().create_container(spec)

    def put_archive(self, container: str, path: str, data: bytes) -> None:
        super().put_archive(container, path, data)
        if path == "/work":
            self.prepares_met.wait()

    def start(self, container: str) -> None:
        super().start(container)
        if self.containers[container].kind != "restore":
            return
        with self._guard:
            self.active_restores += 1
            self.restores_started += 1
            self.most_active_restores = max(self.most_active_restores, self.active_restores)
        self.restore_started.set()
        try:
            self.release_restores.wait(WAIT_SECONDS)
        finally:
            with self._guard:
                self.active_restores -= 1


def execute_in_thread(
    runner_: DockerCodeRunner, repo_: Path, tmp_path: Path, repo_id: str
) -> tuple[threading.Thread, list[ExecutionResult | BaseException]]:
    outcome: list[ExecutionResult | BaseException] = []

    def target() -> None:
        try:
            outcome.append(
                runner_.execute(
                    request(repo_, tmp_path, repo_id=repo_id, logs_dir=tmp_path / repo_id)
                )
            )
        except BaseException as error:  # reported by the test
            outcome.append(error)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def test_parallel_executions_restore_one_at_a_time_but_prepare_together(
    repo: Path, tmp_path: Path
) -> None:
    engine = BlockingRestoreEngine()
    shared = runner(engine)

    first, first_outcome = execute_in_thread(shared, repo, tmp_path, "dana__api")
    second, second_outcome = execute_in_thread(shared, repo, tmp_path, "noam__api")
    assert engine.restore_started.wait(WAIT_SECONDS)
    engine.restore_started.clear()
    # The prepare steps met, so the other execution is about to restore; it must wait.
    assert not engine.restore_started.wait(SECOND_RESTORE_GRACE_SECONDS)
    engine.release_restores.set()
    first.join(WAIT_SECONDS)
    second.join(WAIT_SECONDS)

    assert not engine.prepares_met.broken
    assert engine.restores_started == 2
    assert engine.most_active_restores == 1
    for outcome in (first_outcome, second_outcome):
        [result] = outcome
        assert isinstance(result, ExecutionResult), result
        assert result.steps == (StepStatus.SUCCEEDED,) * 3
    assert engine.leftovers == 0


def test_a_restore_that_raises_releases_the_lock(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine(fail_on={"start:restore": RuntimeError("restore crashed")})
    shared = runner(engine)
    with pytest.raises(RuntimeError, match="restore crashed"):
        shared.execute(request(repo, tmp_path, logs_dir=tmp_path / "first"))
    engine.fail_on.clear()

    thread, outcome = execute_in_thread(shared, repo, tmp_path, "noam__api")
    thread.join(WAIT_SECONDS)

    assert not thread.is_alive(), "the second restore never got the lock"
    [result] = outcome
    assert isinstance(result, ExecutionResult), result
    assert result.steps == (StepStatus.SUCCEEDED,) * 3
    assert len(engine.specs("restore")) == 2
