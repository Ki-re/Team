from tricky import to_roman


def test_to_roman_exact_boundary_values():
    assert to_roman(1000) == "M"
    assert to_roman(900) == "CM"
    assert to_roman(500) == "D"
    assert to_roman(400) == "CD"
    assert to_roman(90) == "XC"
    assert to_roman(50) == "L"
    assert to_roman(40) == "XL"
    assert to_roman(9) == "IX"
    assert to_roman(5) == "V"
    assert to_roman(4) == "IV"
    assert to_roman(1) == "I"


def test_to_roman_composite_values():
    assert to_roman(1994) == "MCMXCIV"
    assert to_roman(2026) == "MMXXVI"
    assert to_roman(58) == "LVIII"
    assert to_roman(3999) == "MMMCMXCIX"
