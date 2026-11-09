# Local architecture evidence

`ArchitectureEvidenceReader.scan(root)` is a read-only, repository-local capability for
collecting project inventory and a small set of C# syntax signals. It is not connected to
the round graph, assignment selection, findings, severities, fixer, LLM or publishing.
Call it with the root of one already-checked local repository; it does not fetch, build,
execute, or transmit repository content.

The immutable `ArchitectureEvidence` result contains:

- relative `.csproj` paths and literal SDK references;
- relative `ProjectReference` targets with a lexical match/missing/unsupported status;
- signal kind, relative file, line, and only the identifier(s) supporting each candidate;
- separate `inventory_coverage` and `signal_coverage` statuses and structured limitations.

Source text, absolute repository paths, names of students and raw operating-system errors
are not retained. Project references are normalized against the source project's directory,
then compared only with the bounded local `.csproj` inventory. An absolute, escaping,
wildcard or MSBuild-expression reference is not followed or opened. XML line numbers are
not available from the current standard-library reader and are therefore omitted.
Project files and C# source must decode as UTF-8 (an optional project-file BOM is accepted).

The local adapter skips `bin/`, `obj/`, `.git/`, `.vs/`, generated C# suffixes, migration
and properties source folders, symlinks and junctions. It bounds directory/file counts,
per-file bytes, project-file bytes and total C# bytes. Unreadable, malformed, oversized,
invalid-UTF-8, unsafe, unsupported and truncated inputs lower coverage rather than
producing a clean empty result.

## What signals mean

An interface declaration, constructor candidate, constructor parameter whose simple name
matches a discovered interface declaration, or generic `AddSingleton`/`AddScoped`/
`AddTransient` call is only a lexical candidate. It does not prove that the names resolve,
that the registration is active, that a constructor is used, that every dependency uses
injection, or that any course requirement is met or violated. The reader does not evaluate
MSBuild, C# aliases, namespaces, overloads, conditional compilation, syntax trees, semantic
types, generated source or runtime behavior. C# coverage is consequently marked partial
even when the supported scan encounters no specific ambiguity; absence of a signal must
not be treated as proof of absence.

The reader reuses the existing C# comment/string masker and declaration reader. This stage
makes zero LLM calls. It does not
reuse the Web-project discovery operation as a classifier: that operation chooses a
project for execution and does not represent a complete project-reference inventory.
The existing general `SourceFiles` capability is for fixer working copies, has no bounded
inventory result, and is not appropriate for this untrusted read-only scan.

## Later pilot and cost measurement

Keep the first pilot local, deterministic, and without a fixer or new LLM role. After
separate approval of a small sample, record by stage and role: request/call count, input
and output tokens, cache hits where observable, real premium requests or billing when
available, and adjudicated signal accuracy plus coverage and ambiguity rates. Compare
one versus two fixer attempts only in a separately approved follow-up. Token counts are
not dollar estimates, and cache hits or future savings must not be assumed.
