"""Tracing lessons: letter, number and word rotation, and start-dot coverage."""
import string

from generators.activity_sheets.tracing import MAX_TRACE_WORD, STROKE_START, plan_lessons


def test_lessons_rotate_and_advance():
    lessons = plan_lessons(6, ["cow", "pig", "horse", "chicken", "tractor", "farmer", "watermelon"])
    assert [lesson.kind for lesson in lessons] == ["letters", "words", "letters", "numbers", "letters", "words"]
    assert lessons[0].rows == ("A", "a", "B", "b") and lessons[0].title == "Trace the Letters A and B"
    assert lessons[2].rows == ("C", "c", "D", "d")
    assert lessons[3].rows == ("1", "2", "3", "4", "5") and lessons[3].title == "Trace the Numbers 1 to 5"
    assert lessons[1].rows == ("cow", "pig", "horse", "chicken", "tractor")
    assert lessons[5].rows[0] == "farmer"
    assert all(len(w) <= MAX_TRACE_WORD for lesson in lessons if lesson.kind == "words" for w in lesson.rows)


def test_no_words_means_letters_and_numbers_only():
    lessons = plan_lessons(4, [])
    assert {lesson.kind for lesson in lessons} == {"letters", "numbers"}


def test_letters_wrap_after_z():
    lessons = plan_lessons(60, [], letters_per_page=2)
    letters = [lesson.subject for lesson in lessons if lesson.kind == "letters"]
    assert letters[12] == ("Y", "Z") and letters[13] == ("A", "B")


def test_every_glyph_has_a_start_dot():
    for ch in string.ascii_letters + string.digits:
        fx, fy = STROKE_START[ch]
        assert 0 <= fx <= 1 and 0 < fy <= 1
