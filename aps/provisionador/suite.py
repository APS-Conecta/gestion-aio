# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The suite port: the only code that talks to AIO, the APS name service and Nextcloud's command line (occ).

The Provisionador is built with one: `Suite` in production, `FakeSuite` in the wizard-API tests. A screen or an
install step that needs the suite adds its methods to both, and the fake records what it was asked so the tests can
assert the suite state.

«Dirección» claims the centre's name from the name service, which answers a credential that changes only that
name's address records and, through acme-dns, its certificate's challenge record. The credential is stored
root-only the moment it arrives and reused, so a name is claimed once. AIO is then set to the name, with our Caddy
image as its reverse proxy, and the server's inside address is published; the host's timer publishes it again
every 10 minutes (`provisionador.py publish`).

«Instalar» drives AIO's own page as its interface does from a same-origin browser, signed in with the passphrase AIO
keeps on this server: its optional-containers form, its backup settings and its start button. AIO closes that login once
Apache runs. The first time its page opens, AIO makes the backup's encryption password, which the Provisionador only
reads. The passphrase and that password leave this process only in the passwords file. The containers' progress is AIO's
own reading of their state, as its page lists them. The server's cores and memory, which decide Talk recording, are read
here too. Every later install step runs occ in the Nextcloud container. The apps step downloads App releases from
GitHub, without a token, and unpacks the ones it verified into Nextcloud's custom_apps; the theme step places the theme
folder in Nextcloud's themes folder, which upstream's updates keep, and the theme and apps steps have
aps/images/nextcloud/browser-texts.php build the browser's texts.
"""

import base64
import html
import http.client
import io
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CREDENTIAL = Path("/var/lib/aps-conecta/name-service.json")
APP_KEY = Path(__file__).resolve().parent / "apps.pub"  # the app key's public half, in signify's format
UNREACHABLE = ("No se pudo conectar con el servicio de nombres de APS Conecta. Revise la conexión a internet del "
               "servidor y presione «Reintentar».")
LIMITED = ("El servicio de nombres de APS Conecta limita las direcciones que se obtienen desde una misma red cada día y "
           "cada semana. Presione «Reintentar» más tarde.")
REFUSED = "El servicio de nombres de APS Conecta rechazó la solicitud (HTTP {code}). Contacte a APS Conecta."
SUITE_DOWN = "El contenedor maestro de la suite no responde. Revise que Docker esté en marcha y presione «Reintentar»."
SUITE_REFUSED = ("El contenedor maestro de la suite rechazó la instalación (HTTP {code}). Presione «Reintentar»; si "
                 "vuelve a ocurrir, contacte a APS Conecta.")
NOT_STARTED = ("El contenedor maestro no logró iniciar todos los contenedores de la suite. Presione «Reintentar»; si "
               "vuelve a ocurrir, contacte a APS Conecta.")
NEXTCLOUD_DOWN = ("El contenedor de Nextcloud no responde. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS "
                  "Conecta.")
OCC_FAILED = "Nextcloud no pudo completar «occ {}». Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta."
DOWNLOAD_RETRIES = (5, 15, 30)  # seconds before each new try of a GitHub download GitHub itself failed
GITHUB_FAILED = ("No se pudo descargar {} desde GitHub. Revise la conexión a internet del servidor y presione "
                 "«Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
GITHUB_LIMITED = ("GitHub limitó las consultas de este servidor al pedir {}. Presione «Reintentar» en una hora; si "
                  "vuelve a ocurrir, contacte a APS Conecta.")
SEED_FAILED = ("Nextcloud no pudo recibir las carpetas y páginas de las carpetas de equipo. Presione «Reintentar»; si "
               "vuelve a ocurrir, contacte a APS Conecta.")
UNPACK_FAILED = ("Nextcloud no pudo recibir los archivos de la aplicación. Presione «Reintentar»; si vuelve a ocurrir, "
                 "contacte a APS Conecta.")
THEME_FAILED = ("No se pudo instalar el tema de APS Conecta Gestión en el servidor de aplicaciones. Presione "
                "«Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
NO_BACKUP_PASSWORD = ("El contenedor maestro de la suite aún no tiene la contraseña de cifrado de las copias de "
                      "seguridad, que el archivo de contraseñas debe incluir. Presione «Reintentar»; si vuelve a "
                      "ocurrir, contacte a APS Conecta.")
ISSUER_FAILED = ("No se pudo obtener ni verificar el certificado intermedio de {}. Revise la conexión a internet del "
                 "servidor y presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
MASTERCONTAINER = "nextcloud-aio-mastercontainer"
NEXTCLOUD = "nextcloud-aio-nextcloud"
DOWN = {MASTERCONTAINER: SUITE_DOWN, NEXTCLOUD: NEXTCLOUD_DOWN}
AIO_CONFIG = "/mnt/docker-aio-config/data/configuration.json"  # AIO's settings, its passphrase among them
AIO_AUTHORITY = "/mnt/docker-aio-config/caddy-internal/pki/authorities/local/root.crt"  # signs AIO's page certificate
OFFICE_SUITES = {"collabora", "onlyoffice", "eurooffice"}  # AIO's office suites, one choice among them
HIDDEN = "***REMOVED SENSITIVE VALUE***"  # what occ config:list shows of a value Nextcloud counts as sensitive
# How AIO's start answer ends once every container started (DockerController::getStreamingResponseHtmlEnd); when one
# fails, it stops short or Slim's error page follows.
STARTED = "</body>\n</html>"
# How that answer says AIO begins to download a container's image, by AIO's name for it, HTML-escaped
# (DockerActionManager::PullImage, written by DockerController::getAddToStreamingResponseBody). A progress mark, <span
# class='progress-indicator'></span>, follows every few seconds while the download moves, then «Iniciando el
# contenedor» once AIO created the container.
DOWNLOADING = re.compile(r"<div>([^<]*): Descargando la imagen</div>")


class Taken(Exception):
    """Another installation holds the name."""


class Unreachable(Exception):
    """The name service, the internet or a container of the suite did not do what was asked; the Spanish message says
    why."""

    def __init__(self, message=UNREACHABLE):
        super().__init__(message)


# AIO's first-run setup (so its passphrase exists), then the name as its domain (the claim vouches for it), our
# Caddy image as the reverse proxy and, among AIO's secrets, the values community-containers/caddy/caddy.json passes
# it. The values arrive on stdin only.
CONFIGURE = r"""
ini_set('display_errors', 'stderr');
require '/var/www/docker-aio/php/vendor/autoload.php';
$container = \AIO\DependencyInjection::GetContainer();
$container->get(\AIO\Data\Setup::class)->Setup();
$config = $container->get(\AIO\Data\ConfigurationManager::class);
$in = json_decode(stream_get_contents(STDIN), true, 4, JSON_THROW_ON_ERROR);
$config->aioCommunityContainers = ['caddy'];
$config->setDomain($in['domain'], true);
(function (array $values): void {
    $this->set('secrets', array_merge($this->get('secrets', []), $values));
})->call($config, $in['secrets']);
"""


# The containers AIO's page lists (all but its own helpers), with AIO's reading of their state: stopped, starting
# (its port does not answer yet) or running.
CONTAINERS = r"""
ini_set('display_errors', 'stderr');
require '/var/www/docker-aio/php/vendor/autoload.php';
$definition = \AIO\DependencyInjection::GetContainer()->get(\AIO\ContainerDefinitionFetcher::class);
$listed = [];
foreach ($definition->FetchDefinition() as $c) {
    if (!$c->hideFromList) {
        $listed[] = ['id' => $c->identifier, 'name' => $c->displayName, 'state' => $c->GetStartingState()->value];
    }
}
echo json_encode($listed, JSON_THROW_ON_ERROR);
"""


# Nextcloud's command line, in its container, with its input from a file: config:import reads its input without
# waiting for it.
OCC = """input=$(mktemp) && cat > "$input" && php /var/www/html/occ --no-ansi --no-interaction "$@" < "$input"
status=$?; rm -f "$input"; exit $status"""


# In the Nextcloud container, for the host in $1 that serves only its leaf: the CA its certificate names (its CA
# Issuers pointer) is imported into Nextcloud's trusted certificates once it is verified to chain the leaf to a root
# the container trusts, as Nextcloud's own requests then do. An import already there is kept.
ISSUER = r"""
set -eu
host=$1
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
timeout 20 openssl s_client -connect "$host:443" -servername "$host" < /dev/null 2> /dev/null \
  | openssl x509 > "$dir/leaf"
