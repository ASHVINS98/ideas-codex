# Run locally on Windows with Docker Desktop

Docker supplies Python, Chromium and a browser viewer. No local Python installation
or GitHub Actions secrets are required for this mode. Docker Desktop must be using
Linux containers and remain running while you use the tool.

## 1. Download the code

After authenticating Git with the GitHub account that can access the repository:

```cmd
git clone https://github.com/ASHVINS98/ideas-codex.git
cd ideas-codex
```

If you already cloned it, use `git pull`. Alternatively, open the repository in
your authenticated browser and choose **Code → Download ZIP**, then extract it and
open a terminal inside that folder. Do not clone again into an existing checkout.

## 2. Create your local configuration

In Windows Command Prompt, from the project folder:

```cmd
copy profile.example.json profile.json
copy .env.example .env
mkdir private
mkdir reports
notepad profile.json
notepad .env
```

Copy only when those files do not already exist; keep any existing configuration.
Replace the example career details in `profile.json` with your real experience,
target roles, skills and preferred locations. Put your resume inside `private/`
and set its relative path, for example `"resume_path": "private/resume.pdf"`.

In `.env`, set **`VNC_PASSWORD`** to a password you choose of exactly eight printable
ASCII characters without spaces. This password protects the local browser viewer;
it is separate from your Naukri password. VNC supports only eight password
characters. `.env`, your profile, private files and reports are Git-ignored. The
Docker build context includes only source code and synthetic test fixtures.

## 3. Build and start the local browser viewer

Start Docker Desktop, then:

```cmd
docker compose up -d --build desktop
docker compose ps
```

The first build downloads Chromium and its desktop dependencies and can take
several minutes. Open **http://localhost:6080/vnc.html** in your normal browser.
Click **Connect** and enter the viewer password you chose in `.env`. Initially the
desktop is blank; Chromium opens when you run the login command in the next step.
The viewer binds to `127.0.0.1` only. Keep this configuration local; it is not a
public hosting configuration. Do not publish port 5900 or change the bind address.

## 4. Log into Naukri in the container

From your project terminal:

```cmd
docker compose exec desktop python -m naukri_assistant login
```

Return to the browser viewer. Sign into Naukri normally, completing any OTP or
CAPTCHA yourself. Then return to the terminal and press **Enter**. Successful login
closes Chromium and saves the session in a Docker named volume.

This is a new browser session: an existing Naukri login in your Windows browser is
not automatically available inside Docker. This tool does not read or save your
password in project files.

## 5. Scan and review matches first

```cmd
docker compose exec desktop python -m naukri_assistant scan --headless
```

Open `reports/latest.json` on your Windows computer and inspect the matches and
scores. This command submits no applications. Docker setup tests do not prove that
Naukri currently accepts automation or that the live UI selectors still work.

## 6. Start automatic applications every 15 minutes

After a successful real scan and checking your filters:

```cmd
docker compose --profile automation up -d worker
docker compose logs -f worker
```

The worker attempts at most three qualifying direct applications per cycle and
20 recorded attempts per day in Asia/Kolkata. You can close the terminal after
starting it; Docker Desktop and your computer must stay running and awake. Ctrl+C
while following logs stops only the log viewer, not the background worker.

The worker exits on a login/CAPTCHA/browser failure and is not automatically
restarted. Inspect `docker compose ps -a` and `docker compose logs worker` for the
outcome. To refresh your login, first stop the worker, repeat step 4, then start it
again. Do not run this Docker worker and GitHub Actions applications for the same
account: the local and remote histories are separate.

## Stop and restart

```cmd
docker compose stop worker
docker compose down
```

`down` removes containers and their network but preserves the named session/history
volume, your local resume, profile and reports. Starting the desktop again reuses
the saved state. **Do not use `docker compose down -v`**: that deletes session and
deduplication history. Rebuilds also retain the volume; do not delete it to fix a
login problem. Stop the worker before interactive login to avoid a locked profile.

Read application history with:

```cmd
docker compose exec desktop python -m naukri_assistant history
```

On macOS/Linux, create the same folders and copy the same files using your file
manager or `cp`; the Docker commands are identical. Bind-mounted folders must be
writable by container UID 1000. Windows Docker Desktop normally handles this;
Linux users may need to adjust the folders' owner/permissions.

## Offline demo and container tests

No account or real job submissions are involved:

```cmd
docker run --rm naukri-assistant:local rank --profile examples/profile.json --jobs examples/jobs.json --output /tmp/demo.json
docker run --rm --shm-size=1g --entrypoint python naukri-assistant:local -m unittest discover -s tests -v
```

For managed cloud builds behind a trusted proxy, the Dockerfile accepts the optional
BuildKit secret `proxy_ca` containing the combined system CA bundle. That trust
material is mounted only for networked build steps and is not baked into the image.
Normal local Docker Desktop builds do not require it. Runtime requests behind an
enterprise proxy must also use the appropriate trusted CA configuration; do not
disable TLS verification.
