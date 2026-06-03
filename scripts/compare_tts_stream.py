from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path


DEFAULT_URL = "http://127.0.0.1:8012/v1/tts/stream"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_TEXT = (
    "\u3053\u308c\u306f\u30b9\u30c8\u30ea\u30fc\u30df\u30f3\u30b0"
    "\u51fa\u529b\u3068\u901a\u5e38\u51fa\u529b\u306e\u4f53\u611f\u5dee"
    "\u3092\u6bd4\u3079\u308b\u305f\u3081\u306e\u30c6\u30b9\u30c8\u3067\u3059\u3002"
    "\u6700\u521d\u306e\u97f3\u58f0\u304c\u3044\u3064\u5c4a\u304f\u304b\u3001"
    "\u5168\u4f53\u304c\u7d42\u308f\u308b\u307e\u3067\u4f55\u79d2"
    "\u304b\u304b\u308b\u304b\u3092\u78ba\u8a8d\u3057\u307e\u3059\u3002"
)


@dataclass
class ChunkEvent:
    index: int
    at_seconds: float
    bytes_received: int
    cumulative_bytes: int
    cumulative_audio_seconds: float


@dataclass
class TtsResult:
    mode: str
    sample_rate: int
    headers_seconds: float
    first_chunk_seconds: float | None
    elapsed_seconds: float
    chunk_count: int
    byte_count: int
    audio_seconds: float
    pcm_path: Path
    wav_path: Path
    timeline_path: Path
    events: list[ChunkEvent]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare CosyVoice2 stream=true and stream=false request behavior "
            "by measuring first audio byte and total completion time."
        )
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="CosyVoice engine endpoint.")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text to synthesize.")
    parser.add_argument("--speed", type=float, default=1.0, help="TTS speed.")
    parser.add_argument(
        "--text-frontend",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable CosyVoice text frontend. Default is false for Japanese text.",
    )
    parser.add_argument("--prompt-text", default="", help="Optional promptText override.")
    parser.add_argument("--prompt-wav", default="", help="Optional promptWav override.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("backend") / "runtime" / "tts_compare",
        help="Directory for PCM, WAV, and timeline CSV outputs.",
    )
    parser.add_argument(
        "--order",
        default="stream,nonstream",
        help="Run order: stream,nonstream or nonstream,stream.",
    )
    parser.add_argument("--chunk-size", type=int, default=4096, help="Client read size in bytes.")
    parser.add_argument("--timeout", type=float, default=600.0, help="HTTP timeout seconds.")
    parser.add_argument(
        "--log-chunks",
        type=int,
        default=8,
        help="Print the first N chunk arrivals for each mode. Use 0 to hide chunks.",
    )
    parser.add_argument("--play", action="store_true", help="Play saved WAV files after each run on Windows.")
    return parser.parse_args()


def request_mode(args: argparse.Namespace, *, stream: bool) -> TtsResult:
    mode = "stream_true" if stream else "stream_false"
    payload = {
        "text": args.text,
        "speed": args.speed,
        "stream": stream,
        "textFrontend": args.text_frontend,
    }
    if args.prompt_text:
        payload["promptText"] = args.prompt_text
    if args.prompt_wav:
        payload["promptWav"] = args.prompt_wav

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pcm_path = args.out_dir / f"{mode}.pcm"
    wav_path = args.out_dir / f"{mode}.wav"
    timeline_path = args.out_dir / f"{mode}_timeline.csv"

    request = urllib.request.Request(
        args.url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"\n[{mode}] request start: stream={stream}")
    started = time.perf_counter()
    events: list[ChunkEvent] = []
    byte_count = 0
    chunk_count = 0
    first_chunk_seconds: float | None = None

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            headers_seconds = time.perf_counter() - started
            sample_rate = int(response.headers.get("X-Sample-Rate") or DEFAULT_SAMPLE_RATE)
            print(f"[{mode}] response headers: {headers_seconds:.3f}s, sample_rate={sample_rate}")

            with pcm_path.open("wb") as pcm_file:
                while True:
                    chunk = response.read(args.chunk_size)
                    if not chunk:
                        break
                    now = time.perf_counter()
                    chunk_count += 1
                    byte_count += len(chunk)
                    at_seconds = now - started
                    if first_chunk_seconds is None:
                        first_chunk_seconds = at_seconds
                        print(f"[{mode}] first audio chunk: {first_chunk_seconds:.3f}s")

                    cumulative_audio_seconds = byte_count / 2 / sample_rate
                    events.append(
                        ChunkEvent(
                            index=chunk_count,
                            at_seconds=at_seconds,
                            bytes_received=len(chunk),
                            cumulative_bytes=byte_count,
                            cumulative_audio_seconds=cumulative_audio_seconds,
                        )
                    )
                    if args.log_chunks and chunk_count <= args.log_chunks:
                        print(
                            f"[{mode}] chunk {chunk_count}: "
                            f"t={at_seconds:.3f}s, bytes={len(chunk)}, "
                            f"audio_ready={cumulative_audio_seconds:.3f}s"
                        )
                    pcm_file.write(chunk)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{mode} failed HTTP {error.code}: {body[:1000]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"{mode} failed to connect: {error.reason}") from error

    elapsed_seconds = time.perf_counter() - started
    audio_seconds = byte_count / 2 / sample_rate if sample_rate else 0.0
    write_wav(pcm_path, wav_path, sample_rate)
    write_timeline(timeline_path, events)

    print(
        f"[{mode}] done: total={elapsed_seconds:.3f}s, "
        f"audio={audio_seconds:.3f}s, chunks={chunk_count}, bytes={byte_count}"
    )
    print(f"[{mode}] wav: {wav_path.resolve()}")

    return TtsResult(
        mode=mode,
        sample_rate=sample_rate,
        headers_seconds=headers_seconds,
        first_chunk_seconds=first_chunk_seconds,
        elapsed_seconds=elapsed_seconds,
        chunk_count=chunk_count,
        byte_count=byte_count,
        audio_seconds=audio_seconds,
        pcm_path=pcm_path,
        wav_path=wav_path,
        timeline_path=timeline_path,
        events=events,
    )


