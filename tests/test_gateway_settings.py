"""Tests for GatewaySettings — load, save, apply, singleton."""
import json
import threading
import pytest

# Patch the settings file path before importing to avoid polluting ~/.config/ydnu02
import ydnu02_tcp_gateway.gateway_settings as _gs_module


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Redirect settings file to a temp dir and reset the singleton before each test."""
    monkeypatch.setattr(_gs_module, '_SETTINGS_DIR',  str(tmp_path))
    monkeypatch.setattr(_gs_module, '_SETTINGS_FILE', str(tmp_path / 'gateway_settings.json'))
    monkeypatch.setattr(_gs_module, '_instance', None)
    yield
    monkeypatch.setattr(_gs_module, '_instance', None)


from ydnu02_tcp_gateway.gateway_settings import GatewaySettings


class TestGatewaySettingsDefaults:
    def test_defaults_on_first_run(self):
        """Verify default settings values on initial load."""
        s = GatewaySettings.instance()
        assert s.ha_iso_replay_enabled is True
        assert s.ha_iso_replay_interval_s == 60.0

    def test_settings_file_created_on_first_run(self, tmp_path):
        """Verify settings JSON file is created on first run."""
        GatewaySettings.instance()
        assert (tmp_path / 'gateway_settings.json').exists()

    def test_file_content_matches_defaults(self, tmp_path):
        """Verify created settings file contains default key-value pairs."""
        GatewaySettings.instance()
        data = json.loads((tmp_path / 'gateway_settings.json').read_text())
        assert data['ha_iso_replay_enabled'] is True
        assert data['ha_iso_replay_interval_s'] == 60.0


class TestGatewaySettingsPersistence:
    def test_saved_values_survive_reload(self, monkeypatch, tmp_path):
        """Verify modified settings persist across instance reloads."""
        s = GatewaySettings.instance()
        s.apply_from_dict({'ha_iso_replay_enabled': False, 'ha_iso_replay_interval_s': 120.0})

        # Reset singleton and reload
        monkeypatch.setattr(_gs_module, '_instance', None)
        s2 = GatewaySettings.instance()
        assert s2.ha_iso_replay_enabled is False
        assert s2.ha_iso_replay_interval_s == 120.0

    def test_partial_update_preserves_other_keys(self):
        """Verify updating subset of settings retains unmentioned default keys."""
        s = GatewaySettings.instance()
        s.apply_from_dict({'ha_iso_replay_interval_s': 30.0})
        assert s.ha_iso_replay_enabled is True  # unchanged
        assert s.ha_iso_replay_interval_s == 30.0


class TestGatewaySettingsValidation:
    def test_interval_too_small_raises(self):
        """Verify ValueError is raised when replay interval is below minimum boundary."""
        s = GatewaySettings.instance()
        with pytest.raises(ValueError, match='5–3600'):
            s.apply_from_dict({'ha_iso_replay_interval_s': 4.9})

    def test_interval_too_large_raises(self):
        """Verify ValueError is raised when replay interval exceeds maximum boundary."""
        s = GatewaySettings.instance()
        with pytest.raises(ValueError, match='5–3600'):
            s.apply_from_dict({'ha_iso_replay_interval_s': 3601.0})

    def test_interval_boundary_5_ok(self):
        """Verify minimum valid replay interval value of 5 seconds is accepted."""
        s = GatewaySettings.instance()
        s.apply_from_dict({'ha_iso_replay_interval_s': 5.0})
        assert s.ha_iso_replay_interval_s == 5.0

    def test_interval_boundary_3600_ok(self):
        """Verify maximum valid replay interval value of 3600 seconds is accepted."""
        s = GatewaySettings.instance()
        s.apply_from_dict({'ha_iso_replay_interval_s': 3600.0})
        assert s.ha_iso_replay_interval_s == 3600.0

    def test_unknown_keys_ignored(self):
        """Verify unrecognized dictionary keys are ignored during updates."""
        s = GatewaySettings.instance()
        s.apply_from_dict({'unknown_key': 'hello', 'ha_iso_replay_enabled': False})
        assert s.ha_iso_replay_enabled is False
        assert not hasattr(s, 'unknown_key')

    def test_enabled_coerced_to_bool(self):
        """Verify truthy/falsy integer values are coerced to booleans."""
        s = GatewaySettings.instance()
        s.apply_from_dict({'ha_iso_replay_enabled': 1})
        assert s.ha_iso_replay_enabled is True
        s.apply_from_dict({'ha_iso_replay_enabled': 0})
        assert s.ha_iso_replay_enabled is False


class TestGatewaySettingsSingleton:
    def test_same_instance_returned(self):
        """Verify GatewaySettings.instance() returns the same singleton instance."""
        s1 = GatewaySettings.instance()
        s2 = GatewaySettings.instance()
        assert s1 is s2

    def test_singleton_is_thread_safe(self):
        """Two threads calling instance() should get the same object."""
        results = []
        def _get():
            results.append(GatewaySettings.instance())
        t1 = threading.Thread(target=_get)
        t2 = threading.Thread(target=_get)
        t1.start(); t2.start()
        t1.join();  t2.join()
        assert results[0] is results[1]


class TestGatewaySettingsToDict:
    def test_to_dict_returns_all_keys(self):
        """Verify to_dict returns a dictionary containing all settings keys."""
        s = GatewaySettings.instance()
        d = s.to_dict()
        assert 'ha_iso_replay_enabled' in d
        assert 'ha_iso_replay_interval_s' in d

    def test_to_dict_is_copy(self):
        """Verify mutating returned dictionary does not modify underlying settings."""
        s = GatewaySettings.instance()
        d = s.to_dict()
        d['ha_iso_replay_enabled'] = False
        # Original should be unchanged
        assert s.ha_iso_replay_enabled is True


class TestGatewaySettingsCorruptFile:
    def test_corrupt_json_falls_back_to_defaults(self, tmp_path):
        """Verify corrupt JSON file falls back to default settings."""
        (tmp_path / 'gateway_settings.json').write_text('NOT JSON {{{')
        s = GatewaySettings.instance()
        assert s.ha_iso_replay_enabled is True
        assert s.ha_iso_replay_interval_s == 60.0

    def test_partial_json_merges_known_keys(self, tmp_path, monkeypatch):
        """Verify partial JSON settings file merges known keys with defaults."""
        (tmp_path / 'gateway_settings.json').write_text(
            json.dumps({'ha_iso_replay_enabled': False})
        )
        s = GatewaySettings.instance()
        assert s.ha_iso_replay_enabled is False
        assert s.ha_iso_replay_interval_s == 60.0  # default preserved
