"""The committed assignments/ folder must always load; this stage ships only _general."""

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.config.settings import REPO_ROOT


def test_committed_assignments_folder_loads_with_only_general() -> None:
    assignments = YamlAssignmentSource(REPO_ROOT / "assignments").load_all()

    assert [assignment.id for assignment in assignments] == ["_general"]
    assert assignments[0].order == 0
    assert assignments[0].requirements == ()
