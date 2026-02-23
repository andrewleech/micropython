# MicroPython Code Coverage Toolchain

Split-architecture coverage toolchain: a lightweight on-device tracer (MicroPython) paired with host-side reporting via coverage.py.

## Prerequisites

**Build:**
```bash
cd mpy-cross && make                              # needed for mpy pathway
cd ports/unix && make submodules && make VARIANT=coverage  # unix test binary
```

**Host Python packages:**
```bash
pip install coverage mpy-cross
```

**Hardware targets:** firmware must be built with `MICROPY_PY_SYS_SETTRACE=1`.

## Quick Start (CLI)

The `mpy_coverage_cli.py` wrapper handles instrumentation, data collection, and reporting. Each test run stores a timestamped JSON file; the report command merges all collected data.

```bash
# Collect coverage for individual tests
python3 mpy_coverage_cli.py run test_foo.py --include myapp
python3 mpy_coverage_cli.py run test_bar.py --include myapp

# Generate merged report
python3 mpy_coverage_cli.py report --method auto --show-missing

# List collected data files
python3 mpy_coverage_cli.py list

# Remove collected data
python3 mpy_coverage_cli.py clean
```

The micropython binary is auto-detected from `ports/unix/build-coverage/micropython` (relative to the script) or `micropython` in PATH. Override with `--micropython`.

### Hardware targets

```bash
# Auto-deploys mpy_coverage.py to device, runs test, collects data
python3 mpy_coverage_cli.py run test_foo.py \
    --device /dev/serial/by-id/usb-... \
    --include myapp

# Skip auto-deploy if mpy_coverage.py is already on device
python3 mpy_coverage_cli.py run test_foo.py \
    --device /dev/serial/by-id/usb-... \
    --no-deploy --include myapp
```

### Multi-pass workflow

Run each test separately to accumulate coverage data, then generate a single merged report:

```bash
python3 mpy_coverage_cli.py run tests/test_network.py --include myapp
python3 mpy_coverage_cli.py run tests/test_storage.py --include myapp
python3 mpy_coverage_cli.py run tests/test_ui.py --include myapp

# Report merges all .json files from the data directory
python3 mpy_coverage_cli.py report --show-missing --format html --output-dir htmlcov
```

Data files are stored in `.mpy_coverage/` by default (override with `--data-dir`).

## Quick Start (Manual)

For direct control over the tracer without the CLI wrapper:

```python
# On MicroPython (unix coverage variant or settrace-enabled firmware)
import mpy_coverage

mpy_coverage.start(include=['myapp'], collect_executable=True)
import myapp
myapp.main()
mpy_coverage.stop()
mpy_coverage.export_json('coverage.json')
```

```bash
# On host
python3 mpy_coverage_report.py coverage.json --method co_lines --show-missing
```

## Executable Line Detection Pathways

| Method | Where | Pros | Cons |
|--------|-------|------|------|
| `co_lines` | On-device | No host tools needed, exact MicroPython view | Only sees called functions |
| `ast` | Host CPython | Sees all code, matches coverage.py conventions | May differ from MicroPython's view |
| `mpy` | Host via mpy-cross | Exact MicroPython bytecode view, sees all code | Requires mpy-cross binary |

Use `--method auto` (default) which uses `mpy` — the most accurate method for MicroPython since it reflects the actual bytecode the VM executes. The `ast` method uses CPython's parser which may disagree with MicroPython's grammar on edge cases.

## On-Device Tracer API (`mpy_coverage.py`)

```python
import mpy_coverage

# Functional API
mpy_coverage.start(include=['mymod'], exclude=['test_'], collect_executable=False)
# ... run code ...
mpy_coverage.stop()
data = mpy_coverage.get_data()
mpy_coverage.export_json('out.json')    # to file
mpy_coverage.export_json()              # to stdout with serial delimiters

# Context manager
with mpy_coverage.coverage(include=['mymod'], collect_executable=True):
    import mymod
    mymod.run()
```

