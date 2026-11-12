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
here too, as are the GPU «IA local» can use and the disks «Almacenamiento» lists. Before AIO's start, the
«Almacenamiento» step has the port mount the files disk for good (an /etc/fstab entry, an immutable empty mount point,
the mount) and start the mastercontainer again through the bundle's start, which hands AIO the staff's files folder.
Every later install step runs occ in the Nextcloud container; «IA local» switches our «ollama» community container on or
off through AIO's own code in its mastercontainer, as AIO's start would, and asks Ollama, through Nextcloud's container,
which models it holds and to pull one. The apps step downloads App releases from GitHub, without a token, and unpacks
the ones it verified into Nextcloud's custom_apps; the theme step places the theme folder in Nextcloud's themes folder,
which upstream's updates keep, and the theme and apps steps have aps/images/nextcloud/browser-texts.php build the
browser's texts.
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
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CREDENTIAL = Path("/var/lib/aps-conecta/name-service.json")
# The staff's files folder the mastercontainer passes to AIO as NEXTCLOUD_DATADIR: written here, read by the bundle's
# mastercontainer start (aps/bootstrap/mastercontainer.sh) at every start of the mastercontainer.
FILES_FOLDER = Path("/var/lib/aps-conecta/files-folder")
START = Path(__file__).resolve().parent.parent / "bootstrap" / "mastercontainer.sh"  # in the unpacked bundle
AIO_BACK = 5 * 60  # seconds AIO's page may take to answer again once the mastercontainer started again
WWW_DATA = 33  # the user Nextcloud's container runs as, who owns its data folder (Containers/nextcloud/entrypoint.sh)
APP_KEY = Path(__file__).resolve().parent / "apps.pub"  # the app key's public half, in signify's format
UNREACHABLE = ("No se pudo conectar con el servicio de nombres de APS Conecta. Revise la conexión a internet del "
               "servidor y presione «Reintentar».")
LIMITED = ("El servicio de nombres de APS Conecta limita las direcciones que se obtienen desde una misma red cada día y "
           "cada semana. Presione «Reintentar» más tarde.")
REFUSED = "El servicio de nombres de APS Conecta rechazó la solicitud (HTTP {code}). Contacte a APS Conecta."
SUITE_DOWN = "El contenedor maestro de la suite no responde. Revise que Docker esté en marcha y presione «Reintentar»."
MOUNT_FAILED = ("No se pudo montar el disco {} en {}. Revise que siga conectado y presione «Reintentar»; si vuelve a "
                "ocurrir, contacte a APS Conecta.")
REMOTE = {  # why another computer can't take AIO's backup, as Borg and SSH answer it
    "unreachable": ("El otro computador no responde por SSH en {host}, puerto {port}. Revise su nombre o dirección, su "
                    "puerto y que esté encendido y conectado, y vuelva a probar."),
    "key": ("El otro computador no acepta la clave de AIO para el usuario {user}. Agregue la clave pública de AIO, "
            "que «Almacenamiento» muestra, a ~/.ssh/authorized_keys de ese usuario y vuelva a probar."),
    "borg": ("El otro computador responde, pero no tiene Borg instalado para el usuario {user}. Instale Borg en él y "
             "vuelva a probar."),
    "folder": ("La carpeta {folder} del otro computador no se puede usar: no se puede escribir en ella o ya guarda "
               "otros archivos. Elija otra carpeta y vuelva a probar."),
}
def remote_refusal(why, repo):
    """The Spanish reason, as Unreachable, why the other computer can't take AIO's backup: why, a key of REMOTE."""
    user, host, port, folder = re.fullmatch(r"ssh://([^@]+)@([^:]+):(\d+)(/.*)", repo).groups()
    return Unreachable(REMOTE[why].format(user=user, host=host, port=port, folder=folder))


# What SSH says when the other computer can't be reached at all.
UNREACHABLE_SIGNS = ("connection refused", "could not resolve", "no route to host", "timed out", "network is unreachable")
UNWRITABLE = ("No se pudo escribir en {}, el destino de la copia de seguridad. Revise que el disco esté conectado y "
              "presione «Reintentar», o elija otro destino en «Almacenamiento».")
FILES_FAILED = ("No se pudo preparar la carpeta {} para los archivos del personal. Presione «Reintentar»; si vuelve a "
                "ocurrir, contacte a APS Conecta.")
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
SEED_FAILED = ("Nextcloud no pudo recibir las páginas de la Intranet. Presione «Reintentar»; si vuelve a ocurrir, "
               "contacte a APS Conecta.")
