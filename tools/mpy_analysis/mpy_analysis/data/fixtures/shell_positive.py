import subprocess


def invoke(command: str) -> None:
    subprocess.run(command, shell=True, check=True)
