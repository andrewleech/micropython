import tarfile

import pytest

from conftest import write
from mpy_analysis import install
from mpy_analysis.install import InstallError


def fake_binary(path, output):
    write(path.parent, path.name, f"#!/bin/sh\necho '{output}'\n")
    path.chmod(0o755)
    return path


def release_tarball(tmp_path, top):
    src = write(tmp_path / "src", f"{top}/CMakeLists.txt", "")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src.parent, arcname=top)
    return archive


def test_installed_cppcheck_of_another_version_is_refused(tmp_path):
    fake_binary(tmp_path / "cppcheck/bin/cppcheck", "Cppcheck 2.21.0")
    with pytest.raises(InstallError) as err:
        install.cppcheck(tmp_path, "2.22.0", None, 1)
    assert "2.21.0" in str(err.value)


def test_installed_cppcheck_of_the_pinned_version_is_kept(tmp_path):
    binary = fake_binary(tmp_path / "cppcheck/bin/cppcheck", "Cppcheck 2.22.0")
    assert install.cppcheck(tmp_path, "2.22.0", None, 1) == (binary, "Cppcheck 2.22.0")


def test_cppcheck_archive_not_matching_the_pin_is_refused_before_building(tmp_path, monkeypatch):
    archive = release_tarball(tmp_path, "cppcheck-2.22.0")
    built = []
    monkeypatch.setattr(install, "run", lambda cmd, **kw: built.append(cmd))
    (tmp_path / "prefix").mkdir()
    with pytest.raises(InstallError) as err:
        install.cppcheck(tmp_path / "prefix", "2.22.0", archive, 1)
    assert "SHA-256" in str(err.value)
    assert built == []


def test_cppcheck_archive_with_the_wrong_top_directory_is_refused(tmp_path, monkeypatch):
    archive = release_tarball(tmp_path, "cppcheck-main")
    monkeypatch.setitem(install.CPPCHECK_SHA256, "9.9.9", install.sha256_of(archive))
    monkeypatch.setattr(install, "run", lambda cmd, **kw: None)
    (tmp_path / "prefix").mkdir()
    with pytest.raises(InstallError) as err:
        install.cppcheck(tmp_path / "prefix", "9.9.9", archive, 1)
    assert "cppcheck-9.9.9" in str(err.value)


def test_cppcheck_version_without_a_pinned_digest_is_refused(tmp_path):
    with pytest.raises(InstallError) as err:
        install.cppcheck(tmp_path, "0.0.1", None, 1)
    assert "no SHA-256 is pinned" in str(err.value)


def test_interrupted_install_is_removed_before_installing_again(tmp_path, monkeypatch):
    # cmake --install stopped before writing the binary.
    write(tmp_path / "prefix", "cppcheck/share/Cppcheck/addons/misra.py")
    archive = release_tarball(tmp_path, "cppcheck-9.9.9")
    monkeypatch.setitem(install.CPPCHECK_SHA256, "9.9.9", install.sha256_of(archive))

    def cmake(cmd, **kw):
        if cmd[1] == "--install":
            fake_binary(tmp_path / "prefix/cppcheck/bin/cppcheck", "Cppcheck 9.9.9")
    monkeypatch.setattr(install, "run", cmake)
    install.cppcheck(tmp_path / "prefix", "9.9.9", archive, 1)
    assert not (tmp_path / "prefix/cppcheck/share/Cppcheck/addons/misra.py").exists()


def test_installed_arm_gcc_of_another_release_is_refused(tmp_path):
    fake_binary(tmp_path / "arm-gnu-toolchain-15.2.rel1/bin/arm-none-eabi-gcc", "14.3.1")
    with pytest.raises(InstallError) as err:
        install.arm_gcc(tmp_path, "15.2.rel1", None)
    assert "14.3.1" in str(err.value)


def test_arm_gcc_archive_not_matching_arms_digest_is_refused(tmp_path, monkeypatch):
    archive = release_tarball(tmp_path, "arm-gnu-toolchain")
    monkeypatch.setattr(install, "published_sha256", lambda url: "0" * 64)
    (tmp_path / "prefix").mkdir()
    with pytest.raises(InstallError) as err:
        install.arm_gcc(tmp_path / "prefix", "15.2.rel1", archive)
    assert "Arm publishes" in str(err.value)
    assert not (tmp_path / "prefix/arm-gnu-toolchain-15.2.rel1").exists()


