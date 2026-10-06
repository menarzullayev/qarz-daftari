import pytest

from qarz.domain.names import normalize_name, unify_apostrophes


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Alisher", "alisher"),
        ("Алишер", "alisher"),
        ("алишер", "alisher"),
        ("АЛИШЕР", "alisher"),
        ("  Ali   Valiyev \t", "ali valiyev"),
        ("Ali\u00a0Valiyev", "ali valiyev"),
        ("Ғани", "g'ani"),
        ("G'ani", "g'ani"),
        ("G\u02bbani", "g'ani"),
        ("G`ani", "g'ani"),
        ("G\u2019ani", "g'ani"),
        ("G\u2018ani", "g'ani"),
        ("G\u02bcani", "g'ani"),
        ("Ўктам", "o'ktam"),
        ("O'ktam", "o'ktam"),
        ("Шариф", "sharif"),
        ("Sharif", "sharif"),
        ("Чори", "chori"),
        ("Chori", "chori"),
        ("Юсуф", "yusuf"),
        ("Yusuf", "yusuf"),
        ("Яхё", "yahyo"),
        ("Яҳё", "yahyo"),
        ("Yahyo", "yahyo"),
        ("Yaxyo", "yahyo"),  # x and h are one letter for matching
        ("Хуршид", "hurshid"),
        ("Xurshid", "hurshid"),
        ("Қодир", "qodir"),
        ("Qodir", "qodir"),
        ("Маъруф", "ma'ruf"),  # hard sign is an apostrophe
        ("Ma'ruf", "ma'ruf"),
        ("Ильхом", "ilhom"),  # soft sign is dropped
        ("Ilhom", "ilhom"),
        ("Игорь", "igor"),
        ("Елена", "yelena"),  # e at the start of a word
        ("Yelena", "yelena"),
        ("Алексеев", "alekseyev"),  # e after a vowel
        ("Aлексеев", "alekseyev"),  # the same after a Latin vowel in a mixed-script name
        ("Васильев", "vasilyev"),  # e after a soft sign
        ("Подъезд", "pod'yezd"),  # e after a hard sign
        ("Анна-Елена", "anna-yelena"),  # a hyphen starts a new word
        ("Шерзод", "sherzod"),  # e after a consonant
        ("Ёқуб", "yoqub"),
        ("Цой", "tsoy"),
        ("Щукин", "shukin"),
        ("Рыбаков", "ribakov"),
        ("Эльдор", "eldor"),
        ("Жасур", "jasur"),
        ("Йўлдош", "yo'ldosh"),
        ("Yo'ldosh", "yo'ldosh"),
        ("И\u0306ўлдош", "yo'ldosh"),  # decomposed letters compose first
        ("", ""),
        ("   ", ""),
        ("Ali 2", "ali 2"),
        ("Әли", "әli"),  # a letter of another alphabet is kept as it is
    ],
)
def test_normalize_name(name: str, expected: str) -> None:
    assert normalize_name(name) == expected


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Ali", "Aliy"),
        ("Gani", "G'ani"),  # a missing apostrophe is a different name
        ("Oktam", "Ўктам"),
        ("Elena", "Елена"),
        ("Jamshid", "Джамшид"),
        ("Yo'ldosh", "Юлдаш"),
        ("Abdulla", "Abdullah"),
        ("Ali", "Vali"),
        ("Alisher", "Alisher aka"),
    ],
)
def test_normalize_name_keeps_these_names_apart(left: str, right: str) -> None:
    assert normalize_name(left) != normalize_name(right)


@pytest.mark.parametrize("name", ["Alisher", "Ғани", "  O\u2018ktam   aka ", "Яхё", "\x00\ud800\u202e", "💥" * 50])
def test_normalize_name_is_idempotent_and_total(name: str) -> None:
    once = normalize_name(name)
    assert normalize_name(once) == once


def test_unify_apostrophes() -> None:
    assert unify_apostrophes("a'b\u02bcc\u2019d\u2018e\u02bbf`g") == "a'b'c'd'e'f'g"
    assert unify_apostrophes("no apostrophes") == "no apostrophes"
