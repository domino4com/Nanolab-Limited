# Troubleshooting

## Arduino CLI not found or too old

Install or update [Arduino IDE](https://www.arduino.cc/en/software/) and open it once. The program requires Arduino CLI 1.3.0 or newer and searches your PATH, the folder containing `pack.py`, standard Arduino IDE locations on macOS and Windows, and `/Applications/arduino-cli` on macOS. For an IDE installed in a custom location, run `pack.py` without arguments to see how to select its CLI. A manually downloaded CLI must match your computer's operating system and processor.

## Bad CPU type in executable

On an Apple Silicon Mac, this means an Intel-only tool is being launched. Check the executable named in the error; updating Arduino CLI alone does not replace its separate build tools. Arduino's published `ctags` 5.8-arduino11 macOS executable is Intel-only. To avoid Rosetta, build Arduino's own fork for ARM64, as shown below. Your Mac needs Apple's Command Line Tools (`xcode-select --install` if they are missing) and Python 3. Do not substitute a generic `ctags` package; Arduino uses a modified version at a specific path. These commands download [Arduino's official source release](https://github.com/arduino/ctags/releases/tag/5.8-arduino11), fix a macro name that conflicts with modern Apple headers, build it, and back up the existing executable before replacing it. Close Arduino IDE first. Paste the whole block into Terminal.

```sh
(
set -eu
export DEVELOPER_DIR=/Library/Developer/CommandLineTools
CTAGS_BUILD=$(mktemp -d)
trap 'rm -rf "$CTAGS_BUILD"' EXIT
cd "$CTAGS_BUILD"
curl -fL https://github.com/arduino/ctags/releases/download/5.8-arduino11/ctags-5.8-arduino11.tar.xz -o source.tar.xz
tar -xf source.tar.xz
cd ctags-5.8-arduino11
python3 - <<'PY'
from pathlib import Path
for path in Path('.').iterdir():
    if path.suffix in ('.c', '.h'):
        data = path.read_bytes()
        if b'__unused__' in data:
            path.write_bytes(data.replace(b'__unused__', b'ARDUINO_CTAGS_UNUSED'))
PY
CC=clang CFLAGS='-O2 -arch arm64 -std=gnu89 -Wno-implicit-function-declaration -Wno-int-conversion' ./configure
make -j4
file ./ctags
CTAGS_DIR="$HOME/Library/Arduino15/packages/builtin/tools/ctags/5.8-arduino11"
test -f "$CTAGS_DIR/ctags"
cp -pn "$CTAGS_DIR/ctags" "$CTAGS_DIR/ctags.intel-backup"
install -m 755 ./ctags "$CTAGS_DIR/ctags"
file "$CTAGS_DIR/ctags"
)
```

The final output must contain `arm64`. Run `pack.py` again. If you configured a different Arduino data directory, use the `ctags` directory printed in the error instead. Reinstalling Arduino's tools may overwrite this replacement. If the error names another executable, that tool also needs a native ARM version; changing `ctags` alone will not fix it.

## Board package not installed

In Arduino IDE's Settings/Preferences, add `https://espressif.github.io/arduino-esp32/package_esp32_index.json` to Additional Boards Manager URLs. Open Boards Manager and install **esp32 by Espressif Systems**, version **3.3.11** for the Nanolab setup. Select **ESP32S3 Dev Module** and compile your sketch in the IDE. The packager records the actual installed core version used for its build. Its default board settings are ESP32-S3, hardware CDC, CDC on boot, 8 MB flash, no PSRAM, DIO flash mode, 240 MHz CPU, and the default partition scheme. If a board option is rejected, check that you installed the intended ESP32 core version.

## Compilation failed

Read the first compiler error and fix it in Arduino IDE, then save your sketch before trying again. The packager creates a ZIP only after successful compilation and, by default, successful isolated verification. Full CLI output is retained under the output folder's `details/logs` directory. The program copies your saved sketch to a temporary folder; references to files outside that sketch may need to be moved inside the project to make it portable. Existing `sketch.yaml` settings are ignored during discovery; Nanolab's default board settings and your installed libraries are used instead. Run the program without arguments if you need the advanced controls.

## Download or network error

The isolated verification build may download the pinned ESP32 core and tools even if they are already installed for Arduino IDE. Allow time, disk space, and internet access for this first download. Check your internet connection, proxy, or firewall if a download fails, then rerun the program. No ZIP is published from a failed build.

## ESP32 headers or functions not found

The required calls are ESP32-specific. They are intended here for the ESP32-S3 and cannot be pasted unchanged into sketches for AVR boards such as the classic Uno or Nano, or ESP32 variants without the required radios. Install the correct board package and place `#include <esp_wifi.h>` and `#include <esp_bt.h>` above `setup()`. No Wi-Fi or Bluetooth library needs to be installed separately.

## File or folder not found

Pass the sketch folder, not the `.ino` file, and put the full path in quotes, particularly when it contains spaces. `MySketch` must contain a main file named `MySketch.ino`. Save the sketch in Arduino IDE first. If Python cannot find `pack.py`, change to the extracted repository folder or give the full path to `pack.py`. On Windows, the sketch may be in OneDrive rather than the example Documents location.

## Library not found

Install the missing library using Arduino IDE's Library Manager, or restore your custom library to the IDE's sketchbook `libraries` folder. Compile and save the sketch again. The packager automatically reads the IDE's `.arduinoIDE/arduino-cli.yaml` configuration when present, including a custom or OneDrive sketchbook path. If you use a separate CLI configuration or libraries elsewhere, run `pack.py` without arguments to see the optional controls. Both directly used and indirectly used libraries are bundled; libraries already supplied by the board core are supplied through the pinned core instead.

## Output folder already exists

Normal runs automatically select a new numbered output folder and preserve previous packages. If you deliberately selected an existing output folder using an advanced option, choose a new folder. Run the packager against your original saved sketch, not the copy under `details`. Send the ZIP from the latest successful run; older ZIPs contain older code.

## Permission denied or path too long

Extract the repository into a writable folder and keep the sketch outside protected system locations. Close software that is locking the output, then try again. On Windows, use a short local sketch path such as `C:\Arduino\MySketch` if a compiler reports excessive path length. The packager already uses a short temporary directory for builds, but a long sketch name or deeply nested library can still exceed a tool's limit.

## Python3 not found

Install Python 3.9 or newer from [python.org](https://www.python.org/downloads/) and reopen Terminal or PowerShell. On macOS, use `python3`. On Windows, use `py -3`; if that launcher is unavailable but Python is installed, use `python` and check that it reports Python 3.9 or newer. A Windows Store prompt or “not recognized” message usually means Python is not installed or its executable is not available on PATH. No additional Python packages are required.

## Radio calls report not initialized

When your code has never initialized Wi-Fi or Bluetooth, the four required calls can return “not initialized” or “invalid state”. The example intentionally ignores those return values. Keep the four calls as the first statements in `setup()` and keep Wi-Fi and Bluetooth unused throughout your program. Do not wrap them in an error-checking macro that aborts execution on these expected return codes.

## Windows blocks Arduino CLI

Use Arduino IDE's bundled CLI or download the appropriate Windows CLI from [Arduino's installation page](https://docs.arduino.cc/arduino-cli/installation). Extract the archive before running the program. If Windows blocks a manually downloaded executable, review its source and the Windows file properties/security prompt. The macOS executable cannot run on Windows. Standard per-user and all-users IDE installations are detected automatically; custom installations can be selected through the options shown when `pack.py` runs without arguments.

## ZIP is too large to email

Use [WeTransfer](https://wetransfer.com) and send a link to nanolab@maxiq.space.