issuer=$(openssl x509 -in "$dir/leaf" -noout -ext authorityInfoAccess | sed -n 's/^ *CA Issuers - URI://p' | head -n 1)
name=$(basename "$issuer" .crt).pem
case "$issuer" in http://*.crt | https://*.crt) ;; *) echo "no CA Issuers pointer: $issuer" >&2; exit 1 ;; esac
case "$name" in *[!A-Za-z0-9._-]*) echo "unexpected issuer name: $name" >&2; exit 1 ;; esac
php /var/www/html/occ security:certificates --output=json | grep -qF "\"$name\"" && exit 0
curl -fsS --max-time 20 --proto =http,https -o "$dir/issuer" "$issuer"
openssl x509 -inform DER -in "$dir/issuer" -out "$dir/$name" 2> /dev/null \
  || openssl x509 -in "$dir/issuer" -out "$dir/$name"
openssl verify -untrusted "$dir/$name" -verify_hostname "$host" "$dir/leaf"
php /var/www/html/occ security:certificates:import "$dir/$name"
"""


# In the Nextcloud container, the account $1 named $2, unless it exists, with the first password on stdin, which occ
# reads from its environment.
ADD_USER = r"""
set -eu
php /var/www/html/occ --no-ansi user:info "$1" > /dev/null 2>&1 && exit 0
OC_PASS=$(cat)
export OC_PASS
php /var/www/html/occ --no-ansi --no-interaction user:add --password-from-env --display-name="$2" "$1" > /dev/null
"""


# In the Nextcloud container, the group folder $1's missing folders and files, from the JSON object on stdin that holds
# each file's text, or null for a folder, by its path in the group folder; whatever the folder holds stays. Groupfolders
# then scans the folder, so Nextcloud lists what was added.
SEED = r"""
set -eu
files="$(php /var/www/html/occ --no-ansi config:system:get datadirectory)/__groupfolders/$1/files"
php -r '
foreach (json_decode(stream_get_contents(STDIN), true, 3, JSON_THROW_ON_ERROR) as $path => $text) {
    $target = $argv[1] . "/" . $path;
    $folder = $text === null ? $target : dirname($target);
    if (in_array("..", explode("/", $path), true) || !(is_dir($folder) || mkdir($folder, 0755, true))) {
        exit(1);
    }
    if ($text !== null && !file_exists($target) && file_put_contents($target, $text) === false) {
        exit(1);
    }
}' -- "$files"
php /var/www/html/occ --no-ansi groupfolders:scan "$1" > /dev/null
"""


# In the Nextcloud container, the app $1's tarball, in base64 on stdin, in place of any copy in custom_apps.
UNPACK = """set -eu
rm -rf "/var/www/html/custom_apps/${1:?}"
base64 -d | tar -xzf - -C /var/www/html/custom_apps"""


# In the Nextcloud container, the theme folder named $1 in Nextcloud's themes folder (upstream's updates keep it,
# Containers/nextcloud/upgrade.exclude), replaced whole by the tar archive that arrives on stdin in base64.
THEME = r"""
set -eu
cd /var/www/html/themes
rm -rf ".$1.new" ".$1.old"
mkdir ".$1.new"
base64 -d | tar -x -C ".$1.new"
if [ -e "$1" ]; then mv "$1" ".$1.old"; fi
mv ".$1.new" "$1"
rm -rf ".$1.old"
"""


# In the Nextcloud container: each CA of AIO's own folder of trusted CAs, which AIO mounts there only when it has one
# (a test build's Let's Encrypt staging roots), imported into Nextcloud's trusted certificates, as AIO's own start
# leaves them out.
CACERTS = r"""
set -eu
[ -n "${TRUSTED_CACERTS_DIR:-}" ] || exit 0
for ca in /usr/local/share/ca-certificates/*.crt; do php /var/www/html/occ security:certificates:import "$ca"; done
"""


def downloads(listed, downloading):
    """A reader of AIO's start answer, given its text so far each time more arrives: it hands `downloading` each
    container of `listed`, as `containers` lists them, whose image AIO begins to download, once and in turn."""
    by_name, told = {c["name"]: c for c in listed}, 0

    def read(text):
        nonlocal told
        found = DOWNLOADING.findall(text)
        for name in found[told:]:
            if (container := by_name.get(html.unescape(name))) is not None:
                downloading(container)
        told = len(found)

    return read


def write_root_only(path, text):
    """Replace the file with `text`, readable by its owner (root, on the server) alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


class AioPage:
    """AIO's own page on this host's loopback, driven as its interface drives it from a same-origin browser: the login
    form, then the forms its page posts with the session's CSRF token. Its certificate is trusted only if AIO's internal
    authority signed it."""

    def __init__(self, authority, host):
        self.context = ssl.create_default_context(cadata=authority)
        self.context.check_hostname = False  # the loopback address: the authority alone vouches for the page
        self.host = host  # what AIO records as its own address (AIO_URL), as a browser's address bar would
        self.cookie = ""
        self.csrf = {}

    def request(self, method, path, form=None, timeout=60, each=None):
        """The status and text AIO answers; `each`, if given, gets the text so far each time more of it arrives."""
        headers = {"Host": self.host, "Sec-Fetch-Site": "same-origin"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        body = None
        if form is not None:
            body = urllib.parse.urlencode({**self.csrf, **form})
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection = http.client.HTTPSConnection("127.0.0.1", 8080, context=self.context, timeout=timeout)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            data = b""
            while chunk := response.read1():
                data += chunk
                if each:
                    each(data.decode(errors="replace"))
            text = data.decode(errors="replace")
        except (OSError, http.client.HTTPException) as error:
            print(f"AIO {path}: {error}", file=sys.stderr)
            raise Unreachable(SUITE_DOWN) from error
        finally:
            connection.close()
        for cookie in response.headers.get_all("Set-Cookie") or ():
            self.cookie = cookie.split(";", 1)[0]  # AIO's one session cookie
        return response.status, text

    def post(self, path, form, expected=201, timeout=60, each=None):
        status, text = self.request("POST", path, form, timeout, each)
        if status != expected:
            print(f"AIO {path}: {status} {text[-2000:]}", file=sys.stderr)
            raise Unreachable(SUITE_REFUSED.format(code=status))
        return text

    def log_in(self, passphrase):
        """Sign in with the passphrase, then open the page after login, which joins the mastercontainer to the suite's
        network."""
        _, page = self.request("GET", "/login")
        self.csrf = dict(re.findall(r'name="(csrf_name|csrf_value)" value="([^"]*)"', page))
        self.post("/api/auth/login", {"password": passphrase})
        self.request("GET", "/containers")


class Suite:
    """The real adapter, driving AIO, the name service and occ on this server."""

    def __init__(self, services, credential=CREDENTIAL):
        """`services` is the release's services.json (aps/release/package.sh): the name service's `url`, the
        `suffix` of its names and the ACME directory, `acme_ca`, the certificates come from."""
        self.services = json.loads(Path(services).read_text(encoding="utf-8"))
        self.suffix = self.services["suffix"]
        self.credential = Path(credential)
        self.app_key = APP_KEY.read_text(encoding="ascii")

    def call(self, path, body, headers=()):
        """The name service's JSON answer. A name in use is Taken; the claim limits, any other refusal, a server error
        or a network failure is Unreachable with its Spanish message. Refusals are logged, never a credential."""
        request = urllib.request.Request(
            self.services["url"] + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "aps-conecta-gestion", **dict(headers)})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            print(f"name service {path}: {error.code} {error.read(500).decode(errors='replace')}", file=sys.stderr)
            if error.code == 409:
                raise Taken() from error
            if error.code == 429 and path == "/claim":
                raise Unreachable(LIMITED) from error
            raise Unreachable(REFUSED.format(code=error.code) if error.code < 500 else UNREACHABLE) from error
        except (OSError, ValueError) as error:
            print(f"name service {path}: {error}", file=sys.stderr)
            raise Unreachable() from error

    def stored(self):
        try:
            return json.loads(self.credential.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def claimed(self):
        """The stored claim's DEIS code and name, or None before the claim."""
        credential = self.stored()
        return credential and {"code": credential["username"], "name": credential["name"]}

    def claim(self, code, name):
        """Claim the DEIS code's name unless a claim is stored, set AIO to the stored claim's name and return it. The
        name service answers the name; `name`, the slug table's, is what the fake answers."""
        credential = self.stored()
        if credential is None:
            credential = self.call("/claim", {"code": code})
            write_root_only(self.credential, json.dumps(credential))
        values = {"ACMEDNS_USERNAME": credential["username"], "ACMEDNS_PASSWORD": credential["password"],
                  "ACMEDNS_SUBDOMAIN": credential["subdomain"], "ACMEDNS_SERVER_URL": credential["server_url"],
                  "ACME_CA": self.services["acme_ca"]}
        self.mastercontainer("php", "-r", CONFIGURE,
                             stdin=json.dumps({"domain": credential["name"], "secrets": values}))
        return credential["name"]

    @staticmethod
    def execute(container, command, stdin="", failed=None):
        """What a command run in the container as its web user printed. What it printed may be a secret and is never
        logged; a failure logs its errors only and is Unreachable: the container's own message when it does not answer,
        `failed` when the command fails."""
        try:
            run = subprocess.run(["docker", "exec", "--interactive", "--user", "www-data", container, *command],
                                 input=stdin, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"{container} {command[0]}: {error}", file=sys.stderr)
            raise Unreachable(DOWN[container]) from error
        if run.returncode:
            print(f"{container} {command[0]}: {run.stderr}", file=sys.stderr)
            answered = "Error response from daemon" not in run.stderr and "No such container" not in run.stderr
            raise Unreachable(failed if failed and answered else DOWN[container])
        return run.stdout

    def mastercontainer(self, *command, stdin=""):
        return self.execute(MASTERCONTAINER, command, stdin)

    def occ(self, *args, stdin=""):
        """What Nextcloud's command line printed."""
        return self.execute(NEXTCLOUD, ("sh", "-c", OCC, "sh", *args), stdin, OCC_FAILED.format(" ".join(args)))

    def theme(self, name, files):
        """The theme folder `name` in Nextcloud's themes folder holds the files, by their path in it, and no other."""
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for path, data in sorted(files.items()):
                info = tarfile.TarInfo(path)
                info.size, info.mode = len(data), 0o644
                tar.addfile(info, io.BytesIO(data))
        self.execute(NEXTCLOUD, ("sh", "-c", THEME, "sh", name), base64.b64encode(archive.getvalue()).decode(),
                     THEME_FAILED)

    def browser_texts(self):
        """The browser's texts of the apps Nextcloud holds now, as the image's browser-texts.php builds them from the
        theme folders."""
        self.execute(NEXTCLOUD, ("php", "/browser-texts.php"), failed=THEME_FAILED)

    def trust_issuer(self, host):
        """Nextcloud's own requests verify a host that serves only its leaf certificate: the CA that issued it is among
        Nextcloud's trusted certificates."""
        self.execute(NEXTCLOUD, ("sh", "-c", ISSUER, "sh", host), failed=ISSUER_FAILED.format(host))

    @staticmethod
    def download(url, what, method="GET"):
        """What GitHub serves at the URL, or for HEAD the URL it redirects to; a failure is Unreachable, naming `what`:
        GitHub's rate limit (403 or 429 without a token) asks to retry later. GitHub's own failures now and then (an
        answer of 500 or more, a dropped or timed-out connection) are tried again after each of DOWNLOAD_RETRIES first."""
        request = urllib.request.Request(url, method=method, headers={"User-Agent": "aps-conecta-gestion"})
        for wait in (*DOWNLOAD_RETRIES, None):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.geturl() if method == "HEAD" else response.read()
            except (OSError, http.client.HTTPException) as error:
                print(f"GitHub {url}: {error}", file=sys.stderr)
                answered = isinstance(error, urllib.error.HTTPError)
                if wait is None or (answered and error.code < 500):
                    limited = answered and error.code in (403, 429)
                    raise Unreachable((GITHUB_LIMITED if limited else GITHUB_FAILED).format(what)) from error
                time.sleep(wait)

    def latest(self, repo):
        """The tag of the repo's latest release, from where GitHub redirects /releases/latest, with no API call; None
        when the repo has none."""
        final = self.download(f"https://github.com/{repo}/releases/latest", f"la última versión de {repo}", "HEAD")
        found = re.search(r"/releases/tag/([^/?#]+)$", final)
        return found and urllib.parse.unquote(found.group(1))

    def releases(self, repo):
        """The tags of the repo's published releases, newest first, from one call to GitHub's API."""
        listed = self.download(f"https://api.github.com/repos/{repo}/releases?per_page=100", f"las versiones de {repo}")
        try:
            return [r["tag_name"] for r in json.loads(listed) if not (r["draft"] or r["prerelease"])]
        except (ValueError, TypeError, KeyError) as error:
            raise Unreachable(GITHUB_FAILED.format(f"las versiones de {repo}")) from error

    def asset(self, repo, tag, name):
        """One file of the repo's release."""
        return self.download(f"https://github.com/{repo}/releases/download/{tag}/{name}", name)

    def unpack(self, app, tarball):
        """Unpack the app's tarball into Nextcloud's custom_apps, in place of any copy there."""
        self.execute(NEXTCLOUD, ("sh", "-c", UNPACK, "sh", app), base64.b64encode(tarball).decode(),
                     UNPACK_FAILED.format(app))

    def add_user(self, user, name, password):
        """The account, with its name and first password, unless it exists: an existing account keeps its own."""
        self.execute(NEXTCLOUD, ("sh", "-c", ADD_USER, "sh", user, name), password,
                     OCC_FAILED.format(f"user:add {user}"))

    def seed(self, folder, files):
        """The group folder holds each of the files it does not hold yet: their text by their path in it, None for a
        folder. What it holds stays as staff left it."""
        self.execute(NEXTCLOUD, ("sh", "-c", SEED, "sh", str(folder)), json.dumps(files), SEED_FAILED)
    def trust_cacerts(self):
        """Nextcloud's own requests trust the CAs that AIO's folder of trusted CAs holds, as Talk does; a release has
        none."""
        self.execute(NEXTCLOUD, ("sh", "-c", CACERTS, "sh"), failed=OCC_FAILED.format("security:certificates:import"))

    def secrets(self):
        """AIO's passphrase, the first password of the suite's administrator, `admin`, and the backup's encryption
        password, as AIO keeps them here; without the last, Unreachable."""
        config = json.loads(self.mastercontainer("cat", AIO_CONFIG))
        backup_password = config["secrets"].get("BORGBACKUP_PASSWORD")
        if not backup_password:
            raise Unreachable(NO_BACKUP_PASSWORD)
        return {"passphrase": config["password"], "administrator": config["secrets"]["NEXTCLOUD_PASSWORD"],
                "backup_password": backup_password}

    def _signed_in(self):
        """AIO's page, signed in with the passphrase AIO's first-run setup (on «Dirección») stored on this server."""
        page = AioPage(self.mastercontainer("cat", AIO_AUTHORITY), f"{self.stored()['name']}:8080")
        page.log_in(json.loads(self.mastercontainer("cat", AIO_CONFIG))["password"])
        return page

    @staticmethod
    def hardware():
        """The CPU cores and the memory of this server, which runs the suite; the memory in GiB, rounded up as the
        bootstrap's check rounds it."""
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return {"cpus": len(os.sched_getaffinity(0)), "memory_gib": (memory + 2 ** 30 - 1) // 2 ** 30}

    def switch(self, components):
        """Turn the given optional containers on and every other one off, with AIO's optional-containers form
        (php/templates/includes/optional-containers.twig): a switch it does not send is off, and the office suite is
        one choice."""
        form = {"options-form": "options-form", "office_suite_choice": ""}
        for component in components:
            if component in OFFICE_SUITES:
                form["office_suite_choice"] = component
            else:
                form[component] = "on"
        self._signed_in().post("/api/configuration", form)

    def back_up(self, folder, time):
        """AIO's own backup to the folder on this server, daily at the time (UTC), with its success notification and
        without its automatic updates: what the suite runs changes only with a suite release."""
        self._signed_in().post("/api/configuration", {"borg_backup_host_location": folder, "borg_remote_repo": "",
                                                      "daily_backup_time": time, "success_notification": "on"})

    def start(self, downloading):
        """AIO's start button: it pulls, creates and starts every container in order, and answers once it is done. As
        its answer arrives, `downloading` gets each container whose image AIO begins to download."""
        page = self._signed_in()
        text = page.post("/api/docker/start", {}, expected=200, timeout=900,
                         each=downloads(self.containers(), downloading))
        if not text.rstrip().endswith(STARTED):
            print(f"AIO did not start the suite: {text[-2000:]}", file=sys.stderr)
            raise Unreachable(NOT_STARTED)

    def containers(self):
        """Each container AIO's page lists, by its id and AIO's name, with its state: stopped, starting or running."""
        listed = json.loads(self.mastercontainer("php", "-r", CONTAINERS))
        return [{**c, "state": c["state"] if c["state"] in ("starting", "running") else "stopped"} for c in listed]

    def publish(self, address):
        """Point the claimed name at `address`; nothing before the claim."""
        credential = self.stored()
        if credential is not None:
            self.call("/address", {"address": address},
                      {"X-Api-User": credential["username"], "X-Api-Key": credential["password"]})

    def answers(self, name, address):
        """Whether the suite's reverse proxy presents a certificate for the name at the server's inside address. The
        operator's browser, not this probe, judges the certificate."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((address, 443), timeout=3) as raw, \
                    context.wrap_socket(raw, server_hostname=name):
                return True
        except OSError:
            return False

    def eurooffice_answers(self):
        """Whether Euro-Office answers Nextcloud, by its connector's own check: Euro-Office converts a file it fetches
        from Nextcloud."""
        try:
            self.occ("eurooffice:documentserver", "--check")
            return True
        except Unreachable:
            return False


class FakeSuite:
    """The in-memory suite of the wizard-API tests, with the name service it claims from."""

    suffix = "gestion.apsconecta.cl"
    nextcloud = 34  # the major version of the Nextcloud it runs
    ADMINISTRATOR = "primera-contraseña-del-administrador"
    PASSPHRASE = "frase de contraseña de AIO"
    # The app settings occ config:list hides, as Nextcloud's own list of sensitive ones names them.
    SENSITIVE = {"theming": ("imprintUrl", "privacyUrl", "slogan", "url")}
    BACKUP_PASSWORD = "clave-de-cifrado-de-aio"
    PERMISSIONS = {"read": 1, "write": 6, "delete": 8, "share": 16}  # Groupfolders' words for its permission bits

    def __init__(self):
        self.in_use = set()  # DEIS codes whose name another installation holds
        # Calls in an outage: the name service's "claim" and "address", "aio" (the mastercontainer), "nextcloud"
        # (Nextcloud's container), "issuer" (a host's CA, out of the internet's reach) and "eurooffice" (Euro-Office,
        # which does not answer Nextcloud).
        self.down = set()
        self.limited = False  # the name service refuses claims over its limits
        self.claims = []  # the DEIS codes the name service gave a name
        self.credential = None  # the stored claim
        self.domain = None  # the name AIO is set to
        self.published = []  # the inside addresses published
        # Each account, by its username.
        self.users = {"admin": {"name": "admin", "password": self.ADMINISTRATOR, "enabled": True}}
        self.groups = {"admin": {"name": "admin", "members": {"admin"}}}  # each group, by its id: its name and members
        self.group_folders = {}  # by id: each group folder's mount point, its groups' permissions and its seeded files
        self.answering = True  # whether the suite's reverse proxy answers at its name
        self.machine = {"cpus": 2, "memory_gib": 6}  # the server's hardware: install.sh's floors (Recording tests)
        self.switched = None  # the optional containers AIO was set to run; every other one is off
        self.backup = None  # AIO's nightly backup: (folder, time)
        self.backup_password = self.BACKUP_PASSWORD  # its encryption password, which AIO made, or None
        self.starts = 0  # how often AIO's start button was pressed
        self.states = {}  # what AIO reports per container id: (AIO's name, state: stopped, starting or running)
        self.release = threading.Event()  # AIO's start answers once it is set: tests clear it to watch a start
        self.release.set()
        self.dies = False  # the Provisionador dies while AIO's start runs; AIO goes on
        self.system = {}  # Nextcloud's system settings
        # Each installed app's settings, as Nextcloud ships them: Nextcloud's own voice on.
        self.apps = {"core": {}, "admin_audit": {}, "theming": {}, "office": {}, "eurooffice": {},
                     **{app: {"enabled": "yes"} for app in ("nextcloud_announcements", "firstrunwizard",
                                                            "survey_client", "support")}}
        # The apps AIO's start installs and sets up with the components' switches: the antivirus, which only logs what
        # its background scan finds infected, and full-text search, with the store's files provider.
        self.apps |= {"files_antivirus": {"enabled": "yes", "av_infected_action": "only_log"},
                      **{app: {"enabled": "yes"} for app in ("fulltextsearch", "fulltextsearch_elasticsearch",
                                                              "files_fulltextsearch")}}
        self.outdated = set()  # apps whose code changed while enabled: Nextcloud waits for their upgrade
        self.indexed = False  # whether full-text search's index was built
        self.commands = []  # every occ command, by its arguments
        self.imports = []  # the settings each config:import brought
        self.issuers = []  # the hosts whose certificate's issuer Nextcloud trusts
        self.app_key = ""  # the app key the suite ships; the tests sign their App releases with their own
        self.app_releases = {}  # GitHub's App releases, by repo, newest first: {tag: {file name: bytes}}
        self.store = set()  # the ids of the apps the open app store offers
        self.unpacked = []  # each app id and tarball unpacked into custom_apps, in turn
        self.api_calls = 0  # the calls to GitHub's API
        self.themes = {}  # each theme folder in Nextcloud's themes folder: its files by their path in it
        self.cacerts = []  # AIO's folder of trusted CAs: a test build's staging roots; a release has none
        self.trusted = []  # the CAs of that folder Nextcloud imported
        self.texts_built = []  # each build of the browser's texts: the theme folders and the enabled apps it found

    def claim(self, code, name):
        if self.credential is None:
            if "claim" in self.down:
                raise Unreachable()
            if self.limited:
                raise Unreachable(LIMITED)
            if code in self.in_use:
                raise Taken()
            self.claims.append(code)
            self.credential = {"username": code, "name": name}
        self.domain = self.credential["name"]
        return self.domain

    def claimed(self):
        return self.credential and {"code": self.credential["username"], "name": self.credential["name"]}

    def publish(self, address):
        if self.credential is None:
            return
        if "address" in self.down:
            raise Unreachable()
        self.published.append(address)

    def answers(self, name, address):
        return self.answering and name == self.domain

    def eurooffice_answers(self):
        return not self.down & {"nextcloud", "eurooffice"} and self.apps.get("eurooffice", {}).get("enabled") == "yes"

    def _outage_guard(self, part="aio"):
        if part in self.down:
            raise Unreachable(NEXTCLOUD_DOWN if part == "nextcloud" else SUITE_DOWN)

    def _signed_in(self):
        """AIO's login, which closes once Apache runs."""
        self._outage_guard()
        if self.states.get("nextcloud-aio-apache", (None, None))[1] == "running":
            raise Unreachable(SUITE_REFUSED.format(code=403))

    def hardware(self):
        return dict(self.machine)

    def switch(self, components):
        self._signed_in()
        self.switched = set(components)

    def back_up(self, folder, time):
        self._signed_in()
        self.backup = (folder, time)

    def start(self, downloading):
        """AIO's start answers once every container it started runs. Its answer streams as AIO's does: each container
        that does not run, in turn, has its image downloaded, with a progress mark, and is started."""
        self._signed_in()
        listed = self.containers()
        read, text = downloads(listed, downloading), ""
        for c in listed:
            if c["state"] == "stopped":
                name = html.escape(c["name"])
                text += f"<div>{name}: Descargando la imagen</div><span class='progress-indicator'></span>"
                read(text)
                text += f"<div>{name}: Iniciando el contenedor</div>"
                read(text)
        self.starts += 1
        self.release.wait()
        if self.dies:
            raise SystemExit  # the thread ends without a word, as the process would
        self.states = {id_: (name, "running") for id_, (name, _) in self.states.items()}

    def containers(self):
        self._outage_guard()
        return [{"id": id_, "name": name, "state": state} for id_, (name, state) in self.states.items()]

    def occ(self, *args, stdin=""):
        """Nextcloud's command line for the commands the install steps run: settings in, settings out, an app on, the
        teams' groups, folders and welcome page, and the people's accounts."""
        self._outage_guard("nextcloud")
        self.commands.append(args)
        if args[0].split(":")[0] in ("group", "groupfolders", "intravox"):
            return self._teams(*args)
        if args[0].startswith("user:"):
            return self._users(*args)
        if args == ("config:list",):
            apps = {app: {key: HIDDEN if key in self.SENSITIVE.get(app, ()) else value for key, value in values.items()}
                    for app, values in self.apps.items()}
            return json.dumps({"system": self.system, "apps": apps})
        if args[0] == "config:app:get" and args[3:] == ("--output=json",):
            return json.dumps(self.apps[args[1]][args[2]])
        if args == ("config:import",):
            settings = json.loads(stdin)
            self.imports.append(settings)
            self.system.update(settings.get("system", {}))
            for app, values in settings.get("apps", {}).items():
                self.apps.setdefault(app, {}).update(values)
            return ""
        if args == ("status", "--output=json"):
            return json.dumps({"installed": True, "version": f"{self.nextcloud}.0.0.0"})
        if args[0] == "app:enable" and len(args) == 2:
            if args[1] not in self.apps and args[1] not in self.store:
                raise Unreachable(OCC_FAILED.format(" ".join(args)))
            if self.apps.setdefault(args[1], {}).get("enabled") != "yes":
                self.outdated.discard(args[1])  # enabling an app runs its upgrade; an enabled one is left as it is
            self.apps[args[1]]["enabled"] = "yes"
            return ""
        if args[0] == "app:disable" and len(args) == 2:
            if args[1] in self.apps:
                self.apps[args[1]]["enabled"] = "no"
            return ""
        if args[0] == "theming:config" and len(args) == 3:
            return self._theming(*args[1:])
        if args == ("fulltextsearch:index", "--no-readline"):
            if self.apps["fulltextsearch"].get("enabled") != "yes":  # a disabled app's commands are not there
                raise Unreachable(OCC_FAILED.format(" ".join(args)))
            self.indexed = True
            return ""
        raise AssertionError(f"the fake suite runs no occ {args}")

    def latest(self, repo):
        return next(iter(self.app_releases.get(repo, {})), None)

    def releases(self, repo):
        self.api_calls += 1
        return list(self.app_releases.get(repo, {}))

    def asset(self, repo, tag, name):
        """A file the release does not hold fails to download."""
        if name not in self.app_releases.get(repo, {}).get(tag, {}):
            raise Unreachable(GITHUB_FAILED.format(name))
        return self.app_releases[repo][tag][name]

    def unpack(self, app, tarball):
        self._outage_guard("nextcloud")
        self.unpacked.append((app, tarball))
        if self.apps.setdefault(app, {}).get("enabled") == "yes":
            self.outdated.add(app)
    def _theming(self, key, value):
        """Theming's settings as occ theming:config stores them; an image is a file of a placed theme folder."""
        theming = self.apps["theming"]
        if key == "background" and value == "backgroundColor":
            theming["backgroundMime"] = value
        elif key in ("logo", "logoheader", "favicon", "background"):
            name, _, path = value.removeprefix("/var/www/html/themes/").partition("/")
            if path not in self.themes.get(name, {}):
                raise Unreachable(OCC_FAILED.format(f"theming:config {key} {value}"))
            theming[f"{key}Mime"] = "image/svg+xml"
        else:
            theming[key] = value in ("1", "true", "yes", "on") if key == "disable-user-theming" else value
        return ""

    def theme(self, name, files):
        self._outage_guard("nextcloud")
        self.themes[name] = dict(files)

    def browser_texts(self):
        self._outage_guard("nextcloud")
        self.texts_built.append({"themes": set(self.themes),
                                 "apps": {app for app, values in self.apps.items() if values.get("enabled") == "yes"}})

    def trust_issuer(self, host):
        self._outage_guard("nextcloud")
        if "issuer" in self.down:
            raise Unreachable(ISSUER_FAILED.format(host))
        if host not in self.issuers:
            self.issuers.append(host)

    def trust_cacerts(self):
        self._outage_guard("nextcloud")
        self.trusted = list(self.cacerts)

    def secrets(self):
        self._outage_guard()
        if not self.backup_password:
            raise Unreachable(NO_BACKUP_PASSWORD)
        return {"passphrase": self.PASSPHRASE, "administrator": self.ADMINISTRATOR,
                "backup_password": self.backup_password}

    def _teams(self, command, *args):
        """occ's groups, Groupfolders' folders and IntraVox's setup, as the «Equipos» steps run them. An app's commands
        fail while it is not enabled, as occ does not know them then."""
        failed = Unreachable(OCC_FAILED.format(" ".join((command, *args))))
        app = command.split(":")[0]
        if app != "group" and self.apps.get(app, {}).get("enabled") != "yes":
            raise failed
        mounts = {folder["mount"]: id_ for id_, folder in self.group_folders.items()}
        if (command, args) == ("group:list", ("--output=json",)):
            return json.dumps({gid: sorted(group["members"]) for gid, group in self.groups.items()})
        if command == "group:add" and len(args) == 2 and args[0].startswith("--display-name="):
            if args[1] in self.groups:
                raise failed
            self.groups[args[1]] = {"name": args[0].removeprefix("--display-name="), "members": set()}
        elif command in ("group:adduser", "group:removeuser") and len(args) == 2:
            if args[0] not in self.groups or args[1] not in self.users:
                raise failed
            if command == "group:adduser":
                self.groups[args[0]]["members"].add(args[1])
            else:
                self.groups[args[0]]["members"].discard(args[1])
        elif (command, args) == ("groupfolders:list", ("--output=json",)):
            return json.dumps([{"id": id_, "mountPoint": folder["mount"], "groups_list": folder["groups"] or []}
                               for id_, folder in self.group_folders.items()])
        elif command == "groupfolders:create" and len(args) == 1:
            if args[0] in mounts:
                raise failed
            return f"{self._group_folder(args[0], {})}\n"
        elif command == "groupfolders:group" and len(args) >= 2:
            folder, group, *words = args
            granted = self.group_folders[int(folder)]["groups"]
            if words == ["--delete"]:
                granted.pop(group, None)
            elif group in self.groups and set(words) <= self.PERMISSIONS.keys():
                granted[group] = 1 | sum(self.PERMISSIONS[word] for word in words)
            else:
                raise failed
        elif (command, args) == ("intravox:setup", ("--skip-demo",)):
            if self.apps.get("groupfolders", {}).get("enabled") != "yes":
                raise failed
            for group in ("IntraVox Admins", "IntraVox Editors", "IntraVox Users"):
                self.groups.setdefault(group, {"name": group, "members": set()})
            mount = self.apps[app].get("groupfolder_name", "IntraVox")
            if mount not in mounts:
                self._group_folder(mount, {"admin": 31, "IntraVox Admins": 31, "IntraVox Editors": 7,
                                           "IntraVox Users": 1})
        elif (command, args) != ("intravox:reindex", ()):
            raise AssertionError(f"the fake suite runs no occ {(command, *args)}")
        return ""

    def _users(self, command, *args):
        """occ's accounts, as the people step reads and sets them: Nextcloud keeps an email trimmed and in lower case,
        and refuses a display name the account already has. occ reads a word that starts with «-» as an option, and
        fails on one it does not know, unless «--» came before it."""
        failed = Unreachable(OCC_FAILED.format(" ".join((command, *args))))
        if (command, args) == ("user:list", ("--info", "--limit=0", "--output=json")):
            return json.dumps({uid: {"display_name": user["name"], "email": user.get("email", ""),
                                     "enabled": user["enabled"],
                                     "groups": sorted(g for g, group in self.groups.items() if uid in group["members"])}
                               for uid, user in self.users.items()})
        if command in ("user:disable", "user:enable") and len(args) == 1:
            if args[0] not in self.users:
                raise failed
            self.users[args[0]]["enabled"] = command == "user:enable"
            return ""
        ends = args.index("--") if "--" in args else len(args)
        if any(arg.startswith("-") for arg in args[:ends]):
            raise failed
        args = args[:ends] + args[ends + 1:]
        if command == "user:setting" and len(args) == 4 and args[1:3] in (("settings", "email"),
                                                                        ("settings", "display_name")):
            user, _, key, value = args
            if user not in self.users or key == "display_name" and value in ("", self.users[user]["name"]):
                raise failed
            if key == "email":
                self.users[user]["email"] = value.strip().lower()
            else:
                self.users[user]["name"] = value
            return ""
        raise AssertionError(f"the fake suite runs no occ {(command, *args)}")

    def _group_folder(self, mount, groups):
        id_ = len(self.group_folders) + 1
        self.group_folders[id_] = {"mount": mount, "groups": groups, "files": {}}
        return id_

    def add_user(self, user, name, password):
        self._outage_guard("nextcloud")
        self.users.setdefault(user, {"name": name, "password": password, "enabled": True})

    def seed(self, folder, files):
        self._outage_guard("nextcloud")
        held = self.group_folders[folder]["files"]
        for path, text in files.items():
            held.setdefault(path, text)
