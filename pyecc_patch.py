# pyecc_patch.py — iterative FQP.__pow__ patch for py_ecc on CPython 3.12+
# py_ecc's recursive square-and-multiply in FQP.__pow__ blows CPython 3.12's
# C-level recursion guard (Py_C_RECURSION_LIMIT) during pairing's final
# exponentiation (~6000 dispatch frames). setrecursionlimit cannot raise it.
# Import this module BEFORE calling py_ecc.bn128.pairing.
import sys
from py_ecc.fields import field_elements


def _iter_pow(self, other):
    other = int(other)
    cls = type(self)
    n = len(self.coeffs)
    identity = cls([1] + [0] * (n - 1))
    if other == 0:
        return identity
    result = None
    base = self
    while other > 0:
        if other & 1:
            result = base if result is None else result * base
        other >>= 1
        if other:
            base = base * base
    return result if result is not None else identity


field_elements.FQP.__pow__ = _iter_pow
