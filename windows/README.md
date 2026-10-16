# Botsy helper for Windows

The Botsy helper is a small program that runs quietly on your Windows PC. When you
export a CSV from EverBee or eRank, it sends that file to your Botsy server, and the
server takes it from there. Once a week it also brings back your **search list**: the
searches worth exporting next.

**What it does**
- Every 30 seconds it looks in your **Downloads** folder for new EverBee or eRank export files.
- It sends each new export to your server once.
- Once an hour it copies three files from the server into **Documents\Botsy**:
  - `status.txt`: what the server did with your files;
  - `daily-log.txt`: the server's daily log, written every morning (what happened in the
    last 24 hours, items waiting for your review, spending, problems). Open it when you like;
  - `search-list.txt`: this week's searches. This one opens in Notepad by itself when it
    changes (the other two never open by themselves).

**What it never does**
- It never sends any other file. A bank statement, an order history, a spreadsheet you
  saved as CSV: none of these leave your PC (see "Which files it sends" below).
- It never deletes, moves, renames or changes your files.
- It never visits etsy.com. It only talks to your own server.

---

## Before you start (about 10 minutes, once)

You need:
- Windows 10 or Windows 11.
- The address of your Botsy server: the **static IP** of your Lightsail server, for
  example `3.120.45.67`. In the Lightsail console it is on your server's page, under
  "Public static IP".
- The `windows` folder from the Botsy project, copied anywhere on your PC (your Desktop is fine).
  It holds three files: `install_helper.ps1`, `botsy_helper.py` and this README.

### Step 1. Install Python

1. Go to <https://www.python.org/downloads/> and click the yellow **Download Python** button.
2. Open the file you downloaded.
3. On the first screen, **tick "Add python.exe to PATH"** at the bottom. This matters.
4. Click **Install Now** and wait until it says "Setup was successful". Click **Close**.

Use the python.org installer, not Python from the Microsoft Store.

### Step 2. Check that Windows' OpenSSH Client is on

Most Windows 10 and 11 PCs already have it. To check:

1. Open **Settings > System > Optional features**
   (on Windows 10: **Settings > Apps > Optional features**).
2. Look for **OpenSSH Client** in the list of installed features.
3. If it is not there: click **View features** (Windows 11) or **Add a feature** (Windows 10),
   search for **OpenSSH Client**, tick it and click **Install**. Wait until it finishes.

If you skip this step, the installer checks for you and tells you if it is missing.

### Step 3. Run the installer

1. Open the `windows` folder.
2. **Right-click `install_helper.ps1`** and choose **Run with PowerShell**.
   (On Windows 11 you may first need to click **Show more options**.)
3. If a blue window asks "Do you want to change the execution policy?", type `Y` and press Enter.

If that does not work (for example, Notepad opens instead), do this:

1. Click Start, type `PowerShell`, and open **Windows PowerShell**.
2. Type `cd ` (with a space), drag the `windows` folder into the window, and press Enter.
3. Copy this line, paste it into the window (right-click pastes), and press Enter:

   ```
   powershell -ExecutionPolicy Bypass -File .\install_helper.ps1
   ```

### Step 4. Answer the questions

1. **Server address**: type your server's static IP (like `3.120.45.67`) and press Enter.
   Only the address: no `botsy-upload@`, no `:22`.
2. The installer makes a **key** for this PC. A key is how the server knows the files
   really come from you; there is no password to remember.
3. It then shows **one line** that starts with `sudo botsy-setup --upload-pubkey`.
   That line is already copied for you, and it is also saved in `%APPDATA%\Botsy\server-command.txt`.

### Step 5. Paste that line on the server (once per PC)

1. In your web browser, open the **Lightsail console** and click your server.
2. Click **Connect using SSH**. A black terminal window opens.
3. Paste the line: press **Ctrl+Shift+V**, or use the clipboard icon at the bottom right
   of that window. Press **Enter**.
4. The server needs a few seconds. When it has finished you should see
   **Upload key added** (or **That upload key was already allowed**, if you did this before).
5. Go back to the PowerShell window and press **Enter**.

Only if the server never says either of those (for example it stopped with an error
first), paste the second line the installer showed instead. It starts with
`echo "restrict ssh-ed25519` and adds the key directly. If the first line answered
"botsy-setup: command not found", or the second one answers "No such file or directory",
the server itself is not set up yet: do that first (`deploy/lightsail/README.md`), then
paste the first line again.

### Step 6. Test the connection

The installer asks "Test the connection to the server now?". Press **Enter** (yes).

- **"OK: connected to botsy-upload@... The Botsy helper is ready."** You are done.
- Anything else: see "If something is wrong" below, fix it, then test again (see "Handy commands").

The installer then starts the helper. From now on it starts by itself every time you
log in to Windows. You will not see a window; that is normal.

---

## Everyday use

1. In EverBee (Product Analytics) or eRank, click **Export CSV** as usual.
2. The file lands in your Downloads folder.
3. Within about a minute the helper sends it to the server. You do not need to do anything.

You can leave the file in Downloads, or move it somewhere else afterwards. The helper
remembers what it sent: if you download the same file again, it is not sent a second time.

Each week, when the new **search list** opens in Notepad, search each line in EverBee and
click Export CSV. That is all.

You can tick off lines or write notes in `search-list.txt` and save it. The helper leaves
your copy alone until the server has a new list, and only then replaces it (and opens it).

## How to see that it is working

