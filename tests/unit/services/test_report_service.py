from pathlib import Path

import pytest

from hw_checker.domain import Assignment
from hw_checker.domain.errors import StorageError
from hw_checker.services.reports import ReportService
from tests.fakes.reporting import FakeReportWriter
from tests.unit.domain import builders
from tests.unit.reporting import reports_data as data


def assignments() -> tuple[Assignment, ...]:
    return (
        Assignment(
            id="_general",
            order=0,
            title="General",
            requirements=(builders.requirement(id="gen-naming-methods", title="Naming"),),
        ),
        Assignment(
            id="hw01",
            order=1,
            title="Users API",
            requirements=(builders.requirement(id="hw01-create", title="POST creates a user"),),
        ),
    )


def test_repo_report_gets_every_requirement_title_and_the_partner() -> None:
    writer = FakeReportWriter()

    path = ReportService(writer).write_repo_report(
        data.RUN_ID, "hw01", data.full_repo_run(), assignments(), partner="Yael Testberg"
    )

    [report] = writer.repo_reports
    assert report.requirement_titles == {
        "gen-naming-methods": "Naming",
        "hw01-create": "POST creates a user",
    }
    assert (report.run_id, report.assignment_id, report.partner) == (
        data.RUN_ID,
        "hw01",
        "Yael Testberg",
    )
    assert path == Path("reports", data.RUN_ID, "noa__webapi.md")


def test_repo_report_failure_is_raised_to_the_caller() -> None:
    service = ReportService(FakeReportWriter(failing_repos=["noa__webapi"]))

    with pytest.raises(StorageError, match="disk full"):
        service.write_repo_report(data.RUN_ID, "hw01", data.full_repo_run(), assignments())


def test_round_report_returns_the_summary_pointing_to_the_files() -> None:
    writer = FakeReportWriter()
    report = data.round_report(Path("reports"))

    summary = ReportService(writer).write_round_report(
        report.summary, report.repos, report.partners, report.roster_warnings
    )

    [written] = writer.round_reports
    assert written == report
    assert summary.reports_dir == Path("reports", data.RUN_ID)
    assert summary.round_report == Path("reports", data.RUN_ID, "round.md")
    assert summary.repos == report.summary.repos
