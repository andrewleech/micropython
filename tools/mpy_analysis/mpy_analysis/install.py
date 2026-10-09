#!/usr/bin/env python3
"""Install the pinned analysers into a prefix: cppcheck built from its release tag, the Arm GNU
Toolchain used only for GCC -fanalyzer, and CodeChecker with the clang it drives for the Clang
Static Analyzer.

All of them run in the stock MicroPython build container, which has cmake, g++, python3, its
development headers and xz but no curl, wget or ensurepip, so downloads use urllib. None is
patched.

  cppcheck      https://github.com/cppcheck-opensource/cppcheck/archive/refs/tags/<ver>.tar.gz,
                checked against the SHA-256 pinned here for that version (cppcheck publishes no
                source archive or checksum of its own, and a tag can be moved), then built with
                cmake into PREFIX/cppcheck (bin/cppcheck, FILESDIR PREFIX/cppcheck/share/Cppcheck),
                with the match compiler on because it is the difference between an analysis that
                fits in a CI run and one that does not. HAVE_RULES stays off: nothing here uses
                --rule-file, and the container has no PCRE.
  Arm GCC       arm-gnu-toolchain-<ver>-<host>-arm-none-eabi.tar.xz from developer.arm.com, checked
                against the .sha256asc Arm publishes beside it, unpacked to
                PREFIX/arm-gnu-toolchain-<ver>.
  CodeChecker   From PyPI into its own virtual environment, PREFIX/codechecker-<ver>, with every
                package, CodeChecker's own source archive included, pinned by version and SHA-256
                in data/codechecker-<ver>.txt, which pip enforces (--require-hashes). The archives
                PyPI has only as source are built without build isolation, against the setuptools
                pinned the same way in data/codechecker-<ver>-build.txt, so nothing unpinned is
                fetched to build them.
  clang         LLVM-<ver>-Linux-<arch>.tar.xz from the LLVM project's GitHub release, checked
                against the SHA-256 pinned here, which is the digest GitHub reports for the release
                asset. The archive is about 2 GB and the Clang Static Analyzer needs only the clang
                driver and its resource headers, so the archive is streamed: those members are
                unpacked to a scratch directory while the whole archive is hashed, and the directory
                becomes PREFIX/llvm-<ver> only once the digest matches.

A component whose binary already exists is not rebuilt; its version is reported and must be the one
asked for, so a prefix restored from a cache for another version fails instead of being used. A
component directory without its binary is what an interrupted install leaves, and is removed and
installed again.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

CPPCHECK_URL = "https://github.com/cppcheck-opensource/cppcheck/archive/refs/tags/{version}.tar.gz"
# SHA-256 of CPPCHECK_URL per release, measured when the release was adopted. A version without an
# entry is refused rather than built unchecked.
CPPCHECK_SHA256 = {
    "2.22.0": "d74945deb2d50393430e07596b766f8a779512c7f60dac2a30ea64e059ece57b",
}
ARM_GCC_URL = (
    "https://developer.arm.com/-/media/Files/downloads/gnu/{version}/binrel/"
    "arm-gnu-toolchain-{version}-{host}-arm-none-eabi.tar.xz"
)
ARM_GCC_HOSTS = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
LLVM_URL = (
    "https://github.com/llvm/llvm-project/releases/download/llvmorg-{version}/"
    "LLVM-{version}-Linux-{arch}.tar.xz"
)
LLVM_ARCHES = {"x86_64": "X64", "amd64": "X64"}
# SHA-256 of LLVM_URL per release and host architecture: the digest GitHub reports for the release
# asset, and that of the archive downloaded when the release was adopted. A release or host without
# an entry is refused.
LLVM_SHA256 = {
    ("20.1.8", "X64"): "1ead36b3dfcb774b57be530df42bec70ab2d239fbce9889447c7a29a4ddc1ae6",
}
# The pinned requirements of each CodeChecker release: every package and its SHA-256, for pip's
# hash-checking mode. A version without a file is refused.
DATA = Path(__file__).resolve().parent / "data"


class InstallError(Exception):
    pass


def download(url, dest):
    print(f"downloading {url}", flush=True)
    partial = dest.with_name(dest.name + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise InstallError(f"download of {url} failed: {exc}")
    partial.replace(dest)
    return dest


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def clear_partial(home, binary):
    """Remove what an interrupted install left: the component's directory without its binary."""
    if home.exists() and not binary.exists():
        print(
            f"{home} has no {binary.name}, so an earlier install did not finish; removing it",
            flush=True,
        )
        shutil.rmtree(home)


