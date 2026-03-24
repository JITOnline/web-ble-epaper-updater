# Web BLE E-Paper Updater

A Django-based web application for dynamically managing and updating Gicisky BLE e-paper displays. Built to integrate seamlessly on bare-metal or Docker-based infrastructure.

## Architecture & Requirements

- **Backend:** Python 3.11+, Django.
- **Hardware Integration:** Relies on `bleak` for BLE communications.
- **Host Dependencies:** The host machine (or LXC on Proxmox) **must** have Bluetooth enabled with the `dbus` daemon actively running.
- **Database:** SQLite (default configuration out-of-the-box).

## Local Development Setup

To run the application locally on your host:

1. **Establish the Workspace:**
   ```bash
   # Enter the directory and create the Python virtual environment
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Initialize the SQLite Database:**
   SQLite is configured by default. The database is generated at the root of the project as `db.sqlite3`. Prepare the schema by running Django migrations:
   ```bash
   python manage.py migrate
   ```

4. **Create a Superuser (Optional for Admin access):**
   ```bash
   python manage.py createsuperuser
   ```

5. **Run the Server:**
   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```
   Navigate to HTTP port 8000.

---

## Production Deployment (Docker + SQLite)

As an Ansible-first/Docker environment, deploying this app requires special container configuration to pass through proper Bluetooth hardware access to the `bleak` library.

### Docker Compose Configuration

Create a `docker-compose.yml` leveraging the following structure:

```yaml
services:
  web_epaper:
    build: .
    # Host networking is highly recommended for Bleak to detect host BLE adapters
    network_mode: "host"
    volumes:
      # Map the application code (optional if using a builder pattern)
      - .:/app
      # CRITICAL: Expose the host's dbus socket for BlueZ BLE communications
      - /run/dbus:/run/dbus:ro
      # Persist the local SQLite DB data
      - ./data:/app/data
    environment:
      - DJANGO_SETTINGS_MODULE=config.settings
    # Start gunicorn and apply migrations
    command: >
      bash -c "python manage.py migrate 
      && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3"
    restart: unless-stopped
```

### Automation Strategy (Semaphore / Ansible)

If you are orchestrating deployments via **Semaphore UI** and Ansible playbooks:
1. **Directory Integrity:** Ensure your playbooks `file` modules correctly scaffold the persistent `./data` directory where the SQLite database will live. Failure to do so might result in Docker creating the directory as `root`, causing permission issues for Django.
2. **Idempotent Migrations:** The Docker Compose `command` automatically applies pending migrations incrementally during runtime without manual intervention (`python manage.py migrate`). This adheres to your declarative deployment footprint.
3. **Database Settings Update:** Be sure to adjust your Django `settings.py` so the `sqlite3` path points to a persistent mounted volume block (e.g. `./data/db.sqlite3`) and not the ephemeral container workspace.

### LXC Considerations (Proxmox VE)

If you deploy this directly inside a Proxmox LXC container (without Docker), you must pass through the Bluetooth adapter and DBus sockets in the container `.conf` file:

```ini
# Add to /etc/pve/lxc/<CTID>.conf
lxc.mount.entry: /run/dbus run/dbus none bind,ro,create=dir 0 0
# You might also be required to grant cgroup permissions for rfkill/bluetooth
```

---

## Raspberry Pi Zero W (DietPi) Deployment

Due to the limited resources of a Raspberry Pi Zero W (512MB RAM, single-core ARMv6), deploying on the **DietPi** operating system is an excellent choice for a lightweight footprint. Because Docker introduces notable CPU/Memory overhead on this specific hardware, running it natively via a `systemd` service is highly recommended, though Docker remains a viable alternative.

### 1. DietPi Preparation

Before deploying, ensure Bluetooth is enabled and the necessary system packages are installed:

1. Open `dietpi-config`.
2. Navigate to **Advanced Options > Bluetooth** and ensure it is turned **On**.
3. Install required build dependencies via the terminal:
   ```bash
   apt update
   apt install -y python3-venv python3-pip python3-dev libglib2.0-dev libdbus-1-dev
   ```

### 2. Bare-Metal Deployment (Recommended)

To minimize overhead, run the application natively via a `systemd` service.

1. **Clone and Setup:**
   ```bash
   cd /opt
   git clone <repo-url>
   cd web-ble-epaper-updater
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   ```

2. **Create a Systemd Service:**
   Create `/etc/systemd/system/epaper-updater.service`:
   ```ini
   [Unit]
   Description=Web BLE E-Paper Updater
   After=network.target bluetooth.target

   [Service]
   User=dietpi
   Group=dietpi
   WorkingDirectory=/opt/web-ble-epaper-updater
   Environment="DJANGO_SETTINGS_MODULE=config.settings"
   ExecStart=/opt/web-ble-epaper-updater/venv/bin/gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and Start:**
   ```bash
   systemctl daemon-reload
   systemctl enable --now epaper-updater
   ```

### 3. Docker Deployment (Alternative)

If you prefer to maintain uniformity with your DevOps/Ansible containerization strategy despite the hardware constraints, DietPi offers highly optimized Docker installations.

1. Install Docker and Docker Compose via DietPi's software tool:
   ```bash
   dietpi-software install 162 134
   ```
2. Utilize the `docker-compose.yml` configuration documented in the main section above. Make sure you utilize the `--compatibility` flag or rely on `restart: unless-stopped` to ensure the container survives reboots.
