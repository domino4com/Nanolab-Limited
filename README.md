<a href="https://www.youtube.com/watch?v=2w5mD4_LKXo">
  <img
    align="right"
    width="300"
    src="./YouTube_Logo.svg"
    alt="Watch the video on YouTube"
  />
</a>

# Nanolab Limited

Prepare your ESP32-S3 Arduino code for Nanolab on **macOS or Windows**. You need Python 3.9 or newer and a recent Arduino IDE with your ESP32 board package and sketch libraries installed. Your code must compile for the Nanolab ESP32-S3 board. Wi-Fi and Bluetooth must not be used.

## Step 1: Update your code

Add these headers at the top of your Arduino sketch:

```cpp
#include <Arduino.h>
#include <esp_wifi.h>
#include <esp_bt.h>
```

Insert the following four lines as the **first statements in your existing `setup()`**, keeping the rest of your code below them:

```cpp
void setup() {
  esp_wifi_stop();
  esp_wifi_deinit();
  esp_bt_controller_disable();
  esp_bt_controller_deinit();

  // Keep your existing setup code here.
}
```

Keep your existing `loop()` and other code. **Save the sketch.** These headers come with the ESP32 board package; no extra Wi-Fi or Bluetooth library is needed.

## Step 2: Run the Python program

Download this repository using **Code → Download ZIP**, then extract it. Open Terminal on macOS or PowerShell on Windows and change to the extracted `Nanolab-Limited-main` folder.

Run the command for your system, replacing the quoted example with the path to your **Arduino sketch folder** (the folder containing your `.ino` file).

**macOS**

```sh
python3 pack.py "/Users/yourname/Documents/Arduino/MySketch"
```

**Windows**

```powershell
py -3 pack.py "C:\Users\yourname\Documents\Arduino\MySketch"
```

The program compiles your saved code, includes the libraries it uses, creates `sketch.yaml`, and checks that the packaged sketch compiles. It uses the Nanolab ESP32-S3 board settings and finds Arduino CLI automatically. An internet connection may be needed to download the board tools during verification. Your original sketch is preserved; no connected board is needed.

Wait for **“Both builds passed.”** The program prints the ZIP's full path. It is saved beside your sketch in a folder named `MySketch-nanolab` (with a number added on repeat runs). Supporting files are kept inside its `details` subfolder.

## Step 3: Send your ZIP

Email the generated **`MySketch.zip`** to **[nanolab@maxiq.space](mailto:nanolab@maxiq.space)**. Send the ZIP printed by the program, which includes your code, libraries, and `sketch.yaml`. If you are not able to email the file for any reason, then use [WeTransfer](https://wetransfer.com)

If something goes wrong, see [Troubleshooting](docs/TROUBLESHOOTING.md).
