"""Command line: `ultron-eyes run | scan | analyze | stats`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ultron-eyes", description="Private AI search for home camera events.")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="listen for motion, analyze, store, and serve the search page")
    sc = sub.add_parser("scan", help="snapshot + analyze cameras once right now")
    sc.add_argument("channels", nargs="*", type=int, help="default: every configured camera")
    an = sub.add_parser("analyze", help="analyze an image file (no camera needed)")
    an.add_argument("image")
    an.add_argument("--plates", action="store_true")
    sub.add_parser("stats", help="event counts, review accuracy, latency")
    sub.add_parser("serve", help="search page only (no motion listening)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "open_image_models", "fast_alpr", "fast_plate_ocr", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from . import config as config_mod

    if args.cmd == "analyze":
        import cv2

        from .vision import Vision

        cfg = config_mod.load(args.config) if Path(args.config).exists() else None
        v = Vision(cfg.labels if cfg else ["person", "car", "truck", "dog", "cat", "bicycle", "motorcycle"],
                   read_plates=args.plates)
        img = cv2.imread(args.image)
        if img is None:
            raise SystemExit(f"cannot read {args.image}")
        t = time.time()
        a = v.analyze(img, cfg.known_plates if cfg else [], plates=args.plates)
        print(json.dumps({"ms": int((time.time() - t) * 1000),
                          "objects": [(o.label, o.confidence) for o in a.objects],
                          "plates": [(p.text, p.confidence, p.known_name) for p in a.plates]}, indent=2))
        return

    cfg = config_mod.load(args.config)
    from .store import Store

    store = Store(cfg.data_dir)
    if args.cmd == "stats":
        print(json.dumps(store.stats(), indent=2))
        return

    import uvicorn

    from .web import create_app

    if args.cmd == "serve":
        uvicorn.run(create_app(cfg, store), host=cfg.web_host, port=cfg.web_port, log_level="warning")
        return

    from .pipeline import Pipeline

    pipe = Pipeline(cfg, store)
    if args.cmd == "scan":
        for ch in args.channels or [c.channel for c in cfg.cameras]:
            t = time.time()
            ev = pipe.process(ch, t)
            print(f"cam {ch}: " + (f"{ev['labels']} plates={[(p['text'], p['known_name']) for p in ev['plates']]}"
                                   f" saved as event {ev['id']} ({ev['latency_ms']} ms)" if ev else "nothing recognized"))
        return

    stop = threading.Event()
    threading.Thread(target=pipe.worker, args=(stop,), daemon=True, name="worker").start()
    threading.Thread(target=lambda: asyncio.run(pipe.listen_home_assistant(stop)), daemon=True, name="ha").start()
    logging.info("search page: http://%s:%d", cfg.web_host, cfg.web_port)
    try:
        uvicorn.run(create_app(cfg, store, pipe), host=cfg.web_host, port=cfg.web_port, log_level="warning")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