- **What the helper did**: the log file. Press **Windows key + R**, paste
  `%APPDATA%\Botsy` and press Enter, then open `helper.log`. Lines like
  `Uploaded EverBee Export.csv as ...` mean a file was sent. Lines like
  `Not uploading statement.csv: it does not look like an EverBee or eRank export` mean
  a file was (correctly) left alone.
- **What the server did**: open **Documents\Botsy\status.txt**. It is updated once an
  hour (and about 15 minutes after an upload). It lists files received, any files the
  server turned down and why, and what was made from them.
- **The last day in short**: **Documents\Botsy\daily-log.txt**, new every morning. It
  also gives the exact command for each item that waits for your review.
- **This week's searches**: **Documents\Botsy\search-list.txt**.

## Which files it sends

A file is sent only when **all** of these are true:
- it is directly in your Downloads folder (not in a subfolder);
- its name ends in `.csv`;
- it was saved in the last 14 days;
- it has finished downloading (its size stopped changing);
- it is no bigger than 25 MB;
- it has not been sent before (the same content under another name counts as sent);
- its first 20 lines contain a header row with at least two column names that EverBee or
  eRank use, and one of them is a demand number such as **Mo. Sales**, **Revenue**,
  **Favorites**, **Hearts**, **Reviews**, **Listing Age**, **Avg. Searches**, **Avg. Clicks**,
  **CTR** or **Etsy Competition**.

The last rule is what keeps your other CSV files private. A bank statement (Date,
Description, Amount), an order history, or Etsy's own download of your listings (Title,
Price, Tags) does not have those demand columns, so it is never sent.

## If something is wrong

| You see | What to do |
|---|---|
| "Python was not found" | Do Step 1 again and make sure "Add python.exe to PATH" is ticked. Then run the installer again. |
| "The Windows OpenSSH Client is not turned on" | Do Step 2, then run the installer again. |
| "The server did not accept this computer's key" | Do Step 5 again (paste the line from `%APPDATA%\Botsy\server-command.txt`). |
| "Could not reach the server" | Check that the PC is online and the address is right. In the Lightsail console, check the server is running. |
| "The server's identity changed" | Only expected if the server was rebuilt. Then open PowerShell and run `ssh-keygen -R 3.120.45.67` (your address), and test again. If the server was not rebuilt, stop and ask for help. |
| "A folder on the server is missing" | The server setup is not finished. On the server, run the setup again: `sudo botsy-setup` |
| "The server closed the connection" | If it keeps happening, run the server setup again: `sudo botsy-setup` |
| "The Botsy helper cannot start" in `helper.log` | The settings file is broken (often after editing it by hand). Run the installer again; it fixes the file and keeps your key. |
| Nothing in `helper.log` at all | The helper is not running. Sign out of Windows and back in, or run the installer again. |
| "Windows did not allow a Scheduled Task" | Nothing to do: the installer used your Startup folder instead. It works the same way. |

## Handy commands

Open **Windows PowerShell** (Start, type `PowerShell`) and paste one of these:

- Test the connection:
  ```
  python "$env:LOCALAPPDATA\Botsy\botsy_helper.py" --check
  ```
- Do one check of the Downloads folder right now and show what happened:
  ```
  python "$env:LOCALAPPDATA\Botsy\botsy_helper.py" --once
  ```
  (If it says the helper is already running, that is fine: the running one is doing the work.)
- Remove the helper (your settings, key and log are kept). Run this from the `windows` folder:
  ```
  powershell -ExecutionPolicy Bypass -File .\install_helper.ps1 -Uninstall
  ```
- Pause it for a while: open **Task Scheduler**, click **Task Scheduler Library**,
  right-click **Botsy helper** and choose **End**, then **Disable**. Choose **Enable** to turn it back on.

## Settings

The settings live in `%APPDATA%\Botsy\helper.json`. You rarely need to change them; run
the installer again instead. If you do edit the file (with Notepad), sign out and back in afterwards.

| Setting | Meaning | Usual value |
|---|---|---|
| `host` | your server's address | `3.120.45.67` |
| `user` | the upload-only account on the server | `botsy-upload` |
| `key` | this PC's key | `%USERPROFILE%\.ssh\botsy_upload` |
| `watch_dir` | the folder it watches | `%USERPROFILE%\Downloads` |
| `poll_seconds` | how often it looks | `30` |
| `max_age_days` | ignore files older than this | `14` |
| `outbox_dir` | where the search list, status and daily log go | `%USERPROFILE%\Documents\Botsy` |

If your browser saves downloads somewhere else, put that folder in `watch_dir`, for
example `"C:\\Users\\you\\OneDrive\\Downloads"` (two backslashes each time). If the file
is not written exactly right, the helper does not start and says so in `helper.log`.

## Where everything is

| What | Where |
|---|---|
| The program | `%LOCALAPPDATA%\Botsy\botsy_helper.py` |
| Settings | `%APPDATA%\Botsy\helper.json` |
| Log (about 1 MB, older copies `helper.log.1` ...) | `%APPDATA%\Botsy\helper.log` |
| List of files already sent | `%APPDATA%\Botsy\state.json` |
| What the last fetch of the search list, status and daily log got | `%APPDATA%\Botsy\outbox.json` |
| The server line from Step 5 | `%APPDATA%\Botsy\server-command.txt` |
| Search list, status and daily log | `Documents\Botsy` |
| This PC's key (keep it private, never share it) | `%USERPROFILE%\.ssh\botsy_upload` |

**New PC?** Do the same steps there. It gets its own key, so paste its line on the server too.

**About the key.** The server account it opens (`botsy-upload`) can only drop files into
the server's inbox and read the search list and status. It cannot see or change anything
else on the server.
