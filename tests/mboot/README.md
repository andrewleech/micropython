# Mboot host and hardware tests

These suites exercise MicroPython's Mboot configuration/update policy with the unmodified MCUboot bootutil engine. The shared DFU suite lives in `shared/mboot/dfu/tests`; the flash, filesystem and bootloader models here are test environments, not production backends.

## Host commands

Run from the repository root with the relevant submodules and Python signing dependencies installed:

```sh
python3 -m unittest discover -s tests/mboot -p 'test_*.py'
make -C shared/mboot/dfu/tests check
make -C tests/mboot/host unit
make -C tests/mboot/host/core test
make -C tests/mboot/host/bootloader test
make -C tests/mboot/host/bootloader sweep
make -C tests/mboot/host test GLUE=shared
make -C tests/mboot/fuzz test
```

`host/boards.mk` selects representative flash layouts and policies. `host/core` tests flash access/ECC policy, request handoff and security counters; `host/bootloader` tests real boot flow, DFU/filesystem updates and power-cut recovery; `host` sweeps MCUboot swap/revert against local or shared flash-map glue. `fuzz` contains filesystem/stream tests and libFuzzer targets. Each Makefile documents its policy, board and sweep options.

The simulator in `lib/mcuboot/sim` uses its own engine configuration and flash map. `run_sim.py` describes that boundary; it is not a substitute for the shared-policy or port tests.

## Signed application installation

Build an STM32 application with `MBOOT_BACKEND=mcuboot`, then pass the signed image to the host bootloader:

```sh
make -C ports/stm32 BOARD=NUCLEO_H563ZI MBOOT_BACKEND=mcuboot
make -C tests/mboot/host/bootloader test-app APP="$PWD/ports/stm32/build-NUCLEO_H563ZI-mboot/firmware.signed.bin"
```

`test_layout.py` and `test_layout_features.py` exercise derived geometry, rejected layouts, policy choices and signing metadata. `host/ldtest` links an application to exercise the generated linker contract. `host/test_tools.py` covers image handling, DFU result decoding and fault-sweep selection.

## Hardware

`hw/dfu_confine.py`, `hw/powercut.py`, `hw/tear_probe.py` and `fi_sweep.py` operate on real targets. Read their command-line options before use and identify the debug probe by its serial number. `dfu_raw.py` supplies the standard DFU/vendor protocol used by this facility; the established STM32 DfuSe implementation has a different transfer contract.

The optional runtime module and persistent update-history suites belong to separate follow-ups, not this main integration.
