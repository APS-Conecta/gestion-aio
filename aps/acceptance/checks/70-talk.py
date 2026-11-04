#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Talk at the suite's name, as staff on the clinic network use it: two staff members, each in a browser that trusts
no certificate of the suite's own (only the staging roots, as browsers trust Let's Encrypt's production ones) and has
a fake camera and a microphone that plays a tone, join one call from Talk's page, and each browser decodes the other's
audio. Talk's signaling runs at wss://<name>/standalone-signaling/, and the TURN servers Talk hands the browsers are at
<name>:443, where the community Caddy carries Talk's port and a browser gets a relay. The Talk app is the official one:
the app store's signature verifies each of its files. Talk recording is on exactly when the machine passes the
recording threshold, and Talk then holds AIO's recording server."""

import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "aps" / "acceptance"), str(REPO / "aps" / "provisionador")]
from browser import STAGING_ROOTS, chromium, hardware, machine  # noqa: E402
from playwright.sync_api import Error, expect  # noqa: E402
from steps.components import RECORDING, chosen  # noqa: E402

STAFF = ("llamada.uno", "llamada.dos")
TURN_PORT = 443  # Talk's port, as the community Caddy sets it (community-containers/caddy/caddy.json: talk_port)
NEXTCLOUD = "sudo docker exec --interactive --user www-data nextcloud-aio-nextcloud"
# Before Talk loads: every peer connection Talk opens, with the ICE servers it got.
OBSERVE = """window.talkPeers = [];
window.RTCPeerConnection = new Proxy(window.RTCPeerConnection, {
  construct(target, args, newTarget) {
    const peer = Reflect.construct(target, args, newTarget);
    window.talkPeers.push({peer, servers: (args[0] || {}).iceServers || []});
    return peer;
  }
});"""
# Each open peer connection's audio RTP, sent and received (with the energy and duration of the audio decoded), the
# video frames decoded and the candidate pair in use.
STATS = """async () => {
  const audio = {}, video = {}, pairs = [];
  for (const [n, {peer}] of window.talkPeers.entries()) {
    if (peer.connectionState === 'closed') continue;
    const report = await peer.getStats();
    for (const s of report.values()) {
      if (s.type === 'inbound-rtp' && s.kind === 'video') video[n + ':' + s.id] = s.framesDecoded;
      if (['inbound-rtp', 'outbound-rtp'].includes(s.type) && s.kind === 'audio') {
        audio[n + ':' + s.id] = {type: s.type, bytes: s.bytesReceived ?? s.bytesSent,
          packets: s.packetsReceived ?? s.packetsSent, energy: s.totalAudioEnergy, duration: s.totalSamplesDuration};
      }
      if (s.type === 'transport' && s.selectedCandidatePairId) {
        const pair = report.get(s.selectedCandidatePairId);
        pairs.push({state: pair.state, local: report.get(pair.localCandidateId)?.candidateType,
          remote: report.get(pair.remoteCandidateId)?.candidateType});
      }
    }
  }
  return {audio, video, pairs};
}"""
# The TURN servers Talk handed the browser, and how many relay candidates a browser gets through them alone.
RELAY = """async () => {
  const servers = window.talkPeers.flatMap(p => p.servers)
    .filter(s => [].concat(s.urls).some(u => u.startsWith('turn')));
  const peer = new RTCPeerConnection({iceServers: servers, iceTransportPolicy: 'relay'});
  const relays = [];
  peer.onicecandidate = event => event.candidate?.candidate.includes(' typ relay') && relays.push(event.candidate);
  peer.createDataChannel('turn');
  await peer.setLocalDescription(await peer.createOffer());
  await new Promise(done => {
    setTimeout(done, 20000);
    peer.onicegatheringstatechange = () => peer.iceGatheringState === 'complete' && done();
  });
  peer.close();
  const urls = servers.flatMap(s => [].concat(s.urls)).filter(u => u.startsWith('turn'));
  return {urls: [...new Set(urls)], relays: relays.length};
}"""


def occ(command):
    return machine(f"{NEXTCLOUD} php occ {command}")


def decoded(before, after):
    """Whether, between two readings, the browser sent audio and received audio that it decoded, not silence, over a
    connected candidate pair."""
    flowing = set()
    for key, now in after["audio"].items():
        then = before["audio"].get(key)
        if not then or now["bytes"] <= then["bytes"] or now["packets"] <= then["packets"]:
            continue
        heard = (now["energy"] or 0) > (then["energy"] or 0) and (now["duration"] or 0) > (then["duration"] or 0)
        if now["type"] == "inbound-rtp" and not heard:
            continue
        flowing.add(now["type"])
    return flowing == {"inbound-rtp", "outbound-rtp"} and any(p["state"] == "succeeded" for p in after["pairs"])


def join(page):
    """Join the call from the conversation's own button, through Talk's device check, with camera and microphone on.
    Talk tells a headless browser it is not fully supported, in a notice over the button: it is closed first, as a
    person would close it."""
    call = page.locator(".join-call:visible").first  # «Comenzar llamada» or «Unirse a la llamada»
    expect(call).to_be_visible(timeout=60_000)
    for notice in page.locator(".toastify .toast-close").all():
        notice.click()
    call.click()
    devices, joined = page.locator(".media-settings"), page.locator(".leave-call:visible").first
    expect(devices.or_(joined).first).to_be_visible(timeout=60_000)
    if devices.is_visible():
        for off, on in (("Activar audio", "Silenciar audio"), ("Activar vídeo", "Desactivar video")):
            toggle = devices.get_by_role("button", name=re.compile(f"^({off}|{on})$"))
            expect(toggle).to_be_visible(timeout=30_000)
            if toggle.get_attribute("aria-label") == off:
                toggle.click()
            expect(devices.get_by_role("button", name=on, exact=True)).to_be_visible()
        devices.locator(".join-call").click()
    expect(joined).to_be_visible(timeout=60_000)


