"""The C# file the runner adds to the Web project in the run's copy: it creates the tables.

ASP.NET Core runs every `IHostingStartup` of the assemblies named in
`ASPNETCORE_HOSTINGSTARTUPASSEMBLIES` while the host is built, so this file needs no change
to the student's `Program.cs`. It registers a hosted service that finds every `DbContext`
in the app's own assemblies, creates its tables, and prints one marker line.

The file uses reflection only: it has no compile-time reference to Entity Framework, so the
student's EF version, or a project with no EF reference at all, cannot break the build. It
uses fully qualified names from the Web SDK's shared framework (nothing from the student's
`ImplicitUsings`), turns warnings off, and never throws: a failure becomes a marker line.
The file holds no text of the student's.
"""

from pathlib import Path

MARKER_PREFIX = "[hw-checker-schema] "
"""The start of the marker line the file prints; see `schema_step.parse_schema_marker`."""


TEMPLATE_PATH = Path(__file__).with_name("schema_startup.cs")
"""The C# file, kept as a file of its own; `__MARKER__` stands for the marker line's start."""

STARTUP_SOURCE = TEMPLATE_PATH.read_text(encoding="utf-8").replace("__MARKER__", MARKER_PREFIX)
"""The whole file; the same for every repo (it holds nothing of the student's)."""
