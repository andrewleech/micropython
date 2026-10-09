import subprocess


def invoke(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True)
