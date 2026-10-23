# MicroPython build inputs

This package validates compilation database coverage and collects selected native and frozen-source scope from a MicroPython build. It does not run scanners, generate an SBOM or generate typing stubs.

Install it alongside the feature tooling from the same checkout:

```sh
python3 -m pip install ./tools/mpy_build_inputs
mpy-compdb-check --help
mpy-build-scope --help
```

Source-tree use needs `tools` on `PYTHONPATH`:

```sh
PYTHONPATH=tools python3 -m mpy_build_inputs.compdb --help
PYTHONPATH=tools python3 -m mpy_build_inputs.scope --help
PYTHONPATH=tools python3 -m pytest tools/mpy_build_inputs/tests
```

`compdb` validates compiler records and checks that build objects are accounted for. `scope` uses those selected inputs, source ownership and the selected checkout's `tools/manifestfile.py` authority to describe native and frozen inputs. Paths, manifests and owners remain explicit caller inputs; another target's build is not a substitute for the selected build.

The implementation and tests are extracted from `tools/mpy_analysis` at `sbom/g5-build-link` revision `2dfa1ab`. The neutral package is carried on both feature branches so either can be reviewed against the MicroPython baseline without the other feature. Changes to this layer need to be kept consistent between those branches until a common upstream version exists.
