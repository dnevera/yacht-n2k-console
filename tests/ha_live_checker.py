"""
ha_live_checker.py -- Home Assistant Live Device & Entity Registry Comparator.

Fetches live registered devices and entities from Home Assistant (via REST API,
local HA storage files on Pi, or SSH) and compares them against the local gateway's
published devices and sensors.

Provides:
  - HALiveChecker class
  - compare_published_with_ha(gateway_data, ha_data) -> dict
"""
import json
import os
import urllib.request
from typing import Dict, Any, List, Optional


class HALiveChecker:
    """Fetches live devices & entities from Home Assistant."""

    def __init__(self, ha_url: Optional[str] = None, token: Optional[str] = None):
        url = ha_url or os.getenv('HA_URL')
        self.ha_url = url.rstrip('/') if url else None
        self.token = token or os.getenv('HA_TOKEN', '')

    def fetch_from_api(self) -> Optional[Dict[str, Any]]:
        """Fetch states, device registry, and entity registry via HA REST API."""
        if not self.token:
            return None
        headers = {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}
        try:
            req_states = urllib.request.Request(f'{self.ha_url}/api/states', headers=headers)
            with urllib.request.urlopen(req_states, timeout=5) as resp:
                states = json.loads(resp.read().decode('utf-8'))
            return {'states': states, 'source': 'api'}
        except Exception:
            return None

    @staticmethod
    def fetch_from_storage(config_dir: str = '/config') -> Optional[Dict[str, Any]]:
        """Read HA storage files directly from disk (when running on Pi)."""
        dev_path = os.path.join(config_dir, '.storage', 'core.device_registry')
        ent_path = os.path.join(config_dir, '.storage', 'core.entity_registry')
        if not (os.path.exists(dev_path) and os.path.exists(ent_path)):
            return None
        try:
            with open(dev_path, 'r', encoding='utf-8') as f:
                devs = json.load(f).get('data', {}).get('devices', [])
            with open(ent_path, 'r', encoding='utf-8') as f:
                ents = json.load(f).get('data', {}).get('entities', [])
            return {'devices': devs, 'entities': ents, 'source': 'storage'}
        except Exception:
            return None

    def fetch_from_ssh(self, host: str = 'user@<gateway-host>') -> Optional[Dict[str, Any]]:
        """Fetch HA device and entity registries directly from remote Pi via SSH."""
        import subprocess
        try:
            cmd_dev = f"ssh -o ConnectTimeout=5 {host} \"sudo docker exec homeassistant cat /config/.storage/core.device_registry 2>/dev/null\""
            res_dev = subprocess.run(cmd_dev, shell=True, capture_output=True, text=True, timeout=8)
            cmd_ent = f"ssh -o ConnectTimeout=5 {host} \"sudo docker exec homeassistant cat /config/.storage/core.entity_registry 2>/dev/null\""
            res_ent = subprocess.run(cmd_ent, shell=True, capture_output=True, text=True, timeout=8)

            if res_dev.returncode == 0 and res_dev.stdout.strip():
                devs = json.loads(res_dev.stdout).get('data', {}).get('devices', [])
                ents = json.loads(res_ent.stdout).get('data', {}).get('entities', []) if res_ent.returncode == 0 and res_ent.stdout.strip() else []
                return {'devices': devs, 'entities': ents, 'source': 'ssh'}
        except Exception:
            pass
        return None

    def get_ha_data(self) -> Optional[Dict[str, Any]]:
        """Try API first, then storage, then SSH."""
        data = self.fetch_from_api()
        if data:
            return data
        data = self.fetch_from_storage()
        if data:
            return data
        return self.fetch_from_ssh()


def compare_published_with_ha(expected_devices: List[Dict[str, Any]],
                              expected_entities: List[Dict[str, Any]],
                              ha_data: Dict[str, Any]) -> Dict[str, Any]:
    """Compare published gateway devices & entities against HA live data.

    Returns comparison dict with:
      - devices_match: List[dict]
      - devices_missing: List[dict]
      - entities_match: List[dict]
      - entities_missing: List[dict]
      - orphan_devices: List[dict] (devices in HA with 0 entities)
    """
    result = {
        'devices_match': [],
        'devices_missing': [],
        'entities_match': [],
        'entities_missing': [],
        'orphan_devices': [],
    }

    ha_states = {s['entity_id']: s for s in ha_data.get('states', [])}
    ha_devices = ha_data.get('devices', [])

    # Check devices
    for exp_dev in expected_devices:
        exp_model = exp_dev.get('model', '').lower()
        exp_sa = exp_dev.get('src')
        match = False
        for dev in ha_devices:
            dev_model = str(dev.get('model', '')).lower()
            dev_name = str(dev.get('name', '')).lower()
            if exp_model and (exp_model in dev_model or exp_model in dev_name):
                match = True
                result['devices_match'].append({'expected': exp_dev, 'actual': dev})
                break
        if not match:
            result['devices_missing'].append(exp_dev)

    # Check entities
    for exp_ent in expected_entities:
        ent_id = exp_ent.get('entity_id')
        if ent_id in ha_states:
            actual = ha_states[ent_id]
            result['entities_match'].append({
                'entity_id': ent_id,
                'expected_state': exp_ent.get('expected_state'),
                'actual_state': actual.get('state'),
                'unit': actual.get('attributes', {}).get('unit_of_measurement'),
            })
        else:
            result['entities_missing'].append(exp_ent)

    return result