def extract(archive, into):
    """Unpack, refusing members that would land outside the target directory."""
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(into, filter="data")
        else:
            base = os.path.realpath(into)
            for member in tar.getmembers():
                target = os.path.realpath(os.path.join(base, member.name))
                if target != base and not target.startswith(base + os.sep):
                    raise InstallError(f"{archive} member {member.name} escapes the target")
            tar.extractall(into)
    tops = [p for p in into.iterdir()]
    if len(tops) != 1 or not tops[0].is_dir():
        raise InstallError(f"{archive} does not unpack to a single directory")
    return tops[0]


def run(cmd, **kwargs):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    if subprocess.run(cmd, **kwargs).returncode != 0:
        raise InstallError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed")


def first_line(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise InstallError(f"{' '.join(cmd)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip().splitlines()[0]


def cppcheck(prefix, version, archive, jobs):
    home = prefix / "cppcheck"
    binary = home / "bin" / "cppcheck"
    if binary.exists():
        found = first_line([str(binary), "--version"])
        if found != f"Cppcheck {version}":
            raise InstallError(
                f"{binary} is {found!r}, not Cppcheck {version}. Remove {home} to "
                f"install the requested version."
            )
        print(f"cppcheck already installed: {found}")
        return binary, found
    pinned = CPPCHECK_SHA256.get(version)
    if pinned is None:
        raise InstallError(
            f"no SHA-256 is pinned for cppcheck {version}, so its source cannot be "
            f"checked. Add the release to CPPCHECK_SHA256 in {__file__} once its "
            f"archive has been measured."
        )
    clear_partial(home, binary)
    work = Path(tempfile.mkdtemp(prefix="cppcheck-", dir=prefix))
    try:
        url = CPPCHECK_URL.format(version=version)
        if archive is None:
            archive = download(url, work / f"{version}.tar.gz")
        found_digest = sha256_of(archive)
        if found_digest != pinned:
            raise InstallError(
                f"{archive} has SHA-256 {found_digest}, but {pinned} is pinned for "
                f"cppcheck {version} ({url})"
            )
        print(f"SHA-256 matches the pin for cppcheck {version}")
        source = extract(Path(archive), work / "src")
        if source.name != f"cppcheck-{version}":
            raise InstallError(f"{archive} unpacks to {source.name}, not cppcheck-{version}")
        build = work / "build"
        run(
            [
                "cmake",
                "-S",
                str(source),
                "-B",
                str(build),
                "-DCMAKE_BUILD_TYPE=Release",
                "-DUSE_MATCHCOMPILER=ON",
                "-DHAVE_RULES=OFF",
                f"-DCMAKE_INSTALL_PREFIX={home}",
                f"-DFILESDIR={home / 'share' / 'Cppcheck'}",
            ]
        )
        run(["cmake", "--build", str(build), f"-j{jobs}"])
        run(["cmake", "--install", str(build)])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    found = first_line([str(binary), "--version"])
    if found != f"Cppcheck {version}":
        raise InstallError(f"built {binary} reports {found!r}, not Cppcheck {version}")
    return binary, found


def published_sha256(url):
    with tempfile.TemporaryDirectory() as tmp:
        text = download(url + ".sha256asc", Path(tmp) / "sha256asc").read_text()
    digest = text.split()[0] if text.split() else ""
    if len(digest) != 64:
        raise InstallError(f"{url}.sha256asc does not hold a SHA-256 digest")
    return digest.lower()


def arm_gcc(prefix, version, archive):
    home = prefix / f"arm-gnu-toolchain-{version}"
    binary = home / "bin" / "arm-none-eabi-gcc"
    # 15.2.rel1 is GCC 15.2.1: the release name carries the GCC major and minor.
    expected = version.split(".rel")[0] + "."
    if binary.exists():
        found = first_line([str(binary), "-dumpversion"])
        if not (found + ".").startswith(expected):
            raise InstallError(
                f"{binary} is GCC {found}, not the {version} release. Remove {home} "
                f"to install the requested version."
            )
        print(f"arm-none-eabi-gcc already installed: GCC {found}")
        return binary, first_line([str(binary), "--version"])
    host = ARM_GCC_HOSTS.get(platform.machine().lower())
    if host is None:
        raise InstallError(f"Arm publishes no {version} toolchain for host {platform.machine()}")
    url = ARM_GCC_URL.format(version=version, host=host)
    clear_partial(home, binary)
    work = Path(tempfile.mkdtemp(prefix="arm-gcc-", dir=prefix))
    try:
        if archive is None:
            archive = download(url, work / os.path.basename(url))
        found_digest = sha256_of(archive)
        expected_digest = published_sha256(url)
        if found_digest != expected_digest:
            raise InstallError(
                f"{archive} has SHA-256 {found_digest}, Arm publishes {expected_digest}"
            )
        print(f"SHA-256 matches {url}.sha256asc")
        top = extract(Path(archive), work / "unpacked")
        top.replace(home)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    found = first_line([str(binary), "-dumpversion"])
    if not (found + ".").startswith(expected):
        raise InstallError(f"unpacked {binary} is GCC {found}, not the {version} release")
    return binary, first_line([str(binary), "--version"])


class HashingReader:
    """A read-only stream that hashes every byte read through it."""

    def __init__(self, raw):
        self.raw = raw
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.raw.read(size)
        self.digest.update(data)
        return data

    def finish(self):
        """Read what the consumer left, which tarfile does after the end-of-archive blocks, and
        return the digest of the whole stream."""
        for _ in iter(lambda: self.read(1 << 20), b""):
            pass
        return self.digest.hexdigest()


def unpack_member(tar, member, into, wanted):
    """Unpack one member of a streamed archive, refusing a path or link that leaves INTO or a link
    to a member not unpacked with it."""
    parts = Path(member.name).parts
    if member.name.startswith("/") or ".." in parts:
        raise InstallError(f"archive member {member.name} escapes the target")
    if member.issym() or member.islnk():
        target = os.path.normpath(
            os.path.join(os.path.dirname(member.name), member.linkname)
            if member.issym()
            else member.linkname
        )
        if target not in wanted:
            raise InstallError(
                f"archive member {member.name} links to {member.linkname}, which is not unpacked"
            )
    elif not (member.isfile() or member.isdir()):
        raise InstallError(f"archive member {member.name} is not a file, directory or link")
    if hasattr(tarfile, "data_filter"):
        tar.extract(member, into, filter="data")
    else:
        tar.extract(member, into)


def llvm(prefix, version, archive):
    """clang and its resource headers from the LLVM release, which is all the Clang Static
    Analyzer runs."""
    home = prefix / f"llvm-{version}"
    binary = home / "bin" / "clang"
    if binary.exists():
        found = first_line([str(binary), "-dumpversion"])
        if found != version:
            raise InstallError(
                f"{binary} is clang {found}, not {version}. Remove {home} to "
                f"install the requested version."
            )
        print(f"clang already installed: {found}")
        return binary, first_line([str(binary), "--version"])
    arch = LLVM_ARCHES.get(platform.machine().lower())
    pinned = LLVM_SHA256.get((version, arch))
    if pinned is None:
        raise InstallError(
            f"no SHA-256 is pinned for LLVM {version} on {platform.machine()}, so "
            f"its release cannot be checked. Add it to LLVM_SHA256 in {__file__} "
            f"once the release asset has been measured."
        )
    url = LLVM_URL.format(version=version, arch=arch)
    top = f"LLVM-{version}-Linux-{arch}"
    major = version.split(".")[0]
    files = {f"{top}/bin/clang", f"{top}/bin/clang-{major}"}
    headers = f"{top}/lib/clang/{major}/include/"
    clear_partial(home, binary)
    work = Path(tempfile.mkdtemp(prefix="llvm-", dir=prefix))
    try:
        try:
            if archive is None:
                print(f"downloading {url}, unpacking clang and its headers only", flush=True)
                source = urllib.request.urlopen(url, timeout=120)
            else:
                source = open(archive, "rb")
            with source:
                stream = HashingReader(source)
                with tarfile.open(fileobj=stream, mode="r|xz") as tar:
                    for member in tar:
                        if member.name in files or member.name.startswith(headers):
                            unpack_member(tar, member, work, files)
                found_digest = stream.finish()
        except (OSError, tarfile.TarError, EOFError) as exc:
            raise InstallError(f"reading {archive or url} failed: {exc}")
        if found_digest != pinned:
            raise InstallError(
                f"{archive or url} has SHA-256 {found_digest}, but {pinned} is "
                f"pinned for LLVM {version}"
            )
        print(f"SHA-256 matches the pin for LLVM {version}")
        if not (work / top / "bin" / "clang").exists():
            raise InstallError(f"{archive or url} holds no {top}/bin/clang")
        (work / top).replace(home)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    found = first_line([str(binary), "-dumpversion"])
    if found != version:
        raise InstallError(f"unpacked {binary} is clang {found}, not {version}")
    return binary, first_line([str(binary), "--version"])


def without_pythonpath():
    """The environment for CodeChecker's interpreter: the mpy-analysis tools run with PYTHONPATH
    naming their own --target directory, whose packages must not stand in for CodeChecker's."""
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}


def codechecker_version(binary):
    proc = subprocess.run(
        [str(binary), "analyzer-version", "--output", "json"],
        capture_output=True,
        text=True,
        env=without_pythonpath(),
    )
    try:
        return json.loads(proc.stdout)["base_package_version"]
    except (ValueError, KeyError, TypeError):
        raise InstallError(f"{binary} analyzer-version failed: {proc.stderr.strip()[-300:]}")


def codechecker(prefix, version):
    home = prefix / f"codechecker-{version}"
    binary = home / "bin" / "CodeChecker"
    if binary.exists():
        found = codechecker_version(binary)
        if found != version:
            raise InstallError(
                f"{binary} is CodeChecker {found}, not {version}. Remove {home} to "
                f"install the requested version."
            )
        print(f"CodeChecker already installed: {found}")
        return binary, f"CodeChecker {found}"
    requirements = DATA / f"codechecker-{version}.txt"
    build_requirements = DATA / f"codechecker-{version}-build.txt"
    if not (requirements.exists() and build_requirements.exists()):
        raise InstallError(
            f"no pinned requirements for CodeChecker {version}: add "
            f"{requirements.name} and {build_requirements.name} to {DATA}, every "
            f"package with its SHA-256"
        )
    clear_partial(home, binary)
    env = without_pythonpath()
    pip = [
        sys.executable,
        "-m",
        "pip",
        "--python",
        str(home / "bin" / "python"),
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--require-hashes",
    ]
    try:
        run([sys.executable, "-m", "venv", "--without-pip", str(home)], env=env)
        run(pip + ["-r", str(build_requirements)], env=env)
        run(pip + ["--no-build-isolation", "-r", str(requirements)], env=env)
    except (InstallError, OSError):
        shutil.rmtree(home, ignore_errors=True)
        raise
    found = codechecker_version(binary)
    if found != version:
        raise InstallError(f"installed {binary} is CodeChecker {found}, not {version}")
    return binary, f"CodeChecker {found}"


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sast-install-tools",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--prefix", required=True, metavar="DIR")
    ap.add_argument("--cppcheck", default="2.22.0", metavar="VERSION")
    ap.add_argument("--arm-gcc", default="15.2.rel1", metavar="VERSION")
    ap.add_argument("--codechecker", default="6.25.1", metavar="VERSION")
    ap.add_argument(
        "--llvm",
        default="20.1.8",
        metavar="VERSION",
        help="the LLVM release whose clang CodeChecker drives",
    )
    ap.add_argument(
        "--skip-arm-gcc",
        action="store_true",
        help="do not install the analysis GCC (pull request runs do not use it)",
    )
    ap.add_argument(
        "--skip-codechecker",
        action="store_true",
        help="do not install CodeChecker and clang (the MISRA and licence jobs do not use them)",
    )
    ap.add_argument(
        "--cppcheck-archive",
        metavar="FILE",
        help="use this already-downloaded release tarball instead of downloading it; "
        "it is still checked against the pinned SHA-256",
    )
    ap.add_argument(
        "--arm-gcc-archive",
        metavar="FILE",
        help="use this already-downloaded toolchain tarball instead of downloading it; "
        "it is still checked against Arm's published SHA-256",
    )
    ap.add_argument(
        "--llvm-archive",
        metavar="FILE",
        help="use this already-downloaded LLVM release tarball instead of downloading "
        "it; it is still checked against the pinned SHA-256",
    )
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    args = ap.parse_args(argv)

    prefix = Path(args.prefix).resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    installed = []
    try:
        installed.append(
            ("cppcheck", *cppcheck(prefix, args.cppcheck, args.cppcheck_archive, args.jobs))
        )
        if not args.skip_arm_gcc:
            installed.append(
                ("arm-none-eabi-gcc", *arm_gcc(prefix, args.arm_gcc, args.arm_gcc_archive))
            )
        if not args.skip_codechecker:
            installed.append(("clang", *llvm(prefix, args.llvm, args.llvm_archive)))
            installed.append(("CodeChecker", *codechecker(prefix, args.codechecker)))
    except (InstallError, OSError) as exc:
        print(f"sast-install-tools: {exc}", file=sys.stderr)
        return 1
    for name, binary, version in installed:
        print(f"{name:18s} {binary}  ({version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
