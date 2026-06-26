from codiff.config import Settings, settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.REPO_CLONE_DIR == "/tmp/repos"
        assert s.MAX_REPO_SIZE_MB == 500

    def test_module_singleton_is_settings_instance(self):
        assert isinstance(settings, Settings)
