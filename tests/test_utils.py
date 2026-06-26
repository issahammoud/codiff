from codiff.utils.files import hash_file, is_path_in_venv, is_venv_dir
from codiff.utils.gitignore_utils import is_dir_ignored, is_file_ignored, load_gitignore


class TestHashFile:
    def test_returns_64_char_hex(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_bytes(b"hello")
        result = hash_file(str(f))
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_content_same_hash(self, tmp_path):
        a, b = tmp_path / "a.txt", tmp_path / "b.txt"
        a.write_bytes(b"same")
        b.write_bytes(b"same")
        assert hash_file(str(a)) == hash_file(str(b))

    def test_different_content_different_hash(self, tmp_path):
        a, b = tmp_path / "a.txt", tmp_path / "b.txt"
        a.write_bytes(b"one")
        b.write_bytes(b"two")
        assert hash_file(str(a)) != hash_file(str(b))


class TestIsVenvDir:
    def test_egg_info_suffix(self, tmp_path):
        assert is_venv_dir(str(tmp_path), "mypackage.egg-info")

    def test_pyvenv_cfg_marks_venv(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin")
        assert is_venv_dir(str(tmp_path), "venv")

    def test_regular_dir_is_not_venv(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert not is_venv_dir(str(tmp_path), "src")


class TestIsPathInVenv:
    def test_path_inside_venv(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin")
        lib = venv / "lib" / "mod.py"
        lib.parent.mkdir(parents=True)
        lib.touch()
        assert is_path_in_venv(str(lib), str(tmp_path))

    def test_path_outside_venv(self, tmp_path):
        src = tmp_path / "src" / "app.py"
        src.parent.mkdir()
        src.touch()
        assert not is_path_in_venv(str(src), str(tmp_path))

    def test_top_level_file(self, tmp_path):
        f = tmp_path / "app.py"
        f.touch()
        assert not is_path_in_venv(str(f), str(tmp_path))


class TestLoadGitignore:
    def test_returns_none_without_gitignore(self, tmp_path):
        assert load_gitignore(str(tmp_path)) is None

    def test_loads_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        assert load_gitignore(str(tmp_path)) is not None

    def test_matches_ignored_file(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        spec = load_gitignore(str(tmp_path))
        assert is_file_ignored(spec, str(tmp_path), str(tmp_path / "mod.pyc"))

    def test_does_not_match_non_ignored_file(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        spec = load_gitignore(str(tmp_path))
        assert not is_file_ignored(spec, str(tmp_path), str(tmp_path / "mod.py"))


class TestIsDirIgnored:
    def test_ignored_dir(self, tmp_path):
        (tmp_path / ".gitignore").write_text("build/\n")
        spec = load_gitignore(str(tmp_path))
        assert is_dir_ignored(spec, str(tmp_path), str(tmp_path), "build")

    def test_non_ignored_dir(self, tmp_path):
        (tmp_path / ".gitignore").write_text("build/\n")
        spec = load_gitignore(str(tmp_path))
        assert not is_dir_ignored(spec, str(tmp_path), str(tmp_path), "src")

    def test_none_spec_never_ignores(self, tmp_path):
        assert not is_dir_ignored(None, str(tmp_path), str(tmp_path), "anything")


class TestIsFileIgnored:
    def test_none_spec_never_ignores(self, tmp_path):
        assert not is_file_ignored(None, str(tmp_path), str(tmp_path / "foo.py"))
