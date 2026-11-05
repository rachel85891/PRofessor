from hw_checker.checks.deterministic.source_text import scan_source


def texts(source: str) -> list[str]:
    return [literal.text for literal in scan_source(source).literals]


def test_comments_are_masked_and_their_quotes_ignored() -> None:
    source = '// say "hi"\n/* "block"\n "more" */ var x = "real";\n'

    scan = scan_source(source)

    assert texts(source) == ["real"]
    assert len(scan.masked) == len(source)
    assert scan.masked.count("\n") == source.count("\n")
    assert "hi" not in scan.masked and "block" not in scan.masked


def test_quoted_strings_keep_escapes_and_lines() -> None:
    scan = scan_source('a = "x\\"y";\nb = \'z\';\n')

    assert [(scan.line_of(lit.start), lit.text) for lit in scan.literals] == [
        (1, 'x\\"y'),
        (2, "z"),
    ]
    assert scan.masked.startswith('a = "    ";')


def test_template_literals_split_at_interpolations() -> None:
    (literal,) = scan_source("fetch(`api/users/${user.id}?q=${f(`n${m}`)}`)").literals

    assert literal.parts == ("api/users/", None, "?q=", None)
    assert not literal.fixed


def test_regex_literals_are_not_read_as_strings() -> None:
    assert texts('const r = /"[a-z]+"/g; let d = a / b / c; s = "ok";') == ["ok"]


def test_csharp_verbatim_interpolated_and_raw_strings() -> None:
    source = 'var p = @"D:\\data\\x.txt"; var q = $"api/{id}/x {{y}}"; var r = """\nraw "x"\n""";'

    literals = scan_source(source).literals

    assert [lit.text for lit in literals] == ["D:\\data\\x.txt", "api/{id}/x {{y}}", '\nraw "x"\n']
    assert literals[1].parts == ("api/", None, "/x {y}")


def test_literal_at_finds_a_literal_by_its_start() -> None:
    scan = scan_source('f("a", "b")')

    second = scan.literals[1]
    assert scan.literal_at(second.start) == second
    assert scan.literal_at(0) is None
