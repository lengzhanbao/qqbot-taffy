import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tts_bert_manifest_is_pinned_and_complete():
    manifest = json.loads((ROOT / "scripts" / "tts_bert_manifest.json").read_text(encoding="utf-8"))

    assert manifest["repo_id"] == "hfl/chinese-roberta-wwm-ext-large"
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["revision"])
    assert manifest["license"] == "apache-2.0"

    files = {entry["name"]: entry for entry in manifest["files"]}
    assert set(files) == {
        "pytorch_model.bin",
        "config.json",
        "added_tokens.json",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.txt",
    }
    assert "G_7600.pth" not in files
    for entry in files.values():
        assert entry["size"] > 0
        assert re.fullmatch(r"[A-F0-9]{64}", entry["sha256"])


def test_tts_download_script_preserves_existing_files_and_pins_hashes():
    script = (ROOT / "scripts" / "download_tts_bert.ps1").read_text(encoding="utf-8")

    assert "manifest.revision" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "Existing file does not match pinned SHA-256; it was left unchanged" in script
    assert "[System.IO.File]::Move($partial, $destination)" in script
    assert "G_7600.pth" in script
