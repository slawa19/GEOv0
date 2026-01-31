from decimal import Decimal

from app.core.simulator.net_balance_utils import atoms_to_net_sign, net_decimal_to_atoms


def test_net_decimal_to_atoms_and_sign_precision2() -> None:
    # precision=2 -> atoms are cents
    atoms = net_decimal_to_atoms(Decimal("1.23"), precision=2)
    assert atoms == 123
    assert atoms_to_net_sign(atoms) == 1

    atoms = net_decimal_to_atoms(Decimal("-1.23"), precision=2)
    assert atoms == -123
    assert atoms_to_net_sign(atoms) == -1

    atoms = net_decimal_to_atoms(Decimal("0"), precision=2)
    assert atoms == 0
    assert atoms_to_net_sign(atoms) == 0


def test_net_decimal_to_atoms_round_half_up() -> None:
    # ROUND_HALF_UP semantics are relied on by snapshot/viz patch code.
    assert net_decimal_to_atoms(Decimal("0.005"), precision=2) == 1
    assert net_decimal_to_atoms(Decimal("-0.005"), precision=2) == -1
