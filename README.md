# Web BLE E-Paper Updater

A Django-based web application for managing and updating Gicisky BLE e-paper displays. Provides a browser-based dashboard for uploading images, generating calendar views from iCal feeds, and pushing content to displays over Bluetooth Low Energy.

## Features

| Feature | Description |
|---|---|
| **Image Upload & Gallery** | Upload images (drag-and-drop or file picker) that are displayed in a gallery grid. Click any image to transfer it to the connected e-paper display. |
| **Image Deletion** | Hover over a gallery card to reveal a ✕ button that deletes the image from both the gallery and disk. |
| **iCal Free/Busy Automation** | Automatically switch the display between designated "Free" and "Busy" images based on your calendar status. Includes a persistent background worker for hands-off updates. |
| **iCal Calendar Generator** | Paste an iCal feed URL into Settings. Click **📅 Generate Calendar Image** to render an 800×480 day-view showing today's meetings. The generated image is added to the gallery for manual or automatic transfer. |
| **Device Configuration** | Configure MAC address, hardware raw type, dithering algorithm, rotation, negative colors, and advanced overrides (resolution, compression, mirror, BWR) from the sidebar. |
| **Auto-Scan** | Leave the MAC address empty to auto-scan for nearby Gicisky tags. |
| **Debug Console** | Toggle the debug console from Settings to access: raw hex command sending, Connect & Test, Disconnect, and Bluetooth adapter reset. Transfer progress and errors stream to the console in real time. |
| **Bluetooth Reset** | One-click `bluetoothctl power off/on` cycle to recover from BlueZ D-Bus errors without SSH. |
| **Collapsible Settings** | Click the Settings header to collapse the sidebar and maximize gallery space. |
| **BLE Error Handling** | Catches `BleakDeviceNotFoundError` and `BleakDBusError` with user-friendly messages instead of raw tracebacks. |

## Architecture & Requirements

- **Backend:** Python 3.11+, Django 5.x, Gunicorn.
- **BLE Stack:** `bleak` → BlueZ → D-Bus. The host **must** have a working Bluetooth adapter with `bluetoothd` and `dbus` running.
- **Image Processing:** Pillow, NumPy.
- **Calendar:** `icalendar`, `recurring-ical-events`, `requests`.
- **Database:** SQLite (zero-config, file-based).
- **Encoder:** Bundled `gicisky_tag` library handles image quantization, dithering, bitmap packing, and the Gicisky BLE write protocol.

---

## DietPi / Raspberry Pi Deployment (Recommended)

Due to the limited resources of a Raspberry Pi Zero W (512 MB RAM, single-core ARMv6), running natively on **DietPi** via a `systemd` service is the recommended approach. Docker works but adds meaningful overhead on this hardware.

### 1. System Preparation

```bash
# Enable Bluetooth in dietpi-config → Advanced Options → Bluetooth → On

# Install all system dependencies (BLE, image libs, fonts)
sudo apt update
sudo apt install -y \
    python3-venv python3-pip python3-dev \
    libglib2.0-dev libdbus-1-dev \
    libjpeg-dev zlib1g-dev libfreetype-dev liblcms2-dev libopenjp2-7 libtiff-dev \
    pi-bluetooth bluez bluez-firmware rfkill \
    fonts-dejavu-core

# Unblock the adapter and grant permissions
sudo rfkill unblock bluetooth
sudo usermod -aG bluetooth dietpi
sudo systemctl reload dbus
sudo systemctl restart bluetooth
```

> **Important:** The `fonts-dejavu-core` package provides the TTF fonts used by the calendar image generator. Without it, the calendar will fall back to Pillow's tiny default bitmap font.

> **Pillow rebuild:** If you installed Pillow *before* the image library packages (`libjpeg-dev`, etc.), it may have built without JPEG/PNG support. Force-reinstall it:
> ```bash
> /srv/web-ble-epaper-updater/venv/bin/pip install --force-reinstall --no-cache-dir Pillow
> ```

### 2. Application Setup

```bash
cd /srv
git clone <repo-url> web-ble-epaper-updater
cd web-ble-epaper-updater

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py collectstatic --noinput
```

### 3. Systemd Service

Create `/etc/systemd/system/epaper-updater.service`:

```ini
[Unit]
Description=Web BLE E-Paper Updater
After=network.target bluetooth.target

[Service]
User=dietpi
Group=dietpi
WorkingDirectory=/srv/web-ble-epaper-updater
Environment="DJANGO_SETTINGS_MODULE=config.settings"
ExecStart=/srv/web-ble-epaper-updater/venv/bin/gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now epaper-updater
```

Navigate to `http://<pi-ip>:8000`.

---

## Local Development Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

---

## Docker Deployment (Alternative)

### Docker Compose

```yaml
services:
  web_epaper:
    build: .
    network_mode: "host"
    volumes:
      - .:/app
      - /run/dbus:/run/dbus:ro
      - ./data:/app/data
    environment:
      - DJANGO_SETTINGS_MODULE=config.settings
    command: >
      bash -c "python manage.py migrate
      && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3"
    restart: unless-stopped
```

> `network_mode: "host"` is required for Bleak to access the host's BlueZ D-Bus interface.

### DietPi Docker Install

```bash
dietpi-software install 162 134   # Docker + Docker Compose
```

