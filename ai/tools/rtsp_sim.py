"""Serve the benchmark footage over real RTSP, so the pipeline can be measured
against a live stream instead of a file on disk.

Every number the project has recorded so far was read from disk with
pace_realtime=False -- the decoder consumed frames as fast as the CPU allowed.
A camera cannot do that. It emits frames at wall-clock rate, over a protocol
with its own handshake, packetisation and failure modes. This tool closes that
gap without needing a camera: MediaMTX is the RTSP server, and one ffmpeg per
camera republishes the same footage the benchmarks already use.

What this reproduces faithfully:

  * the RTSP handshake, transport negotiation and RTP packetisation
  * the exact H.264/HEVC bitstream, including GOP and keyframe spacing --
    ffmpeg runs with `-c copy`, so nothing is re-encoded and the bytes on the
    wire are the ones the original camera produced (this footage is real CCTV)
  * wall-clock pacing, via `-re`; the source cannot outrun realtime
  * a stream that is always up: publishers start with the server and restart
    on failure, so a client that drops and reconnects rejoins mid-stream the
    way it would with a camera, rather than restarting the file
  * credentials on read, because production cameras require them and code
    that has only ever opened an unauthenticated URL has not been tested

What it does NOT reproduce, and must not be read as evidence about:

  * network jitter, packet loss and latency -- this is loopback, so ~0
  * bandwidth limits
  * vendor quirks: Dahua/Hikvision RTSP dialects, clock drift, VBR under
    scene change, cameras dropping frames when overloaded
  * NVR limits on how many concurrent RTSP clients it will serve

So this answers "does RTSP cost more than reading a file", and says nothing
about "will the WAN hold up". Those are separate measurements on purpose --
running them together would leave no way to attribute a regression to either.

Path naming is one deliberate deviation: paths here are the camera_id, not
Dahua's `/cam/realmonitor?channel=N&subtype=0`. MediaMTX routes on the path
and ignores the query, so a faithful Dahua URL cannot select between 16
streams. The client code still has to handle credentials and query strings,
which is what actually needs testing; the URL cosmetics do not.

Usage -- one command, so the server and the manifest cannot disagree about
which cameras exist:

    # terminal 1: publish 16 cameras and write the matching manifest
    python ai/tools/rtsp_sim.py --start --cameras 16 \
        --write-manifest outputs/rtsp_sim/store_rtsp_16cam.json

    # terminal 2: confirm every stream is up before spending a benchmark on it
    python ai/tools/rtsp_sim.py --check --cameras 16

    # terminal 2: the benchmark takes the RTSP manifest like any other
    python ai/tools/diag_split_tiers.py --layer all \
        --manifest outputs/rtsp_sim/store_rtsp_16cam.json \
        --cameras 16 --duration 180 \
        --decoders 8 --inferers 2 --infer-threads 8 --business 4

There is no --rtsp flag on the benchmark tool, on purpose: the source kind
belongs to the manifest, so the same command measures files and cameras and
nothing in the measurement path knows which one it is looking at.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from ai.src.common.paths import ai_root, outputs_dir
from pathlib import Path
from ai.src.common.logging_setup import configure_logging

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ai_root() / "config/stores/local_dev_store_16cams.json"
DEFAULT_CONFIG = outputs_dir() / "rtsp_sim/mediamtx.yml"


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        sys.exit(f"{binary} not found on PATH; install it before running this tool")
    return path


def load_cameras(manifest: Path) -> list[tuple[str, object]]:
    """(camera_id, source) for every camera, in manifest order.

    source is a Path for an ordinary camera, or a list of (Path, repeat) for a
    camera whose manifest plays a stream.playlist.

    Goes through camera_specs_from_manifest rather than reading the JSON
    directly so ${REPO_ROOT} and the per-camera/manifest-default fallbacks
    resolve exactly the way the pipeline resolves them. A simulator fed by a
    second, slightly different parser would be measuring a different manifest.
    """
    sys.path.insert(0, str(ROOT))
    from ai.src.infra.local_config import camera_specs_from_manifest

    cameras = []
    for spec in camera_specs_from_manifest(str(manifest)):
        videos = ([(Path(e["path"]), int(e["repeat"])) for e in spec.playlist]
                  if spec.playlist else [(Path(spec.source_uri), 1)])
        for video, _repeat in videos:
            if not video.exists():
                sys.exit(f"camera={spec.camera_id}: video not found: {video}")
        cameras.append((spec.camera_id, videos if spec.playlist else videos[0][0]))
    return cameras


def _probe(video: Path) -> tuple[int, int, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "json", str(video)],
        capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    return int(st["width"]), int(st["height"]), st["r_frame_rate"]


def prepare_playlist(camera_id: str, playlist: list[tuple[Path, int]], work_dir: Path) -> Path:
    """Normalise a playlist's files into one continuous, stream-copyable cycle.

    The files of one camera can differ in codec, resolution and frame rate (the
    counter mixes a 1920x1080@15 HEVC recording with a 960x544@24 H.264 clip).
    A stream that changed any of those midway would break both the RTSP reader
    and the recorder, so every file is re-encoded once with identical settings
    -- the first file's resolution and frame rate, H.264, fixed GOP -- and the
    cycle is a concat list the publisher replays with -c copy.
    """
    out_dir = work_dir / camera_id
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height, rate = _probe(playlist[0][0])
    lines = []
    for idx, (video, repeat) in enumerate(playlist):
        norm = out_dir / f"part{idx}_{video.stem}_{width}x{height}.mp4"
        if not norm.exists():
            # Encoded under a temporary name and renamed when complete: a run
            # killed midway must not leave a truncated file that the next run
            # would take for a finished one.
            partial = norm.with_name(norm.stem + f".partial-{os.getpid()}.mp4")
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                 "-an", "-vf", f"scale={width}:{height},fps={rate}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-profile:v", "high", "-bf", "0",
                 "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
                 "-video_track_timescale", "90000", str(partial)], check=True)
            os.replace(partial, norm)
        lines += [f"file '{norm}'"] * repeat
    concat = out_dir / "cycle.txt"
    concat.write_text("\n".join(lines) + "\n")
    return concat


def build_config(cameras, *, port: int, user: str, password: str,
                 record_dir: Path | None = None, playback_port: int = 9996,
                 work_dir: Path | None = None) -> str:
    lines = [
        "# Generated by ai/tools/rtsp_sim.py -- do not edit by hand.",
        "logLevel: info",
        "readTimeout: 30s",
        "writeTimeout: 30s",
        "",
        "# TCP first: that is what a deployment over a WAN would force, since",
        "# UDP loses packets silently. Both are offered so the transport can",
        "# be varied as its own experiment.",
        "rtspTransports: [tcp, udp]",
        f"rtspAddress: :{port}",
        "",
        "# Publishing is loopback-only and anonymous -- the ffmpeg publishers",
        "# below are spawned by this same process. Reading requires a password",
        "# because real cameras do, and an RTSP client that has only ever",
        "# opened an unauthenticated URL has an untested credential path.",
        "authInternalUsers:",
        "  - user: any",
        "    ips: ['127.0.0.1/32', '::1/128']",
        "    permissions:",
        "      - action: publish",
        "      - action: playback",
        f"  - user: {user}",
        f"    pass: {password}",
        "    permissions:",
        "      - action: read",
        "",
    ]
    if record_dir is not None:
        # Record every stream unchanged so evidence clips can be cut at native
        # fps (ai/src/infra/recorded_clips.py). Short segments and a short
        # retention: a clip needs 30 s of history, and the box has 24 GB.
        lines += [
            "playback: yes",
            f"playbackAddress: 127.0.0.1:{playback_port}",
            "",
            "pathDefaults:",
            "  record: yes",
            f"  recordPath: {record_dir}/%path/%Y-%m-%d_%H-%M-%S-%f",
            "  recordFormat: fmp4",
            "  recordPartDuration: 1s",
            "  recordSegmentDuration: 60s",
            "  recordDeleteAfter: 10m",
            "",
        ]
    lines += ["paths:"]

    for camera_id, video in cameras:
        if record_dir is not None and not isinstance(video, list):
            # Recording forces H.264 on every stream. MediaMTX's recorder cannot
            # parse these cameras' HEVC: measured on this footage it logs
            # "[recorder] invalid DeltaPocS0" and restarts forever, so five of
            # seven streams had no recording and no clip could be cut. The
            # normalised copy plays exactly like the original, one file looped.
            video = [(video, 1)]
        if isinstance(video, list):
            # A playlist camera: one continuous stream over the normalised cycle.
            concat = prepare_playlist(camera_id, video, work_dir or outputs_dir() / "rtsp_sim/playlists")
            cmd = (
                "ffmpeg -hide_banner -loglevel warning "
                "-re -stream_loop -1 -fflags +genpts "
                f"-f concat -safe 0 -i {concat} "
                "-c copy -f rtsp -rtsp_transport tcp "
                "rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
            )
            lines += [f"  {camera_id}:", f"    runOnInit: {cmd}", "    runOnInitRestart: yes"]
            continue
        # -re paces to wall clock; without it ffmpeg pushes the whole file in
        #   seconds and the "live stream" is a burst.
        # -stream_loop -1 keeps the camera up forever.
        # -c copy is what makes this faithful: no re-encode, so GOP structure,
        #   keyframe spacing and bitrate stay exactly as the camera recorded.
        # -fflags +genpts because looping replays the same timestamps, and a
        #   client that sees time jump backwards will drop the whole segment.
        cmd = (
            "ffmpeg -hide_banner -loglevel warning "
            "-re -stream_loop -1 -fflags +genpts "
            f"-i {video} "
            "-c copy -f rtsp -rtsp_transport tcp "
            "rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
        )
        lines += [
            f"  {camera_id}:",
            f"    runOnInit: {cmd}",
            "    runOnInitRestart: yes",
        ]

    return "\n".join(lines) + "\n"


def stream_url(camera_id: str, *, host: str, port: int, user: str, password: str) -> str:
    return f"rtsp://{user}:{password}@{host}:{port}/{camera_id}"


def write_rtsp_manifest(src_manifest: Path, dest: Path, cameras, *,
                        host: str, port: int, user: str, password: str,
                        forever: bool = False, playback_url: str | None = None) -> None:
    """Clone a manifest with every camera repointed at the simulator.

    A copy rather than an edit, so the same run can be measured against files
    and against RTSP with nothing else differing -- same cameras, same
    target_fps, same pipelines, same zones. Anything else and a difference in
    the numbers could not be attributed to the transport.

    Per-camera `simulation` blocks are dropped: they describe seeking and
    looping a recording, which RtspStreamSource rejects outright rather than
    silently ignoring. target_duration_s is kept, since it is what bounds a
    benchmark run.

    forever=True writes strategy=forever instead, for a deployment-shaped run.
    playback_url adds stream.recording so evidence clips are cut from the
    simulator's recording. stream.playlist is dropped: the simulator plays it.
    """
    data = json.loads(src_manifest.read_text())
    wanted = {camera_id for camera_id, _ in cameras}
    kept = []
    for camera in data["cameras"]:
        if camera["camera_id"] not in wanted:
            continue
        camera = json.loads(json.dumps(camera))  # deep copy
        stream = camera.setdefault("stream", {})
        simulation = dict(stream.get("simulation", {}))
        stream["input_mode"] = "rtsp"
        stream["uri"] = stream_url(camera["camera_id"], host=host, port=port,
                                   user=user, password=password)
        stream.pop("simulation", None)
        stream.pop("path", None)
        stream.pop("kind", None)
        stream.pop("playlist", None)
        stream["rtsp"] = {"transport": "tcp"}
        if playback_url:
            stream["recording"] = {"playback_url": playback_url, "path": camera["camera_id"]}
        if forever:
            stream["strategy"] = "forever"
        elif "target_duration_s" in simulation:
            camera.setdefault("runtime", {})
            data.setdefault("runtime", {})["target_duration_s"] = \
                simulation["target_duration_s"]
        kept.append(camera)
    data["cameras"] = kept
    data["store_id"] = data.get("store_id", "rtsp_sim")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2) + "\n")


def check_streams(cameras, *, host, port, user, password, timeout: float) -> int:
    """Probe each stream and report resolution/codec/fps actually received."""
    _require("ffprobe")
    bad = 0
    for camera_id, _ in cameras:
        url = stream_url(camera_id, host=host, port=port, user=user, password=password)
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-rtsp_transport", "tcp",
             "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
             "-of", "json", url],
            capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            print(f"  FAIL {camera_id}: {proc.stderr.strip().splitlines()[-1:] or ['no output']}")
            bad += 1
            continue
        try:
            s = json.loads(proc.stdout)["streams"][0]
        except (KeyError, IndexError, json.JSONDecodeError):
            print(f"  FAIL {camera_id}: no video stream in probe output")
            bad += 1
            continue
        print(f"  ok   {camera_id}: {s['codec_name']} {s['width']}x{s['height']} "
              f"@ {s['avg_frame_rate']}")
    return bad


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--host", default="127.0.0.1",
                   help="host to build URLs against; the server always binds all "
                        "interfaces, this only affects what --print-urls emits")
    p.add_argument("--port", type=int, default=8554)
    p.add_argument("--user", default="admin")
    p.add_argument("--password", default="cam12345")
    p.add_argument("--cameras", type=int, default=None,
                   help="publish only the first N cameras (default: all)")
    p.add_argument("--write", action="store_true", help="generate the config and exit")
    p.add_argument("--start", action="store_true", help="generate the config, then serve")
    p.add_argument("--check", action="store_true", help="probe every stream of a running server")
    p.add_argument("--print-urls", action="store_true", help="print stream URLs")
    p.add_argument("--write-manifest", default=None,
                   help="write a copy of the manifest repointed at the simulator, "
                        "so a file run and an RTSP run differ only in transport")
    p.add_argument("--probe-timeout", type=float, default=20.0)
    p.add_argument("--forever", action="store_true",
                   help="with --write-manifest: strategy=forever on every camera")
    p.add_argument("--record", action="store_true",
                   help="record every stream and serve playback, so clips are cut at native fps")
    p.add_argument("--record-dir", default=str(outputs_dir() / "rtsp_sim/recordings"))
    p.add_argument("--playback-port", type=int, default=9996)
    args = p.parse_args()

    cameras = load_cameras(Path(args.manifest))
    if args.cameras is not None:
        cameras = cameras[:args.cameras]

    if args.print_urls:
        for camera_id, _ in cameras:
            print(stream_url(camera_id, host=args.host, port=args.port,
                             user=args.user, password=args.password))
        return

    if args.check:
        print(f"probing {len(cameras)} streams on {args.host}:{args.port}")
        bad = check_streams(cameras, host=args.host, port=args.port, user=args.user,
                            password=args.password, timeout=args.probe_timeout)
        print(f"\n{len(cameras) - bad}/{len(cameras)} streams OK")
        sys.exit(1 if bad else 0)

    if not (args.write or args.start or args.write_manifest):
        p.error("pass one of --write, --start, --write-manifest, --check or --print-urls")

    # Deliberately composable with --write/--start rather than an early return:
    # generating the server config and the manifest in separate invocations
    # lets --cameras differ between them, and then the benchmark points at 16
    # cameras while only 4 are being published. Twelve StreamOpenErrors is a
    # confusing way to discover a typo. One command cannot disagree with itself.
    if args.write_manifest:
        dest = Path(args.write_manifest)
        write_rtsp_manifest(Path(args.manifest), dest, cameras, host=args.host,
                            port=args.port, user=args.user, password=args.password,
                            forever=args.forever,
                            playback_url=(f"http://127.0.0.1:{args.playback_port}"
                                          if args.record else None))
        print(f"wrote {dest} ({len(cameras)} cameras)", file=sys.stderr)

    if args.write or args.start:
        _require("ffmpeg")
        config = Path(args.config)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(build_config(
            cameras, port=args.port, user=args.user, password=args.password,
            record_dir=Path(args.record_dir) if args.record else None,
            playback_port=args.playback_port))
        print(f"wrote {config} ({len(cameras)} cameras)", file=sys.stderr)

    if args.start:
        mediamtx = _require("mediamtx")
        print(f"serving on rtsp://{args.host}:{args.port}/<camera_id> -- ctrl-c to stop",
              file=sys.stderr)
        try:
            subprocess.run([mediamtx, str(config)])
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
