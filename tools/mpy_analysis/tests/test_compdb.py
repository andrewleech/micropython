from conftest import entry, write, write_db
from mpy_analysis import compdb


def port_build(tmp_path):
    """A MicroPython-shaped build: sources under top/ and top/port/, objects under top/port/build/
    at each source's path relative to the vpath root that found it."""
    top = tmp_path / "top"
    build = top / "port" / "build"
    write(top, "port/main.c")
    write(top, "py/map.c")
    write(top, "py/mpconfig.h")
    entries = [entry(top, "main.c", [], directory=top / "port", obj="build/main.o"),
               entry(top, "../py/map.c", [], directory=top / "port", obj="build/py/map.o")]
    for obj in ("main.o", "py/map.o"):
        write(build, obj)
    return top, build, entries


def check(tmp_path, entries, build, *extra):
    db = write_db(tmp_path / "compile_commands.json", entries)
    return compdb.main(["--check-db", str(db), "--build-dir", str(build), *extra])


def test_every_object_explained_passes(tmp_path):
    top, build, entries = port_build(tmp_path)
    assert check(tmp_path, entries, build) == 0


def test_unexplained_object_fails(tmp_path, capsys):
    top, build, entries = port_build(tmp_path)
    write(build, "py/stray.o")
    assert check(tmp_path, entries, build) == 1
    assert "py/stray.o" in capsys.readouterr().err


def test_assembly_object_with_its_source_on_the_build_path_is_explained(tmp_path):
    # mkrules.mk assembles without -MD, so the source is found as make found it: the object's stem
    # under a root the C entries show the build using (the port directory and the top here).
    top, build, entries = port_build(tmp_path)
    write(top, "port/resethandler.s")
    write(build, "resethandler.o")
    write(top, "lib/startup.S")
    write(build, "lib/startup.o")
    report = compdb.completeness(entries, build)
    assert report["complete"]
    assert report["assembly"] == {str(build / "resethandler.o"): str(top / "port/resethandler.s"),
                                  str(build / "lib/startup.o"): str(top / "lib/startup.S")}


def test_entry_whose_object_is_missing_fails(tmp_path):
    top, build, entries = port_build(tmp_path)
    (build / "main.o").unlink()
    assert check(tmp_path, entries, build) == 1


def test_excluded_subdirectory_belongs_to_another_configuration(tmp_path):
    top, build, entries = port_build(tmp_path)
    write(build, "mboot/other.o")
    assert check(tmp_path, entries, build) == 1
    assert check(tmp_path, entries, build, "--exclude", "mboot") == 0


def test_object_of_a_deleted_absolute_source_is_stale_not_unexplained(tmp_path):
    # A board source is compiled by absolute path; deleting it leaves its object and .P behind.
    top, build, entries = port_build(tmp_path)
    write(build, "board/gone.o")
    write(build, "board/gone.P", f"{build}/board/gone.o: {top}/board/gone.c \\\n {top}/py/mpconfig.h\n")
    report = compdb.completeness(entries, build)
    assert report["complete"]
    assert report["stale_objects_of_deleted_sources"] == {
        str(build / "board/gone.o"): f"{top}/board/gone.c"}
    assert check(tmp_path, entries, build) == 0


def test_object_of_a_deleted_relative_source_is_stale(tmp_path):
    top, build, entries = port_build(tmp_path)
    write(build, "py/gone.o")
    write(build, "py/gone.P", f"{build}/py/gone.o: ../py/gone.c ../py/mpconfig.h\n")
    report = compdb.completeness(entries, build)
    assert report["complete"]
    assert list(report["stale_objects_of_deleted_sources"]) == [str(build / "py/gone.o")]


def test_object_whose_source_still_exists_is_unexplained(tmp_path):
    # The database missed a unit the build still compiles: exactly what the check is for.
    top, build, entries = port_build(tmp_path)
    write(top, "py/extra.c")
    write(build, "py/extra.o")
    write(build, "py/extra.P", f"{build}/py/extra.o: ../py/extra.c ../py/mpconfig.h\n")
    report = compdb.completeness(entries, build)
    assert not report["complete"]
    assert report["objects_unexplained"] == [str(build / "py/extra.o")]


def test_relative_source_from_an_unknown_directory_is_not_called_deleted(tmp_path):
    # Compiled from a directory no entry uses, so the missing source cannot be placed; failing is
    # the safe answer, since a sub-make the database missed looks the same.
    top, build, entries = port_build(tmp_path)
    write(build, "sub/x.o")
    write(build, "sub/x.P", f"{build}/sub/x.o: ../../sub/x.c ../../sub/x.h\n")
    report = compdb.completeness(entries, build)
    assert report["objects_unexplained"] == [str(build / "sub/x.o")]
