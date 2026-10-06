# pytest configuration of tests/mcuboot.
#
# test_module.py and test_module_single.py are MicroPython tests: they need the mcuboot module of
# ports/unix/variants/mcuboot and mcuboot_single (run them with tests/run-tests.py) and exit when the
# module is missing, which aborts the collection under CPython.
collect_ignore = ["test_module.py", "test_module_single.py"]