def write_wav(pcm_path: Path, wav_path: Path, sample_rate: int) -> None:
    with pcm_path.open("rb") as pcm_file:
        pcm = pcm_file.read()
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)


def write_timeline(path: Path, events: list[ChunkEvent]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "chunk_index",
                "arrival_seconds",
                "bytes_received",
                "cumulative_bytes",
                "cumulative_audio_seconds",
            ]
        )
        for event in events:
            writer.writerow(
                [
                    event.index,
                    f"{event.at_seconds:.6f}",
                    event.bytes_received,
                    event.cumulative_bytes,
                    f"{event.cumulative_audio_seconds:.6f}",
                ]
            )


def print_summary(results: list[TtsResult]) -> None:
    print("\nSummary")
    print(
        f"{'mode':<14} {'headers_s':>10} {'first_chunk_s':>14} {'total_s':>10} "
        f"{'audio_s':>10} {'rtf_wall':>10} {'chunks':>8} {'bytes':>10}"
    )
    for result in results:
        first = f"{result.first_chunk_seconds:.3f}" if result.first_chunk_seconds is not None else "n/a"
        rtf = result.elapsed_seconds / result.audio_seconds if result.audio_seconds else 0.0
        print(
            f"{result.mode:<14} {result.headers_seconds:>10.3f} {first:>14} "
            f"{result.elapsed_seconds:>10.3f} {result.audio_seconds:>10.3f} "
            f"{rtf:>10.3f} {result.chunk_count:>8} {result.byte_count:>10}"
        )

    by_mode = {result.mode: result for result in results}
    stream_result = by_mode.get("stream_true")
    nonstream_result = by_mode.get("stream_false")
    if stream_result and nonstream_result:
        stream_first = stream_result.first_chunk_seconds
        nonstream_first = nonstream_result.first_chunk_seconds
        print("\nInterpretation")
        if stream_first is None or nonstream_first is None:
            print("One mode returned no audio bytes, so the comparison is invalid.")
        else:
            lead = nonstream_first - stream_first
            print(f"stream=true first audio was {lead:.3f}s earlier than stream=false.")
            if lead > 0.5:
                print("This run shows a real earlier-audio advantage for stream=true.")
            else:
                print(
                    "This run does not show a meaningful earlier-audio advantage. "
                    "For this text/model path, the first yielded audio may still arrive near completion."
                )

    print("\nOutput files")
    for result in results:
        print(f"{result.mode}:")
        print(f"  wav      {result.wav_path.resolve()}")
        print(f"  pcm      {result.pcm_path.resolve()}")
        print(f"  timeline {result.timeline_path.resolve()}")


def play_wav(path: Path) -> None:
    if sys.platform != "win32":
        print(f"Skipping playback on non-Windows platform: {path}")
        return
    import winsound

    print(f"Playing {path.resolve()}")
    winsound.PlaySound(str(path.resolve()), winsound.SND_FILENAME)


def parse_order(value: str) -> list[bool]:
    mapping = {"stream": True, "stream_true": True, "nonstream": False, "stream_false": False}
    modes: list[bool] = []
    for raw_part in value.split(","):
        part = raw_part.strip().lower()
        if not part:
            continue
        if part not in mapping:
            raise ValueError("order must contain only stream and nonstream")
        modes.append(mapping[part])
    if sorted(modes) != [False, True]:
        raise ValueError("order must include stream and nonstream exactly once")
    return modes


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parse_args()
    try:
        order = parse_order(args.order)
    except ValueError as error:
        print(f"Invalid --order: {error}", file=sys.stderr)
        return 2

    print(f"endpoint: {args.url}")
    print(f"text chars: {len(args.text)}")
    print(f"textFrontend: {args.text_frontend}")
    print(f"output dir: {args.out_dir.resolve()}")

    results: list[TtsResult] = []
    try:
        for stream in order:
            result = request_mode(args, stream=stream)
            results.append(result)
            if args.play:
                play_wav(result.wav_path)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    print_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
