from hw_checker.adapters.docker_runner.excerpt import GAP_MARKER, excerpt_log
from hw_checker.domain.execution import MAX_LOGS_EXCERPT_CHARS

BUILD_ERROR = (
    "/work/src/Controllers/ProductsController.cs(19,19): error CS0103: The name 'produts' "
    "does not exist in the current context [/work/src/SampleWebApi.csproj]"
)
NUGET_ERROR = "/work/src/SampleWebApi.csproj : error NU1101: Unable to find package X."
STARTUP_CRASH = "Unhandled exception. System.InvalidOperationException: startup failure"


def noisy_log(lines: int) -> list[str]:
    return [f"  Restoring step {index:05d} for project SampleWebApi" for index in range(lines)]


def test_short_log_is_kept_whole() -> None:
    log = "  Determining projects to restore...\n  Restored /work/src/SampleWebApi.csproj\n"

    assert excerpt_log(log) == log


def test_crlf_log_is_normalized_and_kept_whole() -> None:
    assert excerpt_log("line one\r\nline two\r\n") == "line one\nline two\n"


def test_ansi_color_codes_are_stripped() -> None:
    log = "\x1b[31;1merror CS0103\x1b[0m: The name 'x' does not exist\n\x1b[32mdone\x1b[0m"

    assert excerpt_log(log) == "error CS0103: The name 'x' does not exist\ndone"


def test_long_log_puts_error_lines_first_then_the_tail() -> None:
    lines = [*noisy_log(200), BUILD_ERROR, *noisy_log(200), NUGET_ERROR, *noisy_log(400)]
    lines += ["Build FAILED.", "    1 Error(s)"]

    excerpt = excerpt_log("\n".join(lines), limit=2000)

    parts = excerpt.split("\n")
    assert parts[:2] == [BUILD_ERROR, NUGET_ERROR]
    assert parts[2] == GAP_MARKER
    assert parts[-2:] == ["Build FAILED.", "    1 Error(s)"]


def test_unhandled_exception_counts_as_an_error_line() -> None:
    lines = [*noisy_log(300), STARTUP_CRASH, *noisy_log(300)]

    assert excerpt_log("\n".join(lines), limit=1000).split("\n")[0] == STARTUP_CRASH


def test_excerpt_respects_the_limit_and_keeps_whole_lines() -> None:
    lines = [*noisy_log(1000), BUILD_ERROR, *noisy_log(1000)]
    original = set(lines) | {GAP_MARKER}

    excerpt = excerpt_log("\n".join(lines))

    assert len(excerpt) <= MAX_LOGS_EXCERPT_CHARS
    assert all(part in original for part in excerpt.split("\n"))


def test_tail_does_not_repeat_an_error_line() -> None:
    lines = [*noisy_log(300), BUILD_ERROR]

    excerpt = excerpt_log("\n".join(lines), limit=1500)

    assert excerpt.count(BUILD_ERROR) == 1


def test_a_line_longer_than_the_limit_is_left_out_rather_than_cut() -> None:
    long_line = "x" * 500
    lines = ["first", long_line, "last"]

    excerpt = excerpt_log("\n".join(lines), limit=100)

    assert long_line not in excerpt
    assert excerpt.split("\n")[-1] == "last"
    assert len(excerpt) <= 100