config = json.loads(machine("sudo docker exec nextcloud-aio-mastercontainer cat "
                            "/mnt/docker-aio-config/data/configuration.json"))
name = config.get("domain")
if not name:
    sys.exit("the suite has no name: «Dirección» claimed none")
suite = f"https://{name}"
evidence = Path(os.environ["EVIDENCE"])

server = hardware()
recording = config.get("isTalkRecordingEnabled", False)
print(f"the machine has {server['cpus']} cores and {server['memory_gib']} GiB; AIO's Talk recording: {recording}")
if recording != (RECORDING in chosen(server)):
    sys.exit("Talk recording is not on exactly when the machine passes the recording threshold")
if recording and "nextcloud-aio-talk-recording:1234" not in occ("config:app:get spreed recording_servers"):
    sys.exit("AIO runs Talk's recording server, but Talk does not hold it")

version = json.loads(occ("app:list --output=json"))["enabled"].get("spreed")
# Nextcloud prints nothing for an app whose files all match its signature, and otherwise each file that differs or
# that the app has no signature to check
integrity = occ("integrity:check-app spreed || true").strip()
if not version or integrity:
    sys.exit(f"Talk ({version or 'not enabled'}) is not the app store's official, unmodified app: {integrity}")
print(f"Talk {version}: the app store's signature verifies each of its files")

password = os.environ["TEST_PASSWORD"]  # both staff members'; run.sh masked it
for user in STAFF:  # the password arrives on stdin, never on a command line
    machine(f"{NEXTCLOUD} sh -c 'read -r OC_PASS; export OC_PASS; exec php occ user:add --password-from-env {user}'",
            password + "\n")
    occ(f"user:setting {user} firstrunwizard show 999.0.0")  # no first-run wizard over Talk's page
basic = base64.b64encode(f"{STAFF[0]}:{password}".encode()).decode()
request = urllib.request.Request(f"{suite}/ocs/v2.php/apps/spreed/api/v4/room", method="POST",
                                 data=json.dumps({"roomType": 1, "invite": STAFF[1]}).encode(),
                                 headers={"Authorization": f"Basic {basic}", "OCS-APIRequest": "true",
                                          "Accept": "application/json", "Content-Type": "application/json"})
with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=STAGING_ROOTS), timeout=30) as answer:
    token = json.load(answer)["ocs"]["data"]["token"]

sockets, pages = [], []
with chromium(media=True) as browser:
    for user in STAFF:  # each signs in, then each opens the conversation and joins its call
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(OBSERVE)
        page = context.new_page()
        page.on("websocket", lambda socket: sockets.append(socket.url))
        page.goto(f"{suite}/login")
        page.fill("#user", user)
        page.fill("#password", password)
        page.click("[data-login-form-submit]")
        page.wait_for_url(lambda url: "/login" not in url, timeout=60_000)
        pages.append(page)
    for user, page in zip(STAFF, pages):  # the invited one opens the conversation once the first one's call rings
        page.goto(f"{suite}/call/{token}")
        try:
            join(page)
        except (AssertionError, Error):
            page.screenshot(path=evidence / f"{user}-not-joined.png")
            sys.exit(f"{user} did not join the call from Talk's page")

    deadline = time.monotonic() + 120
    while True:
        before = [page.evaluate(STATS) for page in pages]
        pages[0].wait_for_timeout(3000)
        after = [page.evaluate(STATS) for page in pages]
        (evidence / "talk-media.json").write_text(json.dumps({"before": before, "after": after}, indent=1))
        flowing = all(decoded(*readings) for readings in zip(before, after))
        if flowing or time.monotonic() > deadline:
            break
    for user, page, reading in zip(STAFF, pages, after):
        page.screenshot(path=evidence / f"{user}.png")
        print(f"{user}: candidate pairs {reading['pairs']}, video frames decoded {list(reading['video'].values())}")
    if not flowing:
        sys.exit("within two minutes the two browsers did not each send audio and decode the other's")
    print("both browsers send audio and decode the other's, not silence")
    turn = pages[0].evaluate(RELAY)

signaling = f"wss://{name}/standalone-signaling/"
print(f"signaling: {sorted({s for s in sockets if s.startswith(signaling)})}; TURN: {turn}")
if not any(s.startswith(signaling) for s in sockets):
    sys.exit(f"the browsers did not reach Talk's signaling at {signaling}")
if not turn["urls"] or any(not re.match(rf"turns?:{re.escape(name)}:{TURN_PORT}(\?|$)", url) for url in turn["urls"]):
    sys.exit(f"Talk handed the browsers TURN servers other than {name}:{TURN_PORT}: {turn['urls']}")
if not turn["relays"]:
    sys.exit(f"a browser got no relay from Talk's TURN server at {name}:{TURN_PORT}")
print(f"Talk's signaling and TURN work at {name}, with no certificate of the suite's own in the browsers")
