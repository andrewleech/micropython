def positive_controlled_file():
    execfile(input("file: "))


def negative_constant_file():
    input()
    execfile("trusted_script.py")
