def valid_file_execution():
    execfile("selected_script.py", {}, {})


def invalid_file_execution():
    execfile(123)
