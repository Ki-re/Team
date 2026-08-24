import pytest
from add import add

def test_add_returns_sum_of_two_numbers():
    assert add(2, 3) == 5

def test_add_returns_sum_of_negative_numbers():
    assert add(-1, -1) == -2

def test_add_returns_sum_of_zero_and_number():
    assert add(0, 5) == 5

def test_add_returns_sum_of_floats():
    assert add(1.5, 2.5) == 4.0
