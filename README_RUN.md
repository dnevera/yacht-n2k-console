# YDNU-02 TCP Gateway & N2K Console -- Developer & Execution Guide

Comprehensive guide for native PyCharm project initialization, environment configuration, testing, building, and deployment.

---

## 1. Native PyCharm Project Setup (Fresh Git Clone)

When cloning the repository from git, PyCharm initializes the environment 100% natively via `requirements.txt`.

### Step 1. Copy Environment Configuration Template
Create your local `.env` file from `.env.example` (the `.env` file is git-ignored and holds your private credentials/addresses):

```bash
cp .env.example .env
```

Edit `.env` to configure your target gateway and Home Assistant addresses:
```env
# Gateway Web API Host and Port
GW_HOST=localhost
GW_PORT=8080

# Home Assistant Integration Target
HA_URL=http://localhost:8123
HA_TOKEN=your_ha_long_lived_access_token_here
```

### Step 2. Native PyCharm Interpreter & Requirements Initialization
1. Open PyCharm -> **File** -> **Open...** -> select the cloned project folder.
2. PyCharm automatically detects `requirements.txt` and presents a notification banner at the top of the editor:
   - Click **Install requirements** in the notification banner.
3. PyCharm automatically creates the local virtual environment (`.venv`) and installs all dependencies from `requirements.txt`.

*(Alternatively: Go to **PyCharm** -> **Settings...** -> **Project: yacht-n2k-console** -> **Python Interpreter** -> **Add Interpreter** -> **Add Local Interpreter...** -> **New Virtualenv Environment** and PyCharm will set up everything automatically).*

---

## 2. Running in PyCharm

Pre-configured native run configurations are located under `.idea/runConfigurations/`.

### In PyCharm Run/Debug Dropdown (top-right corner):
1. **`Run Gateway Console App`** -- Launches the main gateway web service (`app.py --port 8080`).
2. **`Run All Pytest Tests`** -- Executes the complete test suite (219 tests).
3. **`Run Live HA Integration Test`** -- Executes the live Home Assistant integration audit test.
4. **`Build Bundle Package`** -- Packages the release distribution (`./build_bundle.sh`).
5. **`Deploy to Gateway Node`** -- Deploys and restarts the gateway service on target host (`./deploy.sh`).

Select any configuration and press **`▶️`** (`Shift + F10` / `Control + R`).

---

## 3. Command Line Interface (CLI)

### Launch Gateway Web Console:
```bash
python3 app.py --port 8080
```
Web interface will be available at `http://localhost:8080`.

### Run All Tests:
```bash
python3 -m pytest tests/ -v
```

### Run Live HA Integration Test Only:
```bash
python3 -m pytest tests/test_live_ha_integration.py -v
```

---

## 4. Building and Deployment

### Build Release Package:
```bash
./build_bundle.sh
```

### Deploy to Gateway Target:
```bash
./deploy.sh
```
