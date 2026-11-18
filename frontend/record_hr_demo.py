"""Record the HR demo video: the control room, live, with a voiceover.

The calls are the ones our HW9 cold-start agent made (searches, write, ratings), then the
intern's read. They are replayed against the real service, so every allow, deny, label and
review decision on screen is the service's own. Narration is a Microsoft Edge neural voice
(edge-tts, free, no key; NARRATION_VOICE to change it); ffmpeg mixes it in.

Run with the service (HR seed) and the control room already up, agents file = hr + intern:
    python frontend/record_hr_demo.py --out /path/to/mindvault-hr-demo.mp4
Needs: playwright (+ chromium), edge-tts, ffmpeg, macOS `afinfo`.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import edge_tts  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from scripts.selftest import Agent  # noqa: E402
from server.config import Config, load_agents  # noqa: E402

VOICE = os.environ.get("NARRATION_VOICE", "en-US-AvaNeural")  # Microsoft Edge neural voice
RATE = os.environ.get("NARRATION_RATE", "+0%")
VIEWPORT = {"width": 1440, "height": 810}
FRONTEND = os.environ.get("FRONTEND_URL", "http://127.0.0.1:8100")

TITLE = "Why Austin engineers left in Q3: night on-call load since Sep 1"
CONTENT = ("Austin engineering attrition rose from 3% (Q2) to 11% (Q3), almost all resignations in "
           "September after the 24/7 on-call rotation began Sep 1 (b-101, b-318). 9 of 11 leavers cited "
           "night on-call as the main reason; 2 cited pay (b-103). On-call satisfaction fell 3.9 to 2.1 "
           "(b-207); night pages rose 45 to 340 (b-501). Caveat: b-318 is unverified; evidence is "
           "correlational.")

# (narration, action name). Each action starts shortly after its line begins.
SCRIPT = [
    ("MindVault is shared memory for AI agents. Each card is a memory, stacked by sensitivity.",
     None),
    ("An HR agent asks why Austin engineers are leaving. It reads HR, survey and incident data. "
     "The confidential leadership notes stay hidden.", "hr_search"),
    ("It saves its conclusion. It read department HR data, so the service labels it level two, "
     "and a reviewer checks it for personal data.", "hr_write"),
    ("An intern agent tries to read it. Denied. Nothing leaks.", "intern_read"),
    ("In our benchmark, self-labelling agents leaked in 8 of 42 runs. With MindVault, zero.",
     "view_intern"),
]


async def tts(text: str, path: Path) -> float:
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(path))
    out = subprocess.run(["afinfo", str(path)], capture_output=True, text=True, check=True)
    line = next(l for l in out.stdout.splitlines() if "estimated duration" in l)
    return float(line.split(":")[1].split()[0])


async def run(out: Path) -> None:
    cfg = Config.from_env()
    keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    url = f"http://{cfg.host}:{cfg.port}/mcp"
    work = Path(tempfile.mkdtemp(prefix="mv-video-"))

    clips = []
    for i, (line, _) in enumerate(SCRIPT):
        clip = work / f"line{i}.mp3"
        clips.append((clip, await tts(line, clip)))

    new_id = {}

    async def hr_search(hr):
        for q in ("attrition Austin", "engineers leaving"):
            await hr.call("memory_read", {"query": q})
            await asyncio.sleep(1.2)

    async def hr_write(hr):
        w = await hr.call("memory_write", {"title": TITLE, "content": CONTENT})
        new_id["id"] = w.get("block_id")
        print("write:", json.dumps({k: w.get(k) for k in ("block_id", "level", "scope", "status")}),
              w.get("review"))
        await hr.call("memory_rate", {"ratings": [
            {"block_id": b, "accuracy": 5, "reason": "Cited in the conclusion"}
            for b in ("b-101", "b-103", "b-207", "b-501")]})

    async def intern_read(_hr):
        async with Agent(url, keys["intern"]) as intern:
            r = await intern.call("memory_read", {"block_id": new_id.get("id") or "w-0001"})
            print("intern:", r.get("reason_code"))

    async with async_playwright() as p, Agent(url, keys["hr"]) as hr:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport=VIEWPORT, record_video_dir=str(work),
                                        record_video_size=VIEWPORT)
        page = await ctx.new_page()
        t_page = time.monotonic()
        await page.goto(FRONTEND)
        await page.wait_for_timeout(2500)

        async def view_intern(_hr):
            await page.click("button[data-view='intern']")

        actions = {"hr_search": hr_search, "hr_write": hr_write,
                   "intern_read": intern_read, "view_intern": view_intern}

        t0 = time.monotonic()
        marks = []
        for (line, action), (clip, dur) in zip(SCRIPT, clips):
            start = time.monotonic()
            marks.append((clip, start - t0))
            if action:
                await asyncio.sleep(0.8)
                await actions[action](hr)
            rest = dur + 0.1 - (time.monotonic() - start)
            if rest > 0:
                await asyncio.sleep(rest)
        await asyncio.sleep(1.5)
        total = time.monotonic() - t0
        await ctx.close()
        await browser.close()
        video = Path(await page.video.path())

    lead = t0 - t_page
    inputs, filters = ["-ss", f"{lead:.2f}", "-t", f"{total:.2f}", "-i", str(video)], []
    for i, (clip, at) in enumerate(marks, start=1):
        inputs += ["-i", str(clip)]
        ms = int(at * 1000)
        filters.append(f"[{i}:a]adelay={ms}|{ms},aresample=48000[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(1, len(marks) + 1))
    filters.append(f"{mix}amix=inputs={len(marks)}:normalize=0[aout]")
    filters.append("[0:v]scale=1920:1080:flags=lanczos,fps=30[vout]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(filters),
                    "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", str(out)], check=True)
    print(f"wrote {out} ({total:.1f}s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "mindvault-hr-demo.mp4")
    asyncio.run(run(ap.parse_args().out))
