from epic_x import square

def test_square():
    assert square(3) == 9
    assert square(0) == 0
    assert square(-2) == 4
    assert square(5.5) == 30.25