def test_main_reports_a_filesystem_error_without_a_traceback(tmp_path, monkeypatch, capsys):
    def fail(*args):
        raise PermissionError("denied")
    monkeypatch.setattr(install, "cppcheck", fail)
    assert install.main(["--prefix", str(tmp_path), "--skip-arm-gcc"]) == 1
    assert "denied" in capsys.readouterr().err


def llvm_release(tmp_path, members):
    """An LLVM-9.9.9-Linux-X64.tar.xz holding MEMBERS, each a path under the top directory mapped
    to file text or ("link", target)."""
    archive = tmp_path / "LLVM-9.9.9-Linux-X64.tar.xz"
    top = tmp_path / "tree" / "LLVM-9.9.9-Linux-X64"
    for rel, content in members.items():
        path = top / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple):
            path.symlink_to(content[1])
        else:
            path.write_text(content)
            path.chmod(0o755)
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(top, arcname=top.name)
    return archive


CLANG_RELEASE = {"bin/clang": ("link", "clang-9"), "bin/clang-9": "#!/bin/sh\necho 9.9.9\n",
                 "bin/clang-tidy": "#!/bin/sh\n", "lib/libclang-cpp.so.9": "",
                 "lib/clang/9/include/stddef.h": "", "lib/clang/9/lib/rt.a": ""}


@pytest.fixture
def x64(monkeypatch):
    monkeypatch.setattr(install.platform, "machine", lambda: "x86_64")


def test_llvm_unpacks_only_clang_and_its_headers(tmp_path, monkeypatch, x64):
    archive = llvm_release(tmp_path, CLANG_RELEASE)
    monkeypatch.setitem(install.LLVM_SHA256, ("9.9.9", "X64"), install.sha256_of(archive))
    (tmp_path / "prefix").mkdir()
    binary, _ = install.llvm(tmp_path / "prefix", "9.9.9", archive)
    home = tmp_path / "prefix/llvm-9.9.9"
    assert binary == home / "bin/clang"
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*") if not p.is_dir()) == [
        "bin/clang", "bin/clang-9", "lib/clang/9/include/stddef.h"]


def test_llvm_archive_not_matching_the_pin_is_refused_and_nothing_installed(tmp_path, monkeypatch,
                                                                            x64):
    archive = llvm_release(tmp_path, CLANG_RELEASE)
    monkeypatch.setitem(install.LLVM_SHA256, ("9.9.9", "X64"), "0" * 64)
    (tmp_path / "prefix").mkdir()
    with pytest.raises(InstallError) as err:
        install.llvm(tmp_path / "prefix", "9.9.9", archive)
    assert "is pinned for LLVM 9.9.9" in str(err.value)
    assert list((tmp_path / "prefix").iterdir()) == []


def test_llvm_member_linking_outside_what_is_unpacked_is_refused(tmp_path, monkeypatch, x64):
    archive = llvm_release(tmp_path, dict(CLANG_RELEASE, **{"bin/clang": ("link", "/bin/sh")}))
    monkeypatch.setitem(install.LLVM_SHA256, ("9.9.9", "X64"), install.sha256_of(archive))
    (tmp_path / "prefix").mkdir()
    with pytest.raises(InstallError) as err:
        install.llvm(tmp_path / "prefix", "9.9.9", archive)
    assert "links to /bin/sh" in str(err.value)
    assert list((tmp_path / "prefix").iterdir()) == []


def test_llvm_release_without_a_pinned_digest_is_refused(tmp_path, x64):
    with pytest.raises(InstallError) as err:
        install.llvm(tmp_path, "0.0.1", None)
    assert "no SHA-256 is pinned for LLVM 0.0.1" in str(err.value)


def test_installed_clang_of_another_version_is_refused(tmp_path):
    fake_binary(tmp_path / "llvm-20.1.8/bin/clang", "19.1.7")
    with pytest.raises(InstallError) as err:
        install.llvm(tmp_path, "20.1.8", None)
    assert "19.1.7" in str(err.value)


def test_installed_codechecker_of_another_version_is_refused(tmp_path):
    fake_binary(tmp_path / "codechecker-6.25.1/bin/CodeChecker",
                '{"base_package_version": "6.29.1"}')
    with pytest.raises(InstallError) as err:
        install.codechecker(tmp_path, "6.25.1")
    assert "6.29.1" in str(err.value)


def test_codechecker_version_without_pinned_requirements_is_refused(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(install, "run", lambda cmd, **kw: ran.append(cmd))
    with pytest.raises(InstallError) as err:
        install.codechecker(tmp_path, "0.0.1")
    assert "no pinned requirements for CodeChecker 0.0.1" in str(err.value)
    assert ran == []
