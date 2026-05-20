"""
setup_models.py

One-time setup script for the YouTube News Bot local ML models.

Usage:
    python setup_models.py

What it does:
  1. Downloads google/flan-t5-large  (script writing,  ~780 MB)
  2. Downloads google/flan-t5-base   (title/tags/desc, ~250 MB)
  3. Downloads Kokoro-82M ONNX model (~330 MB)
  4. Verifies both models can generate output
  5. Runs a quick inference benchmark on a sample article
  6. Prints a summary with RAM usage and expected time-per-article

After this script completes, the bot runs fully offline — no API calls needed.
"""

import os
import sys
import time
import textwrap
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

load_dotenv()
console = Console()

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_MODEL = os.environ.get("ML_MODEL_SCRIPT", "google/flan-t5-large")
META_MODEL   = os.environ.get("ML_MODEL_META",   "google/flan-t5-base")
CACHE_DIR    = os.environ.get("ML_CACHE_DIR",    "./models")

SAMPLE_TITLE   = "India launches new space mission to study the Moon's south pole"
SAMPLE_CONTENT = (
    "The Indian Space Research Organisation (ISRO) announced the successful launch "
    "of its latest lunar mission from the Satish Dhawan Space Centre. The mission "
    "aims to study the permanently shadowed regions of the Moon's south pole, where "
    "scientists believe significant water ice deposits exist. The spacecraft is expected "
    "to reach lunar orbit within 30 days. Prime Minister Modi congratulated the ISRO team "
    "and called it a landmark achievement in India's space programme. The mission carries "
    "a rover and an orbiter equipped with advanced spectrometers and cameras."
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_dependencies():
    """Verify all required packages are installed before attempting downloads."""
    missing = []
    for pkg in ("transformers", "torch", "sentencepiece", "accelerate", "yake", "kokoro_onnx", "huggingface_hub"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        console.print(f"\n[bold red]Missing packages:[/] {', '.join(missing)}")
        console.print("\n[yellow]Run the following to install:[/]")
        console.print(
            "  [cyan]pip install torch --index-url https://download.pytorch.org/whl/cpu[/]"
        )
        console.print(
            "  [cyan]pip install transformers sentencepiece accelerate yake kokoro-onnx huggingface_hub[/]"
        )
        sys.exit(1)
    console.print("[green]✓[/] All required packages are installed.")


def _get_ram_mb() -> float:
    """Return current process RSS memory in MB (best-effort)."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


def _download_and_verify(model_name: str, label: str, test_prompt: str, max_tokens: int = 60) -> dict:
    """Download model, run a test inference, return stats."""
    from transformers import pipeline as hf_pipeline

    console.print(f"\n[cyan]Downloading:[/] [bold]{model_name}[/]")
    console.print(f"  Cache directory: [dim]{CACHE_DIR}[/]")

    ram_before = _get_ram_mb()
    t_start = time.time()

    pipe = hf_pipeline(
        "text2text-generation",
        model=model_name,
        tokenizer=model_name,
        model_kwargs={"cache_dir": CACHE_DIR},
        device="cpu",
    )

    load_time = time.time() - t_start
    ram_after = _get_ram_mb()
    ram_used  = ram_after - ram_before if ram_after > 0 else 0

    console.print(f"  [green]✓[/] Model loaded in {load_time:.1f}s  |  RAM delta: ~{ram_used:.0f} MB")

    # Test inference
    console.print(f"  Running test inference…")
    t_inf = time.time()
    result = pipe(test_prompt, max_new_tokens=max_tokens, do_sample=False, num_beams=2)
    inf_time = time.time() - t_inf
    output = result[0]["generated_text"].strip()

    console.print(f"  [green]✓[/] Inference done in {inf_time:.1f}s")
    console.print(f"  [dim]Output:[/] {output[:120]}")

    return {
        "label":     label,
        "model":     model_name,
        "load_s":    round(load_time, 1),
        "infer_s":   round(inf_time, 1),
        "ram_mb":    round(ram_used, 0),
        "output_ok": len(output) > 5,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold red]YouTube News Bot — Model Setup")

    console.print("\n[bold]Step 1/4:[/] Checking dependencies…")
    _check_dependencies()

    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    console.print(f"[green]✓[/] Model cache directory: [dim]{CACHE_DIR}[/]")

    # ── Download & verify models ──────────────────────────────────────────────
    console.print("\n[bold]Step 2/4:[/] Downloading Flan-T5 models from HuggingFace…")
    console.print("[dim]  (This only happens once. All subsequent runs are offline.)[/]\n")

    stats = []

    # Script model (flan-t5-large)
    script_prompt = (
        f"Write a professional TV news anchor script for a YouTube video about: {SAMPLE_TITLE}. "
        f"Use authoritative tone. 3 sentences."
    )
    s1 = _download_and_verify(SCRIPT_MODEL, "Script (flan-t5-large)", script_prompt, max_tokens=120)
    stats.append(s1)

    # Meta model (flan-t5-base) — only download if different from script model
    if META_MODEL != SCRIPT_MODEL:
        title_prompt = (
            f"Write a YouTube video title for an Indian news channel about: {SAMPLE_TITLE}. "
            f"Professional, SEO-optimized, max 90 characters."
        )
        s2 = _download_and_verify(META_MODEL, "Title/Tags (flan-t5-base)", title_prompt, max_tokens=40)
        stats.append(s2)
    else:
        console.print(f"\n[dim]  (Meta model = script model, skipping duplicate download.)[/]")

    # ── YAKE tags test ────────────────────────────────────────────────────────
    console.print("\n[bold]Step 3/4:[/] Verifying YAKE keyword extractor…")
    try:
        import yake
        kw_extractor = yake.KeywordExtractor(lan="en", n=2, top=10)
        keywords = kw_extractor.extract_keywords(SAMPLE_TITLE + " " + SAMPLE_CONTENT)
        sample_tags = [kw for kw, _ in keywords[:5]]
        console.print(f"[green]✓[/] YAKE working — sample tags: {sample_tags}")
    except Exception as e:
        console.print(f"[yellow]⚠ YAKE test failed: {e} — tag extraction will fall back to word-frequency.[/]")

    # ── Kokoro-82M TTS Setup ──────────────────────────────────────────────────
    console.print("\n[bold]Step 3.5/4:[/] Downloading Kokoro-82M TTS weights…")
    try:
        from huggingface_hub import hf_hub_download
        
        console.print(f"[dim]Downloading kokoro-v0_19.onnx (~330 MB) to {CACHE_DIR}...[/]")
        onnx_path = hf_hub_download(repo_id="hexgrad/Kokoro-82M", filename="kokoro-v0_19.onnx", cache_dir=CACHE_DIR)
        
        console.print(f"[dim]Downloading voices.json to {CACHE_DIR}...[/]")
        voices_path = hf_hub_download(repo_id="hexgrad/Kokoro-82M", filename="voices.json", cache_dir=CACHE_DIR)
        
        # We need these in a known path for tts_engine.py to find easily
        import shutil
        dest_onnx = os.path.join(CACHE_DIR, "kokoro-v0_19.onnx")
        dest_voices = os.path.join(CACHE_DIR, "voices.json")
        shutil.copy2(onnx_path, dest_onnx)
        shutil.copy2(voices_path, dest_voices)
        
        console.print("[green]✓[/] Kokoro-82M models downloaded successfully!")
    except Exception as e:
        console.print(f"[red]✗ Failed to download Kokoro-82M: {e}[/]")

    # ── End-to-end generator test ─────────────────────────────────────────────
    console.print("\n[bold]Step 4/4:[/] Running end-to-end LocalMLGenerator test…")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from scraper.news_scraper import NewsArticle
        from content.local_ml_generator import LocalMLGenerator

        article = NewsArticle(
            title=SAMPLE_TITLE,
            content=SAMPLE_CONTENT,
            source="ISRO / NDTV",
            category="technology",
            url="https://example.com/isro-moon-mission",
        )

        t_e2e = time.time()
        gen = LocalMLGenerator()
        vc  = gen.generate(article)
        e2e_time = time.time() - t_e2e

        if vc:
            console.print(f"[green]✓[/] Full generation completed in {e2e_time:.1f}s")
            console.print(f"  [bold]Title:[/]  {vc.title}")
            console.print(f"  [bold]Script:[/] {vc.script[:100]}…")
            console.print(f"  [bold]Tags:[/]   {vc.tags[:6]}")
            console.print(f"  [bold]Duration:[/] {vc.estimated_duration}s")
        else:
            console.print("[yellow]⚠ Generator returned None — check logs above.[/]")

    except Exception as exc:
        console.print(f"[red]✗ End-to-end test failed: {exc}[/]")
        import traceback; traceback.print_exc()

    # ── Summary table ─────────────────────────────────────────────────────────
    console.rule("[bold]Setup Summary")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Model",         width=30)
    table.add_column("Load time",     width=12)
    table.add_column("Infer time",    width=12)
    table.add_column("RAM delta",     width=12)
    table.add_column("Status",        width=10)

    for s in stats:
        status = "[green]✓ OK[/]" if s["output_ok"] else "[red]✗ FAIL[/]"
        table.add_row(
            s["label"],
            f"{s['load_s']}s",
            f"{s['infer_s']}s",
            f"~{s['ram_mb']} MB" if s["ram_mb"] else "n/a",
            status,
        )
    console.print(table)

    console.print(textwrap.dedent("""
        [bold green]Setup complete![/]

        Models are cached in [cyan]{cache_dir}[/].
        The bot will now run [bold]fully offline[/] — no API keys, no internet at inference time.

        [bold]Next steps:[/]
          1. Copy [cyan].env.example[/] to [cyan].env[/]  (USE_LOCAL_ML=true is already set)
          2. Add your YouTube API credentials to [cyan].env[/]
          3. Run:  [cyan]python pipeline.py --dry-run[/]
    """.format(cache_dir=CACHE_DIR)))


if __name__ == "__main__":
    main()
