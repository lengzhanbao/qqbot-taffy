"""Loopback-only, bounded HTTP wrapper around the proven Bert-VITS2 V200 path."""
import argparse
import importlib.util
import io
import json
import os
import re
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "data" / "tts" / "runtime"


def runtime_environment():
    sys.dont_write_bytecode = True
    for key, name in {
        "TEMP": "tmp", "TMP": "tmp", "HF_HOME": "hf", "XDG_CACHE_HOME": "cache",
        "TORCH_HOME": "torch", "NUMBA_CACHE_DIR": "numba",
    }.items():
        path = RUNTIME / name
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["BERT_VITS2_SKIP_BERT_CHECK"] = "1"


def text_chunks(text, size=120):
    """Bound BERT/GPU working memory while keeping the entire answer."""
    while len(text) > size:
        boundaries = list(re.finditer(r"[。！？!?；;，,\n]", text[:size]))
        cut = boundaries[-1].end() if boundaries else size
        yield text[:cut]
        text = text[cut:]
    if text.strip():
        yield text


class TaffyEngine:
    def __init__(self, source):
        reference = source / "scripts" / "test_taffy_infer.py"
        spec = importlib.util.spec_from_file_location("taffy_reference", reference)
        ref = importlib.util.module_from_spec(spec)
        runtime_environment()
        spec.loader.exec_module(ref)
        # The original standalone script sets its own E: cache paths. The service
        # keeps all new runtime data under QQBot after importing the tested symbols.
        runtime_environment()
        self.ref = ref
        torch = ref.torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        config = json.loads((source / "config.json").read_text(encoding="utf-8"))
        if config.get("version") != "2.0" or config["data"]["spk2id"].get("永雏塔菲") != 0:
            raise ValueError("Expected the deployed Taffy V200 config")
        data = ref.SimpleNamespace(**config["data"])
        train = ref.SimpleNamespace(**config["train"])
        net = ref.SynthesizerTrn(
            len(ref.symbols), data.filter_length // 2 + 1,
            train.segment_size // data.hop_length, n_speakers=data.n_speakers,
            **config["model"],
        )
        checkpoint = torch.load(source / "G_7600.pth", map_location="cpu", weights_only=True, mmap=True)
        # Strict loading checks all keys and shapes; no random-weight fallback.
        net.load_state_dict(checkpoint["model"], strict=True)
        del checkpoint
        self.net = net.eval().to("cuda")
        self.hps = ref.SimpleNamespace(data=data, train=train, version="2.0")
        from oldVersion.V200.text import chinese_bert
        chinese_bert.get_bert_feature("你好", [1, 1, 1, 1], "cuda")
        self.info = {
            "version": "2.0", "sample_rate": data.sampling_rate,
            "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__,
            "cuda": torch.version.cuda, "model_loads": 1,
        }

    def synthesize(self, text):
        ref = self.ref
        started = time.monotonic()
        chunks = []
        for chunk in text_chunks(text):
            if time.monotonic() - started > 120:
                raise TimeoutError("TTS execution budget exceeded")
            # Exactly the same parameters and inference function as the S3 sample.
            audio = ref.infer(chunk, 0.2, 0.6, 0.8, 1.0, "永雏塔菲", "ZH",
                              self.hps, self.net, "cuda")
            if not ref.np.isfinite(audio).all() or not len(audio):
                raise ValueError("Invalid generated samples")
            chunks.append(audio)
            if sum(len(item) for item in chunks) * 2 > 64 * 1024 * 1024:
                raise ValueError("WAV exceeds transport limit")
        if not chunks:
            raise ValueError("No speakable text")
        audio = ref.np.concatenate(chunks)
        if not ref.np.any(audio):
            raise ValueError("Empty audio")
        pcm = (ref.np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.hps.data.sampling_rate)
            wav.writeframes(pcm.tobytes())
        return output.getvalue()


class TaffyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, engine):
        self.engine = engine
        self.inference_lock = threading.Lock()
        self.queue_slots = threading.BoundedSemaphore(4)
        self.handler_slots = threading.BoundedSemaphore(12)
        self.generated = 0
        super().__init__(address, TaffyHandler)

    def process_request(self, request, address):
        if not self.handler_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.handler_slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.handler_slots.release()


class TaffyHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def respond(self, status, payload, content_type="application/json"):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != "/health":
            return self.respond(404, {"error": "not_found"})
        return self.respond(200, {"service": "taffy-v200", "ready": True, "pid": os.getpid(),
                                  "generated": self.server.generated, **self.server.engine.info})

    def do_POST(self):
        if self.path != "/synthesize":
            return self.respond(404, {"error": "not_found"})
        if not self.server.queue_slots.acquire(blocking=False):
            return self.respond(503, {"error": "queue_full"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                return self.respond(413, {"error": "invalid_payload_size"})
            data = json.loads(self.rfile.read(length))
            text = data.get("text") if isinstance(data, dict) else None
            if not isinstance(text, str) or not text.strip():
                return self.respond(400, {"error": "text_required"})
            with self.server.inference_lock:
                payload = self.server.engine.synthesize(text)
                self.server.generated += 1
            self.respond(200, payload, "audio/wav")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, TypeError, TimeoutError) as exc:
            self.respond(400, {"error": type(exc).__name__})
        except Exception as exc:
            print(f"synthesis_failed: {type(exc).__name__}", flush=True)
            self.respond(503, {"error": "synthesis_failed"})
        finally:
            self.server.queue_slots.release()

    def log_message(self, format, *args):
        print(f"{self.log_date_time_string()} {format % args}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT.parent / "Bert-VITS2-master")
    parser.add_argument("--port", type=int, default=17861)
    args = parser.parse_args()
    engine = TaffyEngine(args.source.resolve())
    with TaffyServer(("127.0.0.1", args.port), engine) as server:
        print(f"Taffy ready on 127.0.0.1:{args.port}, GPU={engine.info['gpu']}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