GROUP_ADMIN_FAILED = ("Nextcloud no pudo revisar ni asignar los administradores de grupo de las cuentas de cargo. "
                      "Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
UNPACK_FAILED = ("Nextcloud no pudo recibir los archivos de la aplicación. Presione «Reintentar»; si vuelve a ocurrir, "
                 "contacte a APS Conecta.")
THEME_FAILED = ("No se pudo instalar el tema de APS Conecta Gestión en el servidor de aplicaciones. Presione "
                "«Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
NO_BACKUP_PASSWORD = ("El contenedor maestro de la suite aún no tiene la contraseña de cifrado de las copias de "
                      "seguridad, que el archivo de contraseñas debe incluir. Presione «Reintentar»; si vuelve a "
                      "ocurrir, contacte a APS Conecta.")
ISSUER_FAILED = ("Nextcloud no pudo verificar ni importar el certificado intermedio del ISP. Presione «Reintentar»; si "
                 "vuelve a ocurrir, contacte a APS Conecta.")
MASTERCONTAINER = "nextcloud-aio-mastercontainer"
NEXTCLOUD = "nextcloud-aio-nextcloud"
DOWN = {MASTERCONTAINER: SUITE_DOWN, NEXTCLOUD: NEXTCLOUD_DOWN}
AIO_DATA = "/mnt/docker-aio-config/data"  # in the mastercontainer: AIO's settings and its other files
AIO_CONFIG = f"{AIO_DATA}/configuration.json"  # AIO's settings, its passphrase among them
AIO_BACKUP_TIME = f"{AIO_DATA}/daily_backup_time"  # the nightly backup's time, then its switches
# AIO's key for remote backups, as its borg container sees the mastercontainer's volume (Containers/borgbackup/start.sh)
AIO_VOLUME = "/nextcloud_aio_volumes/nextcloud_aio_mastercontainer"
BORG_KEY = f"{AIO_VOLUME}/data/id_borg"
IMAGES = Path(__file__).resolve().parent.parent / "images.lock"  # the bundle's pins, AIO's borg image among them
AIO_AUTHORITY = "/mnt/docker-aio-config/caddy-internal/pki/authorities/local/root.crt"  # signs AIO's page certificate
OFFICE_SUITES = {"collabora", "onlyoffice", "eurooffice"}  # AIO's office suites, one choice among them
OLLAMA = "nextcloud-aio-ollama"  # «IA local»'s container, as community-containers/ollama names it
OLLAMA_API = "http://nextcloud-aio-ollama:11434/api/"  # Ollama's API, as Nextcloud reaches it inside the suite
OLLAMA_FAILED = ("La IA local no pudo {}. Presione «Reintentar»; si vuelve a ocurrir, contacte a APS Conecta.")
PULL_WAIT = 2 * 60 * 60  # seconds a model's pull may take
VENDORS = {"0x1002", "0x8086"}  # the PCI vendors of AMD and Intel, whose GPUs Ollama reaches through the DRI devices
# The folder whose filesystem is the system disk: Docker's storage, or /var/lib before Docker exists, as the bootstrap's
# disk check reads it.
DOCKER_STORAGE = ("/var/lib/docker", "/var/lib")
# A disk below this size is a boot, BIOS or extended partition rather than one to keep data on.
SMALLEST_DISK = 10 ** 9
BOOT = {"/boot", "/boot/efi", "/efi"}
# What lsblk lists that holds no filesystem of its own: swap, and the members its LVM volumes, software RAID, encrypted
# volumes and ZFS pools are made of, which lsblk lists in turn.
NOT_FILESYSTEMS = {"swap", "LVM2_member", "linux_raid_member", "crypto_LUKS", "zfs_member"}
# The read-only mount, replaying no journal, that reads the free space of a Linux filesystem nobody has mounted: nothing
# is written to the disk.
READ_ONLY = {"ext4": "ro,noload", "xfs": "ro,norecovery", "btrfs": "ro,nologreplay"}
MOUNTS = "/mnt/aps-conecta"  # where a disk the port mounts for good is mounted, under its filesystem's UUID
HIDDEN = "***REMOVED SENSITIVE VALUE***"  # what occ config:list shows of a value Nextcloud counts as sensitive
# How AIO's start answer ends once every container started (DockerController::getStreamingResponseHtmlEnd); when one
# fails, it stops short or Slim's error page follows.
STARTED = "</body>\n</html>"
# How that answer says AIO begins to download a container's image, after AIO's name for it, HTML-escaped
# (DockerActionManager::PullImage, written by DockerController::getAddToStreamingResponseBody; the gates hold AIO to
# this text). A progress mark, <span class='progress-indicator'></span>, follows every few seconds while the download
# moves, then «Iniciando el contenedor» once AIO created the container.
DOWNLOADING = "Descargando la imagen"
DOWNLOAD_LINE = re.compile(rf"<div>([^<]*): {DOWNLOADING}</div>")


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


# AIO's backup destination, a folder or an ssh:// repository, and time, set as its page sets them
# (ConfigurationController), from stdin's JSON. A new destination once one was set goes through AIO's own reset first,
# which also removes borg.config, so its first backup starts a repository there, as AIO's page does after «Reset backup
# location».
BACK_UP = r"""
ini_set('display_errors', 'stderr');
require '/var/www/docker-aio/php/vendor/autoload.php';
$config = \AIO\DependencyInjection::GetContainer()->get(\AIO\Data\ConfigurationManager::class);
$in = json_decode(stream_get_contents(STDIN), true, 4, JSON_THROW_ON_ERROR);
$held = $config->borgBackupHostLocation !== '' ? $config->borgBackupHostLocation : $config->borgRemoteRepo;
if ($held !== '' && $held !== $in['destination']) {
    $config->deleteBorgBackupLocationItems();
}
$remote = str_starts_with($in['destination'], 'ssh://');
$config->setBorgLocationVars($remote ? '' : $in['destination'], $remote ? $in['destination'] : '');
$config->setDailyBackupTime($in['time'], true, true);
"""


# «IA local»'s switch of our «ollama» community container (community-containers/ollama), from the JSON object on stdin:
# on, it joins AIO's community containers with AIO's NVIDIA switch as the GPU found asks and its memory cap among AIO's
# secrets, and AIO's own code recreates and starts it, as AIO's start does; off, it stops, AIO removes it, keeping its
# volume, and it leaves the list. AIO's start, every night too, then starts it with the suite only while it is listed.
SWITCH_OLLAMA = r"""
ini_set('display_errors', 'stderr');
require '/var/www/docker-aio/php/vendor/autoload.php';
$di = \AIO\DependencyInjection::GetContainer();
$config = $di->get(\AIO\Data\ConfigurationManager::class);
$docker = $di->get(\AIO\Docker\DockerActionManager::class);
$fetcher = $di->get(\AIO\ContainerDefinitionFetcher::class);
$in = json_decode(stream_get_contents(STDIN), true, 4, JSON_THROW_ON_ERROR);
$others = array_values(array_filter($config->aioCommunityContainers, fn($c) => $c !== '' && $c !== 'ollama'));
if (in_array('ollama', $config->aioCommunityContainers, true)) {
    $ollama = $fetcher->GetContainerById('nextcloud-aio-ollama');
    $docker->StopContainer($ollama);
    $docker->DeleteContainer($ollama);
}
$config->enableNvidiaGpu = $in['on'] && $in['nvidia'];
if (!$in['on']) {
    $config->aioCommunityContainers = $others;
    exit(0);
}
(function (string $memory): void {
    $this->set('secrets', array_merge($this->get('secrets', []), ['OLLAMA_MEMORY_LIMIT' => $memory]));
})->call($config, $in['memory']);
$config->aioCommunityContainers = [...$others, 'ollama'];
$ollama = $fetcher->GetContainerById('nextcloud-aio-ollama');
$docker->CreateVolumes($ollama);
$docker->PullImage($ollama, true);
$docker->CreateContainer($ollama);
$docker->StartContainer($ollama);
$docker->ConnectContainerToNetwork($ollama);
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


# In the Nextcloud container, the CA certificate on stdin, in PEM: imported into Nextcloud's trusted certificates as $1
# once it verifies against the roots the container trusts, as Nextcloud's own requests then do. An import already there
# is kept.
ISSUER = r"""
set -eu
php /var/www/html/occ security:certificates --output=json | grep -qF "\"$1\"" && exit 0
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
openssl x509 -out "$dir/$1"
openssl verify "$dir/$1"
php /var/www/html/occ security:certificates:import "$dir/$1"
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


# In the Nextcloud container, the group folder $1's missing files, from the JSON object on stdin that holds each file's
# text by its path in the group folder; whatever the folder holds stays. Groupfolders then scans the folder, so
# Nextcloud lists what was added.
SEED = r"""
set -eu
files="$(php /var/www/html/occ --no-ansi config:system:get datadirectory)/__groupfolders/$1/files"
php -r '
foreach (json_decode(stream_get_contents(STDIN), true, 3, JSON_THROW_ON_ERROR) as $path => $text) {
    $target = $argv[1] . "/" . $path;
    $folder = dirname($target);
    if (in_array("..", explode("/", $path), true) || !(is_dir($folder) || mkdir($folder, 0755, true))) {
        exit(1);
    }
    if (!file_exists($target) && file_put_contents($target, $text) === false) {
        exit(1);
    }
}' -- "$files"
php /var/www/html/occ --no-ansi groupfolders:scan "$1" > /dev/null
"""


# In the Nextcloud container, through Nextcloud's sub-admin API, as occ has no command for group admins: the account
# $argv[1] is a group admin of exactly the groups the further arguments name.
GROUP_ADMIN = r"""
require '/var/www/html/lib/base.php';
$groups = \OCP\Server::get(\OCP\IGroupManager::class);
$subAdmin = $groups->getSubAdmin();
$user = \OCP\Server::get(\OCP\IUserManager::class)->get($argv[1]);
$wanted = array_slice($argv, 2);
if ($user === null) {
    exit(1);
}
foreach ($subAdmin->getSubAdminsGroups($user) as $group) {
    if (!in_array($group->getGID(), $wanted, true)) {
        $subAdmin->deleteSubAdmin($user, $group);
    }
}
foreach ($wanted as $gid) {
    $group = $groups->get($gid);
    if ($group === null) {
        exit(1);
    }
    if (!$subAdmin->isSubAdminOfGroup($user, $group)) {
        $subAdmin->createSubAdmin($user, $group);
    }
}
"""


# In the Nextcloud container, through Nextcloud's sub-admin API: each group admin's groups, as a JSON object by its
# username.
GROUP_ADMINS = r"""
require '/var/www/html/lib/base.php';
$groups = \OCP\Server::get(\OCP\IGroupManager::class);
$admins = [];
foreach ($groups->search('') as $group) {
    foreach ($groups->getSubAdmin()->getGroupsSubAdmins($group) as $user) {
        $admins[$user->getUID()][] = $group->getGID();
    }
}
echo json_encode((object) $admins, JSON_THROW_ON_ERROR);
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
        found = DOWNLOAD_LINE.findall(text)
        for name in found[told:]:
            if (container := by_name.get(html.unescape(name))) is not None:
                downloading(container)
        told = len(found)

    return read


def host(*command, failed, timeout=120):
    """What a command on this server printed; a failure logs its errors only and is Unreachable with `failed`."""
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"{command[0]}: {error}", file=sys.stderr)
        raise Unreachable(failed) from error
    if run.returncode:
        print(f"{' '.join(command[:2])}: {run.stderr[-2000:]}", file=sys.stderr)
        raise Unreachable(failed)
    return run.stdout


def space(folder):
    """The size and free space, in bytes, of the filesystem a folder is on, as the bootstrap's disk check counts it:
    what anyone but root may still use."""
    stat = os.statvfs(folder)
    return {"size": stat.f_blocks * stat.f_frsize, "free": stat.f_bavail * stat.f_frsize}


def unmounted_free(device, filesystem):
    """The free space of a Linux filesystem nobody has mounted, through a read-only mount that replays no journal, in a
    temporary folder, unmounted at once; None for any other filesystem, or when the mount fails. The folder goes only
    once it is empty again: rmdir never reaches into a disk still mounted there."""
    if filesystem not in READ_ONLY:
        return None
    folder = tempfile.mkdtemp(prefix="aps-disk-")
    try:
        mount = subprocess.run(["mount", "-t", filesystem, "-o", READ_ONLY[filesystem], device, folder],
                               capture_output=True, text=True, timeout=60, check=False)
        if mount.returncode != 0:
            return None
        try:
            return space(folder)["free"]
        finally:
            subprocess.run(["umount", folder], capture_output=True, timeout=60, check=True)
    finally:
        os.rmdir(folder)


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
    def execute(container, command, stdin="", failed=None, timeout=120):
        """What a command run in the container as its web user printed, within `timeout` seconds. What it printed may
        be a secret and is never logged; a failure logs its errors only and is Unreachable: the container's own message
        when it does not answer, `failed` when the command fails."""
        try:
            run = subprocess.run(["docker", "exec", "--interactive", "--user", "www-data", container, *command],
                                 input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"{container} {command[0]}: {error}", file=sys.stderr)
            raise Unreachable(DOWN[container]) from error
        if run.returncode:
            print(f"{container} {command[0]}: {run.stderr}", file=sys.stderr)
            answered = "Error response from daemon" not in run.stderr and "No such container" not in run.stderr
            raise Unreachable(failed if failed and answered else DOWN[container])
        return run.stdout

    def mastercontainer(self, *command, stdin="", timeout=120):
        return self.execute(MASTERCONTAINER, command, stdin, timeout=timeout)

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

    def trust_issuer(self, name, certificate):
        """Nextcloud's own requests verify the ISP's site, which serves only its leaf certificate: the CA certificate
        that issued it, `certificate` in PEM, is among Nextcloud's trusted certificates as `name`."""
        self.execute(NEXTCLOUD, ("sh", "-c", ISSUER, "sh", name), certificate, ISSUER_FAILED)

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

    def group_admin(self, user, groups):
        """The account is a group admin of exactly these groups, none for an empty list."""
        self.execute(NEXTCLOUD, ("php", "-r", GROUP_ADMIN, "--", user, *groups), failed=GROUP_ADMIN_FAILED)

    def group_admins(self):
        """Each group admin's groups, by its username."""
        return json.loads(self.execute(NEXTCLOUD, ("php", "-r", GROUP_ADMINS), failed=GROUP_ADMIN_FAILED))

    def seed(self, folder, files):
        """The group folder holds each of the files it does not hold yet: their text by their path in it. What it holds
        stays as staff left it."""
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

    @staticmethod
    def gpu():
        """The GPU of this server that Ollama can use: "nvidia" for an NVIDIA one that Docker reaches through the
        NVIDIA Container Toolkit's runtime, "dri" for an AMD or Intel one's render device, which the bootstrap hands
        to AIO, and None for none, so the CPU."""
        if Path("/dev/nvidia0").exists():
            try:
                info = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True,
                                      text=True, timeout=30, check=False)
                if "nvidia" in json.loads(info.stdout or "{}"):
                    return "nvidia"
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        devices = Path("/sys/class/drm").glob("renderD*/device/vendor")
        return "dri" if {path.read_text(encoding="ascii").strip() for path in devices} & VENDORS else None

    @staticmethod
    def disks():
        """Each filesystem of this server's disks: whole disks, partitions, LVM volumes and software RAID, skipping loop,
        RAM and optical devices, swap, the boot partitions and anything under 1 GB; a disk or partition with no
        filesystem is listed too, unformatted. Each with its filesystem's UUID as its id (its device when it has none),
        its device, the drive it is on (the whole disk at the top of lsblk's tree, for one drive is one disk), model,
        size and free space in bytes, its filesystem, whether it is the system disk, whether it hangs off USB or is
        removable, where it is mounted and whether that mount comes back at boot. The system disk comes first. The free
        space of a Linux filesystem nobody has mounted is read through a read-only mount in a temporary folder,
        unmounted at once; None when that mount fails."""
        target = next(path for path in DOCKER_STORAGE if os.path.isdir(path))
        system = json.loads(subprocess.run(["findmnt", "--json", "--output", "SOURCE,TARGET,FSTYPE,UUID", "--target",
                                            target], capture_output=True, text=True, timeout=30, check=True).stdout)
        system = system["filesystems"][0]
        source = system["source"].split("[", 1)[0]  # a btrfs subvolume's source names it in brackets
        # The NAME column makes lsblk nest each partition, volume and array under what it is made of.
        listed = json.loads(subprocess.run(["lsblk", "--json", "--bytes", "--output",
                                            "NAME,PATH,TYPE,SIZE,FSTYPE,UUID,MODEL,TRAN,RM,MOUNTPOINT"],
                                           capture_output=True, text=True, timeout=30, check=True).stdout)
        # What /etc/fstab mounts at boot, each entry's UUID=, LABEL= or other name resolved to its device.
        fstab = subprocess.run(["findmnt", "--fstab", "--evaluate", "--json", "--output", "SOURCE,TARGET,OPTIONS"],
                               capture_output=True, text=True, timeout=30, check=False).stdout
        boot = [entry for entry in json.loads(fstab or '{"filesystems": []}')["filesystems"]
                if "noauto" not in (entry.get("options") or "").split(",")]
        found = {source: {"id": system.get("uuid") or source, "device": source, "drive": source, "model": "",
                          **space(system["target"]), "filesystem": system.get("fstype"), "system": True, "usb": False,
                          "mounted": system["target"], "at_boot": True}}

        def walk(devices, usb, model, drive):
            for device in devices:
                on_usb = usb or device.get("tran") == "usb" or device.get("rm") in (True, "1")
                named = (device.get("model") or model or "").strip()
                on = drive or device["path"]
                children = device.get("children") or []
                walk(children, on_usb, named, on)
                path, filesystem, uuid = device["path"], device.get("fstype"), device.get("uuid")
                mounted = device.get("mountpoint")
                size = int(device.get("size") or 0)
                if path == source:  # the system disk, read through Docker's storage, gets lsblk's model and transport
                    found[source].update(model=named, usb=on_usb, drive=on)
                    continue
                if (path in found or device["type"] in ("loop", "rom", "ram") or path.startswith("/dev/zram")
                        or filesystem in NOT_FILESYSTEMS or mounted in BOOT or size < SMALLEST_DISK
                        or (children and not filesystem)):
                    continue
                at_boot = any(entry["source"] == path or (mounted and entry["target"] == mounted) for entry in boot)
                free = (space(mounted)["free"] if mounted else unmounted_free(path, filesystem))
                found[path] = {"id": uuid or path, "device": path, "drive": on, "model": named, "size": size,
                               "free": free, "filesystem": filesystem, "system": False, "usb": on_usb,
                               "mounted": mounted, "at_boot": at_boot}

        walk(listed["blockdevices"], False, "", None)
        return list(found.values())

    @staticmethod
    def mount(disk):
        """The disk mounted for good under MOUNTS, at its filesystem's UUID: unless it has a boot entry already, an
        /etc/fstab entry by that UUID, marked so that a missing disk neither stops nor holds up the boot, and ordered
        before Docker, whose containers then find it mounted; and the disk mounted there now. Before the first mount
        the empty mount point is made immutable, so that while the disk is missing nothing, Docker included, can create
        folders in its place. Its mount point."""
        point = f"{MOUNTS}/{disk['id']}"
        failed = MOUNT_FAILED.format(disk["device"], point)
        try:
            os.makedirs(point, exist_ok=True)
            if not disk["at_boot"]:
                with Path("/etc/fstab").open("a", encoding="utf-8") as entries:
                    entries.write(f"UUID={disk['id']} {point} {disk['filesystem']} "
                                  "defaults,nofail,x-systemd.device-timeout=10s,x-systemd.before=docker.service 0 2\n")
                host("systemctl", "daemon-reload", failed=failed)
        except OSError as error:
            print(f"mount {point}: {error}", file=sys.stderr)
            raise Unreachable(failed) from error
        if not os.path.ismount(point):
            host("chattr", "+i", point, failed=failed)
            host("mount", point, failed=failed)
        return point

    @staticmethod
    def files_folder():
        """The folder AIO keeps the staff's files in: the mastercontainer's NEXTCLOUD_DATADIR, None for AIO's own
        volume."""
        env = host("docker", "container", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}",
                   MASTERCONTAINER, failed=SUITE_DOWN)
        return next((line.split("=", 1)[1] for line in env.splitlines() if line.startswith("NEXTCLOUD_DATADIR=")), None)

    def place_files(self, folder):
        """AIO keeps the staff's files in the folder from its next start of Nextcloud's container on, unless it already
        does: the folder made as Nextcloud's container needs it (owned by its web user and group root, mode 750),
        written where the bundle's mastercontainer start reads it, and the mastercontainer started again through that
        start, which passes it to AIO as NEXTCLOUD_DATADIR; then AIO's page answers again."""
        if self.files_folder() == folder:
            return
        try:
            os.makedirs(folder, exist_ok=True)
            os.chown(folder, WWW_DATA, 0)
            os.chmod(folder, 0o750)
            write_root_only(FILES_FOLDER, f"{folder}\n")
        except OSError as error:
            print(f"files folder {folder}: {error}", file=sys.stderr)
            raise Unreachable(FILES_FAILED.format(folder)) from error
        host("bash", str(START), "keep", failed=SUITE_DOWN, timeout=900)
        deadline = time.monotonic() + AIO_BACK
        while True:
            try:
                self._signed_in()
                return
            except Unreachable:
                if time.monotonic() > deadline:
                    raise
                time.sleep(5)

    def switch_ollama(self, settings):
        """«IA local»'s container on, with these settings, the GPU found ("gpu") and the memory cap ("memory_gib"),
        or, with None, off: it stops and AIO no longer runs it, and its volume, with the models, stays. AIO's own code
        pulls its pinned image, which can take minutes."""
        on = settings is not None
        self.mastercontainer("php", "-r", SWITCH_OLLAMA, stdin=json.dumps({
            "on": on, "nvidia": on and settings["gpu"] == "nvidia",
            "memory": str(settings["memory_gib"] * 2 ** 30) if on else ""}), timeout=30 * 60)

    def models(self):
        """The models Ollama holds, each tag's digest as its registry names it; Unreachable while it does not
        answer."""
        listed = json.loads(self.execute(NEXTCLOUD, ("curl", "-fsS", "--max-time", "20", OLLAMA_API + "tags"),
                                         failed=OLLAMA_FAILED.format("responder")))
        return {model["name"]: f"sha256:{model['digest']}" for model in listed["models"]}

    def pull(self, model):
        """Ollama pulls the model's tag from its registry; it resumes a pull cut short."""
        command = ("curl", "-fsS", "--max-time", str(PULL_WAIT), "--data-binary", "@-", OLLAMA_API + "pull")
        self.execute(NEXTCLOUD, command, json.dumps({"model": model, "stream": False}),
                     OLLAMA_FAILED.format(f"descargar el modelo {model}"), timeout=PULL_WAIT + 60)

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

    def back_up(self, destination, time):
        """AIO's own backup to the destination, a folder on this server or a remote repository, daily at the time (UTC), with its success notification and
        its automatic updates on: AIO's nightly job then restarts the containers on the pins the release holds and
        updates the store's apps (own and fork apps stay above the store's versions, and our timer runs them nightly
        anyway), so security fixes reach the suite between releases. Set through AIO's own code in its mastercontainer,
        the same calls its page makes, so a destination changed once the suite runs, when the page's login is closed,
        reaches AIO too."""
        self.mastercontainer("php", "-r", BACK_UP, stdin=json.dumps({"destination": destination, "time": time}))

    def backup_settings(self):
        """Where AIO's nightly backup goes, a folder or a remote repository, and at what time (UTC), as AIO holds
        them."""
        config = json.loads(self.mastercontainer("cat", AIO_CONFIG))
        time = self.mastercontainer("cat", AIO_BACKUP_TIME).splitlines()
        return config.get("borg_backup_host_location") or config.get("borg_remote_repo"), time[0] if time else None

    def ssh_key(self):
        """AIO's public key for remote backups, made first when AIO has none, as AIO's backup script makes it: in the
        mastercontainer's volume, by AIO's pinned borg image. The private half never leaves that volume."""
        if not self.mastercontainer("sh", "-c", f"test -f {AIO_DATA}/id_borg && echo held || true").strip():
            self.borg("ssh-keygen", "-q", "-f", BORG_KEY, "-N", "", failed=SUITE_DOWN)
        return self.mastercontainer("cat", f"{AIO_DATA}/id_borg.pub").strip()

    def reachable(self, repo):
        """The other computer takes AIO's backup at the remote repository, Borg alone, from AIO's pinned borg image with
        AIO's key and passphrase: a repository AIO's passphrase opens passes; where there is none yet, one is made as
        AIO's first backup makes it and then deleted, which proves the folder takes it; a computer that keeps it, as
        an append-only hosting account does, keeps AIO's own first repository, marked as AIO marks a remote one it
        made. Unreachable, with why, when the computer does not take it."""
        env = {"BORG_REPO": repo, "BORG_PASSPHRASE": self.secrets()["backup_password"],
               "BORG_RSH": f"ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 -i {BORG_KEY}"}
        answer = self.borg("borg", "info", env=env, failed=None)
        if answer is None:
            return
        said = answer.lower()  # SSH's own failures first: Borg asks «Is borg working on the server?» after any of them
        why = ("key" if "permission denied (publickey" in said
               else "unreachable" if any(sign in said for sign in UNREACHABLE_SIGNS)
               else "borg" if any(sign in said for sign in ("command not found", "borg: not found", "is borg working"))
               else None if "does not exist" in said or "is not a valid repository" in said
               else "folder")
        if why:
            raise remote_refusal(why, repo)
        if self.borg("borg", "init", "--encryption=repokey-blake2", env=env, failed=None) is not None:
            raise remote_refusal("folder", repo)
        kept = self.borg("borg", "delete", env={**env, "BORG_DELETE_I_KNOW_WHAT_I_AM_DOING": "YES"}, failed=None)
        if kept is not None:  # Containers/borgbackup/backupscript.sh touches it once it made a remote repository
            self.mastercontainer("touch", f"{AIO_DATA}/borg.config")

    @staticmethod
    def borg(*command, env=None, failed=None, timeout=600):
        """A command in a throwaway container of AIO's pinned borg image, with the mastercontainer's volume where AIO's
        borg container has it and `env` passed by name, so no value reaches a command line. None when it succeeds,
        what it printed when it fails and `failed` is None, Unreachable with `failed` otherwise."""
        image = next(line.strip() for line in IMAGES.read_text(encoding="utf-8").splitlines()
                     if "/aio-borgbackup@sha256:" in line)
        names = [part for key in (env or {}) for part in ("--env", key)]
        try:
            run = subprocess.run(["docker", "run", "--rm", "--volume", f"nextcloud_aio_mastercontainer:{AIO_VOLUME}",
                                  *names, "--entrypoint", command[0], image, *command[1:]],
                                 env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout,
                                 check=False)
        except (OSError, subprocess.TimeoutExpired) as error:  # the first run also pulls the image, which may be slow
            print(f"{command[0]} in AIO's borg image: {error}", file=sys.stderr)
            raise Unreachable(failed or SUITE_DOWN) from error
        if run.returncode == 0:
            return None
        if failed is None:
            return run.stdout + run.stderr
        print(f"{command[0]} in AIO's borg image: {run.stderr[-2000:]}", file=sys.stderr)
        raise Unreachable(failed)

    @staticmethod
    def writable(folder):
        """The backup destination takes a file: the folder made if missing, a test file written there and removed at
        once. A missing disk's empty mount point is immutable, so nothing is made in its place."""
        test = Path(folder) / ".aps-conecta-prueba"
        try:
            os.makedirs(folder, exist_ok=True)
            test.write_text("APS Conecta Gestión\n", encoding="utf-8")
            test.unlink()
        except OSError as error:
            print(f"backup destination {folder}: {error}", file=sys.stderr)
            raise Unreachable(UNWRITABLE.format(folder)) from error

    def start(self, downloading):
        """AIO's start button: it pulls, creates and starts every container in order, and answers once it is done. As
        its answer arrives, `downloading` gets each container whose image AIO begins to download."""
        page = self._signed_in()
        text = page.post("/api/docker/start", {}, expected=200, timeout=900,
                         each=downloads(self.containers(), downloading))
        if not text.rstrip().endswith(STARTED):
            print(f"AIO did not start the suite: {text[-2000:]}", file=sys.stderr)
            raise Unreachable(NOT_STARTED)

    def update_apps(self):
        """Nextcloud's own update of the store's apps (occ app:update --all): what it printed, naming each app it
        updated. Own and fork apps stay on the release's manifest, their releases above the store's versions."""
        return self.occ("app:update", "--all").strip()

    def stop(self):
        """AIO's stop button, pressed through its own cron script, for its page's login closes while the suite runs:
        every container stops, so the release's start can update them."""
        self.mastercontainer("php", "/var/www/docker-aio/php/src/Cron/StopContainers.php")

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
    SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAPSConectaGestionFakeKeyForRemoteBackups root@nextcloud-aio"
    PERMISSIONS = {"read": 1, "write": 6, "delete": 8, "share": 16}  # Groupfolders' words for its permission bits
    # The fake's one disk until a test adds others: the system disk, which holds Docker's storage.
    SYSTEM_DISK = {"id": "0b1e5c2a-7d3f-4c8e-9a61-2f4d8b7c9e10", "device": "/dev/vda1", "drive": "/dev/vda", "model": "",
                   "size": 60 * 10 ** 9, "free": 40 * 10 ** 9, "filesystem": "ext4", "system": True, "usb": False,
                   "mounted": "/", "at_boot": True}

    def __init__(self):
        self.in_use = set()  # DEIS codes whose name another installation holds
        # Calls in an outage: the name service's "claim" and "address", "aio" (the mastercontainer), "nextcloud"
        # (Nextcloud's container), "issuer" (the ISP's intermediate, which the roots Nextcloud's container trusts do not
        # verify) and "eurooffice" (Euro-Office, which does not answer Nextcloud).
        self.down = set()
        self.limited = False  # the name service refuses claims over its limits
        self.claims = []  # the DEIS codes the name service gave a name
        self.credential = None  # the stored claim
        self.domain = None  # the name AIO is set to
        self.published = []  # the inside addresses published
        # Each account, by its username.
        self.users = {"admin": {"name": "admin", "password": self.ADMINISTRATOR, "enabled": True}}
        self.groups = {"admin": {"name": "admin", "members": {"admin"}}}  # each group, by its id: its name and members
        self.sub_admins = {}  # each group admin's groups, by its username: Nextcloud's sub-admins
        self.group_folders = {}  # by id: each group folder's mount point, its groups' permissions and its seeded files
        self.answering = True  # whether the suite's reverse proxy answers at its name
        self.machine = {"cpus": 2, "memory_gib": 6}  # the server's hardware: install.sh's floors (Recording tests)
        self.found_gpu = None  # the GPU the server offers Ollama: "nvidia", "dri" (AMD or Intel) or None, the CPU
        self.found_disks = [dict(self.SYSTEM_DISK)]  # the server's disks, as the suite port reads them
        self.mounts = []  # each disk the port mounted for good, in turn: (its id, its mount point)
        self.files_in = None  # AIO's NEXTCLOUD_DATADIR: the folder it keeps the staff's files in; None, its own volume
        self.files_at_start = None  # where AIO kept the staff's files when its start was last pressed
        self.restarts = 0  # how often the mastercontainer was started again for a new files folder
        self.ollama = None  # the settings «IA local»'s container runs with, None while it is off
        self.volumes = set()  # the Docker volumes «IA local» made, which outlive its container
        self.registry = {}  # what Ollama's registry serves, each model's tag with its digest
        self.pulled = {}  # the models Ollama holds in its volume, each tag with its digest
        self.pulls = []  # each model Ollama was asked to pull, in turn
        self.switched = None  # the optional containers AIO was set to run; every other one is off
        self.backup = None  # AIO's nightly backup: (folder, time)
        self.unwritable = set()  # backup destinations a file cannot be written to
        self.tested = []  # each backup destination tested, in turn: a folder written to or a repository reached
        self.repository = None  # where AIO's borg.config holds the repository its backups went to, None before any
        self.ssh_key_made = False  # whether AIO's key for remote backups was made
        self.remote_answer = None  # why the other computer refuses AIO's backup, a key of REMOTE, or None: it takes it
        self.automatic_updates = None  # whether AIO's nightly update job is on, as the backups step sets it
        self.backup_password = self.BACKUP_PASSWORD  # its encryption password, which AIO made, or None
        self.starts = 0  # how often AIO's start button was pressed
        self.stops = 0  # how often the suite's containers were stopped, as the upgrade stops them
        self.app_updates = 0  # how often the store's apps were updated, as the nightly timer does it
        self.states = {}  # what AIO reports per container id: (AIO's name, state: stopped, starting or running)
        self.release = threading.Event()  # AIO's start answers once it is set: tests clear it to watch a start
        self.release.set()
        self.dies = False  # the Provisionador dies while AIO's start runs; AIO goes on
        self.system = {}  # Nextcloud's system settings
        # Each installed app's settings, as Nextcloud ships them: Nextcloud's own voice and its Dashboard on.
        self.apps = {"core": {}, "admin_audit": {}, "theming": {}, "office": {}, "eurooffice": {},
                     **{app: {"enabled": "yes"} for app in ("nextcloud_announcements", "firstrunwizard",
                                                            "survey_client", "support", "dashboard")}}
        # The apps AIO's start installs and sets up with the components' switches: the antivirus, which only logs what
        # its background scan finds infected, and full-text search, with the store's files provider.
        self.apps |= {"files_antivirus": {"enabled": "yes", "av_infected_action": "only_log"},
                      **{app: {"enabled": "yes"} for app in ("fulltextsearch", "fulltextsearch_elasticsearch",
                                                              "files_fulltextsearch")}}
        self.outdated = set()  # apps whose code changed while enabled: Nextcloud waits for their upgrade
        self.indexed = False  # whether full-text search's index was built
        self.commands = []  # every occ command, by its arguments
        self.imports = []  # the settings each config:import brought
        self.issuers = {}  # the CA certificates Nextcloud trusts for the ISP's leaf: their PEM text, by their name
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

    def gpu(self):
        return self.found_gpu

    def disks(self):
        return [dict(disk) for disk in self.found_disks]

    def mount(self, disk):
        point = f"{MOUNTS}/{disk['id']}"
        found = next(found for found in self.found_disks if found["id"] == disk["id"])
        found.update(mounted=point, at_boot=True)
        self.mounts.append((disk["id"], point))
        return point

    def files_folder(self):
        return self.files_in

    def place_files(self, folder):
        if self.files_in != folder:
            self.files_in = folder
            self.restarts += 1

    def switch_ollama(self, settings):
        """«IA local»'s container, as AIO creates and starts it with the settings, or stops and removes it, its volume
        kept."""
        self._outage_guard()
        self.ollama = settings
        if settings is None:
            self.states.pop(OLLAMA, None)
        else:
            self.volumes.add("nextcloud_aio_ollama")
            self.states[OLLAMA] = ("IA local (Ollama)", "running")

    def _ollama_answers(self):
        self._outage_guard("nextcloud")
        if self.states.get(OLLAMA, (None, None))[1] != "running":
            raise Unreachable(OLLAMA_FAILED.format("responder"))

    def models(self):
        self._ollama_answers()
        return dict(self.pulled)

    def pull(self, model):
        self._ollama_answers()
        self.pulls.append(model)
        self.pulled[model] = self.registry[model]

    def switch(self, components):
        self._signed_in()
        self.switched = set(components)

    def back_up(self, destination, time):
        self._outage_guard()
        if self.backup and self.backup[0] != destination:  # AIO's reset: its next backup starts a repository afresh
            self.repository = None
        self.backup, self.automatic_updates = (destination, time), True

    def backup_settings(self):
        self._outage_guard()
        return self.backup or (None, None)

    def writable(self, folder):
        if folder in self.unwritable:
            raise Unreachable(UNWRITABLE.format(folder))
        self.tested.append(folder)

    def ssh_key(self):
        self._outage_guard()
        self.ssh_key_made = True
        return self.SSH_KEY

    def reachable(self, repo):
        self._outage_guard()
        if self.remote_answer:
            raise remote_refusal(self.remote_answer, repo)
        self.tested.append(repo)

    def start(self, downloading):
        """AIO's start answers once every container it started runs. Its answer streams as AIO's does: each container
        that does not run, in turn, has its image downloaded, with a progress mark, and is started."""
        self._signed_in()
        listed = self.containers()
        read, text = downloads(listed, downloading), ""
        for c in listed:
            if c["state"] == "stopped":
                name = html.escape(c["name"])
                text += f"<div>{name}: {DOWNLOADING}</div><span class='progress-indicator'></span>"
                read(text)
                text += f"<div>{name}: Iniciando el contenedor</div>"
                read(text)
        self.starts += 1
        self.files_at_start = self.files_in
        self.release.wait()
        if self.dies:
            raise SystemExit  # the thread ends without a word, as the process would
        self.states = {id_: (name, "running") for id_, (name, _) in self.states.items()}

    def stop(self):
        """The suite's containers stop, as the upgrade's cron script stops them; AIO's page lets its forms be posted
        again once they do not run."""
        self._outage_guard()
        self.stops += 1
        self.states = {id_: (name, "stopped") for id_, (name, _) in self.states.items()}

    def update_apps(self):
        """Nextcloud's update of the store's apps, as occ runs it."""
        self._outage_guard("nextcloud")
        self.app_updates += 1
        return ""

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

    def trust_issuer(self, name, certificate):
        self._outage_guard("nextcloud")
        if "issuer" in self.down:
            raise Unreachable(ISSUER_FAILED)
        self.issuers.setdefault(name, certificate)

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
        elif (command, args) == ("intravox:reindex", ()):  # as Nextcloud's admin, whom the admin group's grant lets read
            mount = self.apps[app].get("groupfolder_name", "IntraVox")
            if not self.group_folders.get(mounts.get(mount), {}).get("groups", {}).get("admin", 0) & 1:
                raise failed
        else:
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

    def group_admin(self, user, groups):
        self._outage_guard("nextcloud")
        if user not in self.users or not set(groups) <= self.groups.keys():
            raise Unreachable(GROUP_ADMIN_FAILED)
        self.sub_admins.pop(user, None)
        if groups:
            self.sub_admins[user] = set(groups)

    def group_admins(self):
        self._outage_guard("nextcloud")
        return {user: sorted(groups) for user, groups in self.sub_admins.items()}

    def seed(self, folder, files):
        self._outage_guard("nextcloud")
        held = self.group_folders[folder]["files"]
        for path, text in files.items():
            held.setdefault(path, text)