Filtering uses substring matching on filenames. `mpy_coverage` itself is always excluded.

## Hardware Usage (Manual)

1. Build firmware with `MICROPY_PY_SYS_SETTRACE=1`
2. Deploy `mpy_coverage.py` to device
3. Run coverage collection — `export_json()` with no argument prints delimited JSON to stdout:
   ```
   ---MPY_COV_START---
   {"executed": {...}}
   ---MPY_COV_END---
   ```
4. Capture serial output, extract the JSON block, save to file
5. Run `mpy_coverage_report.py` with `--path-map` to remap device paths:
   ```bash
   python3 mpy_coverage_report.py serial_capture.json \
       --method ast \
       --path-map "/flash/lib/=./src/" \
       --source-root ./project \
       --show-missing
   ```

## CLI Reference

### mpy_coverage_cli.py

```
python3 mpy_coverage_cli.py [--data-dir DIR] COMMAND [OPTIONS]

Commands:
  run       Collect coverage for a test script
  report    Generate merged coverage report from collected data
  list      List collected coverage data files
  clean     Remove collected coverage data

Global options:
  --data-dir DIR    Directory for coverage data files (default: .mpy_coverage)
```

**run options:**
```
  test_script              Python test script to run
  --device PATH            Hardware target (triggers mpremote flow)
  --micropython PATH       Path to micropython binary (unix port)
  --include PATTERN        Filename substring filter to include (repeatable)
  --exclude PATTERN        Filename substring filter to exclude (repeatable)
  --no-deploy              Skip auto-deploy of mpy_coverage.py to device
```

**report options:**
```
  --method {auto,co_lines,ast,mpy}   Executable line detection method (default: auto)
  --source-root DIR                   Root directory for source files
  --mpy-cross PATH                   Path to mpy-cross binary
  --mpy-tools-dir DIR                Path to MicroPython tools/ directory
  --path-map PREFIX=REPLACEMENT      Device-to-host path mapping (repeatable)
  --format {text,html,json,xml,lcov} Output format (repeatable, default: text)
  --output-dir DIR                   Output directory for report files
  --show-missing                     Show missing line numbers in text report
```

**clean options:**
```
  --yes, -y    Skip confirmation prompt
```

### mpy_coverage_report.py (standalone)

```
python3 mpy_coverage_report.py DATA_FILE [OPTIONS]

Options:
  --method {auto,co_lines,ast,mpy}   Executable line detection method
  --source-root DIR                   Root directory for source files
  --mpy-cross PATH                   Path to mpy-cross binary (mpy method)
  --mpy-tools-dir DIR                Path to MicroPython tools/ directory
  --path-map PREFIX=REPLACEMENT      Device-to-host path mapping (repeatable)
  --format {text,html,json,xml,lcov} Output format (repeatable)
  --output-dir DIR                   Output directory for generated reports
  --show-missing                     Show missing line numbers in text report
```

## JSON Data Format

```json
{
  "executed": {
    "filename.py": [1, 3, 5, 7]
  },
  "executable": {
    "filename.py": [1, 2, 3, 5, 6, 7, 10]
  }
}
```

`executable` is only present when `collect_executable=True` was used.

## Data Directory Structure

```
.mpy_coverage/
  20260213_143022_test_foo.json
  20260213_143025_test_bar.json
  20260213_143030_test_baz.json
```

Files are named `<YYYYMMDD_HHMMSS>_<script_basename>.json`.

## Limitations

- **settrace overhead:** tracing adds significant runtime cost; not suitable for timing-sensitive code
- **co_lines incompleteness:** pathway A only reports executable lines for functions that were entered; uncalled functions are invisible rather than showing 0%
- **bytecode only:** native/viper functions are not traced by settrace
- **memory on constrained devices:** large `_executed` dicts may hit memory limits on small targets
- **coverage.py private API:** the report integration overrides `Coverage._get_file_reporter()` which may change across coverage.py versions
