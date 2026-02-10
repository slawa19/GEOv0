"""Legacy simulator scenario generators.

This script contains the deprecated generators that used to live in
`scripts/generate_simulator_seed_scenarios.py`.

The canonical generators (kept active) are the realistic-v2 scenarios.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_main_generator_module():
	path = ROOT / "scripts" / "generate_simulator_seed_scenarios.py"
	spec = importlib.util.spec_from_file_location("_geo_sim_seed_gen_main", path)
	if spec is None or spec.loader is None:
		raise RuntimeError(f"Failed to load module spec for {path}")
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	return mod


_gen = _load_main_generator_module()


def generate_greenfield_v1() -> None:
	datasets = _gen.ROOT / "admin-fixtures" / "v1" / "datasets"
	participants = _gen._read_json(datasets / "participants.json")
	trustlines = _gen._read_json(datasets / "trustlines.json")
	eq_defs = _gen._read_json(datasets / "equivalents.json")

	scenario = _gen._convert_to_scenario(
		seed_id="greenfield-village-100",
		participants=participants,
		trustlines=trustlines,
		eq_defs=eq_defs,
	)
	_gen._assert_basic_integrity(scenario)

	out_path = _gen.ROOT / "fixtures" / "simulator" / "greenfield-village-100" / "scenario.json"
	_gen._write_json(out_path, scenario)
	_gen._validate_scenario_shape(out_path)
	print("Wrote", out_path)


def generate_greenfield_v2() -> None:
	tools_dir = _gen.ROOT / "admin-fixtures" / "tools"
	seed_path = tools_dir / "generate_seed_greenfield_village_100_v2.py"
	seed = _gen._seed_module_by_path(seed_path, "_seed_greenfield_village_100_v2")

	scenario = _gen._scenario_from_seed_module(
		seed_id="greenfield-village-100-v2",
		seed_module=seed,
	)

	out_path = _gen.ROOT / "fixtures" / "simulator" / "greenfield-village-100-v2" / "scenario.json"
	_gen._write_json(out_path, scenario)
	_gen._validate_scenario_shape(out_path)
	print("Wrote", out_path)


def generate_riverside_v1() -> None:
	tools_dir = _gen.ROOT / "admin-fixtures" / "tools"
	seed_path = tools_dir / "generate_seed_riverside_town_50.py"
	seed = _gen._seed_module_by_path(seed_path, "_seed_riverside_town_50")

	scenario = _gen._scenario_from_seed_module(seed_id="riverside-town-50", seed_module=seed)

	out_path = _gen.ROOT / "fixtures" / "simulator" / "riverside-town-50" / "scenario.json"
	_gen._write_json(out_path, scenario)
	_gen._validate_scenario_shape(out_path)
	print("Wrote", out_path)


def generate_riverside_v2() -> None:
	tools_dir = _gen.ROOT / "admin-fixtures" / "tools"
	seed_path = tools_dir / "generate_seed_riverside_town_50_v2.py"
	seed = _gen._seed_module_by_path(seed_path, "_seed_riverside_town_50_v2")

	scenario = _gen._scenario_from_seed_module(seed_id="riverside-town-50-v2", seed_module=seed)

	out_path = _gen.ROOT / "fixtures" / "simulator" / "riverside-town-50-v2" / "scenario.json"
	_gen._write_json(out_path, scenario)
	_gen._validate_scenario_shape(out_path)
	print("Wrote", out_path)


def main() -> int:
	generate_greenfield_v1()
	generate_greenfield_v2()
	generate_riverside_v1()
	generate_riverside_v2()
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
