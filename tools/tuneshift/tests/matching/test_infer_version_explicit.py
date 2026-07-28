"""infer_version honors a structured explicit flag over title text (Task 3)."""

from tuneshift.matching.version import infer_version


def test_structured_explicit_true_sets_is_explicit():
    p = infer_version("Song", "Album", None, explicit=True)
    assert p.is_explicit is True and p.is_clean is False


def test_structured_explicit_false_records_rating_not_clean_variant():
    # BUG-13: explicit=False means "contains no explicit content" - true of most
    # music. It is NOT an affirmatively marked censored edit of an explicit
    # master, which is what is_clean means everywhere else (it is why
    # source.is_explicit + candidate.is_clean hard-REJECTs). Conflating the two
    # charged every ordinary clean recording a substitute-grade penalty.
    p = infer_version("Song", "Album", None, explicit=False)
    assert p.explicit_rating is False
    assert p.is_clean is False and p.is_explicit is False


def test_structured_explicit_false_still_honours_a_clean_marker():
    # The rating does not suppress a real variant marker in the title.
    p = infer_version("Song (Clean)", "Album", None, explicit=False)
    assert p.is_clean is True and p.explicit_rating is False


def test_unknown_flag_falls_back_to_text_regex():
    # No structured flag, no marker -> neutral (parity with today).
    p = infer_version("Song", "Album", None)
    assert p.is_explicit is False and p.is_clean is False
    # Title marker still works when the flag is unknown.
    assert infer_version("Song (Clean)", "Album", None).is_clean is True
