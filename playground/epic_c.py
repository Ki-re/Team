from epic_a import is_positive
from epic_b import is_negative

def describe_sign(n):
    if is_positive(n):
        return 'positivo'
    elif is_negative(n):
        return 'negativo'
    else:
        return 'cero'