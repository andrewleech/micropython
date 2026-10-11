# pytest configuration of tests/mboot.
#
# test_module.py and test_module_single.py are MicroPython tests: they need the mboot module of
# ports/unix/variants/mboot and mboot_single (run them with tests/run-tests.py) and exit when the
# module is missing, which aborts the collection under CPython.
collect_ignore = ["test_module.py", "test_module_single.py"]
