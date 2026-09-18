from app.commands import parse_comment, DuplicateCommand, NotDuplicateCommand


def test_duplicate_with_hash():
    cmd = parse_comment("/duplicate of #42")
    assert isinstance(cmd, DuplicateCommand)
    assert cmd.canonical_number == 42


def test_duplicate_without_hash():
    cmd = parse_comment("/duplicate of 42")
    assert isinstance(cmd, DuplicateCommand)
    assert cmd.canonical_number == 42


def test_duplicate_case_insensitive():
    cmd = parse_comment("/Duplicate Of #7")
    assert isinstance(cmd, DuplicateCommand)
    assert cmd.canonical_number == 7


def test_duplicate_in_multiline_comment():
    cmd = parse_comment("Thanks for the report!\n/duplicate of #99\nSee that issue.")
    assert isinstance(cmd, DuplicateCommand)
    assert cmd.canonical_number == 99


def test_not_duplicate():
    cmd = parse_comment("/not-duplicate")
    assert isinstance(cmd, NotDuplicateCommand)


def test_not_duplicate_case_insensitive():
    cmd = parse_comment("/Not-Duplicate")
    assert isinstance(cmd, NotDuplicateCommand)


def test_unrelated_comment():
    cmd = parse_comment("Great idea, I'll look into it!")
    assert cmd is None


def test_partial_match_not_triggered():
    cmd = parse_comment("This is not a duplicate command duplicate of #1")
    assert cmd is None