### LXC Considerations (Proxmox VE)

If deploying inside a Proxmox LXC container, pass through D-Bus in the container config:

```ini
# /etc/pve/lxc/<CTID>.conf
lxc.mount.entry: /run/dbus run/dbus none bind,ro,create=dir 0 0
```

You may also need cgroup permissions for `rfkill`/bluetooth.

---

## Usage Guide

### Uploading Images

1. In the sidebar, use the **Choose Image** drop area or drag-and-drop a file.
2. Click **Upload Image or Text** — the image appears in the gallery with a sequence number (e.g., `#1`, `#2`).
3. Click any gallery card to transfer it to the e-paper display.

### Deleting Images

Hover over a gallery card and click the red **✕** button in the top-right corner. A confirmation dialog prevents accidental deletion.

### Generating a Calendar Image

1. In Settings, paste your iCal feed URL into the **iCal Feed URL** field and click **Save Config**.
2. Click the green **📅 Generate Calendar Image** button.
3. The app fetches the feed, renders an 800×480 day-view for today, and adds it to the gallery.
4. Click the generated calendar card to push it to your display.

The calendar renders:
- **Red blocks** for meetings (with title and time labels)
- **Red arrow** for the current time
- **All-day events** in the header ribbon
### iCal Free/Busy Automation

1. **Designate Images**: Upload two images to the gallery—one for when you are "Free" and one for when you are "Busy" (in a timed meeting). Note their card numbers (e.g., `#3`).
2. **Configure**: In the **iCal Automation** section of the sidebar:
   - Provide your **iCal Feed URL**.
   - Select the respective images from the **"FREE" Image** and **"BUSY" Image** dropdowns (labeled with numbers).
   - Check **ACTIVE AUTOMATION** and click **Save Settings**.
3. **Automated Scheduling**: The system automatically manages a **cron job** for the current user. 
   - Enabling automation adds a `*/5 * * * *` check to your crontab.
   - Disabling automation removes the entry.
   - You can also run a manual one-shot check: `python3 manage.py check_automation`.

**Status Reporting**:
- A pulsing green **RUNNING** badge will appear in the sidebar next to the **iCal Automation** title when the feature is enabled.
- The **Debug Console** logs the current status (`[FREE]` or `[BUSY]`) and the calculated time for the next display update whenever the page loads or settings change.

### Debug Console

1. Check **Show Debug Console** in the sidebar to reveal the console below the gallery.
2. Available actions:
   - **Send** — send a raw hex command (e.g. `01`) to the tag's GATT characteristic.
   - **Connect & Test** — open a persistent BLE connection and verify with CMD 01.
   - **Disconnect** — tear down the diagnostic connection.
   - **🔄 Reset Bluetooth** — power-cycle the host Bluetooth adapter (`bluetoothctl power off/on`).
3. Check **Detailed Output** for verbose `gicisky_tag` library logging during transfers.

### Device Configuration

| Setting | Purpose |
|---|---|
| **MAC Address** | Target device. Leave empty to auto-scan. |
| **Hardware Raw Type** | Hex value (e.g. `410B`). Leave empty for defaults or auto-detect. |
| **Rotate 180°** | Flip the image before encoding. |
| **Negative** | Invert all colors. |
| **Dithering** | `None`, `Floyd-Steinberg`, or `Combined` (independent grayscale + red quantization). |
| **Width/Height Override** | Force a custom resolution instead of auto-detected. |
| **Force Compress** | Enable/disable RLE compression in the data stream. |
| **Force BWR** | Force black/white/red encoding even if the tag reports BW-only. |
| **Force Mirror** | Mirror the image horizontally (required by some display types). |
| **iCal Feed URL** | URL for calendar image generation and automation. |
| **Automation Active** | Toggle the background iCal free/busy check. |
| **Free/Busy Images** | Select gallery images for each calendar state. |

---

## Ansible / Semaphore Integration

Structure your playbooks to:

1. **Scaffold directories** — ensure `/srv/web-ble-epaper-updater/data/` exists with correct ownership *before* starting the container or service, preventing root-owned SQLite files.
2. **Idempotent migrations** — the systemd service and Docker Compose command both run `migrate` on startup.
3. **Persistent DB path** — if using Docker, point Django's `DATABASES` setting to `./data/db.sqlite3` so the database survives container rebuilds.

---

## Acknowledgments

This project would not have been possible without the reverse-engineering and reference implementations from:

- **[ATC1441 – ATC_GICISKY_Paper_Image_Upload](https://github.com/atc1441/atc1441.github.io/blob/main/ATC_GICISKY_Paper_Image_Upload.html)** — The original Web Bluetooth uploader by Aaron Christophel (ATC1441) that documented the Gicisky BLE GATT protocol, command handshake, compression format, and display type detection. The encoding logic in `gicisky_tag/encoder.py` and the BLE write sequence in `gicisky_tag/writer.py` are derived from this work.

- **[fpoli/gicisky-tag](https://github.com/fpoli/gicisky-tag/tree/master)** — A Python CLI tool by Federico Poli for interacting with Gicisky e-paper tags via `bleak`. Provided the foundation for the BLE scanner, image dithering pipeline, and the Python-native approach to bitmap packing and transfer used in this project.

---

## License

See [LICENSE](LICENSE).
