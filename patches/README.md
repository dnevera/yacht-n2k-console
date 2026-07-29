# patches/

Runtime patches applied to third-party libraries inside the HA docker container.
Applied automatically by `deploy.sh` on every proxy deploy.

---

## nmea2000_ioclient.py

**Patches:** `nmea2000` PyPI package, file `nmea2000/ioclient.py`  
**Affects:** `TextNmea2000Gateway._receive_impl()`  
**Upstream PR:** https://github.com/dnevera/nmea2000/tree/fix/text-gateway-eof-spin-loop  
**Target:** `https://github.com/tomer-w/nmea2000`

### Bug

When the TCP gateway restarts, `readline()` returns `b""` (EOF).
Without the check, `_receive_impl()` silently returns → `_receive_loop()`
calls it again immediately → **infinite spin at 100% CPU**.

### Fix

```diff
  data = await self.reader.readline()
+ if not data:
+     raise ConnectionError("Connection closed by remote host")
```

`ConnectionError` triggers the existing reconnect logic with exponential backoff.
HA auto-reconnects to `:4001` within ~10s — no manual restart needed.

### How it's applied

`deploy.sh` (proxy section):
1. Finds the exact path inside container via `python3 -c "import nmea2000.ioclient; print(...)"`
2. `docker cp` patches the file
3. `docker restart homeassistant` to reload the module

### When to re-apply manually

If HA container is updated (image pull) and the patch is lost:
```bash
./deploy.sh user@<gateway-host> --patch-ha
```

### Remove this patch when

`nmea2000` package version > `2026.5.2` AND the fix is in the release.
Check: `sudo docker exec homeassistant pip show nmea2000`
