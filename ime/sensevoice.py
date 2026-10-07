"""Lazy SenseVoiceSmall inference from an explicitly supplied local directory.

FunASR's hub loader is bypassed entirely. No model-supplied code, auxiliary
models, package installation, or text rewrite/postprocessing is enabled.
"""
from pathlib import Path
import re
from types import SimpleNamespace


class SenseVoiceAdapter:
    supported_languages = ["zh", "yue", "en"]

    def __init__(self, path, device):
        root = Path(path).expanduser().resolve()
        if not self.cached(root):
            raise ValueError("SenseVoice needs a local config.yaml, model.pt, and one *.bpe.model file")
        from omegaconf import OmegaConf
        from funasr import AutoModel

        config = OmegaConf.to_container(OmegaConf.load(root / "config.yaml"), resolve=True)
        if config.get("model") != "SenseVoiceSmall" or config.get("tokenizer") != "SentencepiecesTokenizer" or config.get("frontend") != "WavFrontend":
            raise ValueError("Only the standard local SenseVoiceSmall configuration is supported")
        # Supplying model_conf skips FunASR's download_model path. Whitelist
        # inference fields so config cannot enable remote code or extra models.
        options = {key: config[key] for key in ("model", "model_conf", "encoder", "encoder_conf", "tokenizer", "tokenizer_conf", "frontend", "frontend_conf") if key in config}
        options.setdefault("model_conf", {})
        options["tokenizer_conf"] = dict(options.get("tokenizer_conf") or {})
        options["tokenizer_conf"]["bpemodel"] = str(next(root.glob("*.bpe.model")))
        options["frontend_conf"] = dict(options.get("frontend_conf") or {})
        options["frontend_conf"]["cmvn_file"] = str(root / "am.mvn") if (root / "am.mvn").is_file() else None
        self.model = AutoModel(**options, init_param=str(root / "model.pt"), model_path=str(root),
                               device=device, disable_update=True, disable_pbar=True,
                               trust_remote_code=False)
        self.device = self.model.kwargs.get("device", device)

    @staticmethod
    def cached(path):
        root = Path(path).expanduser()
        return (root / "config.yaml").is_file() and (root / "model.pt").is_file() and len(list(root.glob("*.bpe.model"))) == 1

    def transcribe(self, waveform, *, language=None, hotwords=None, initial_prompt=None, **_):
        if hotwords or initial_prompt:
            raise ValueError("SenseVoiceSmall does not support hotwords or initial prompts; use faster-whisper for terminology hints")
        results = self.model.generate(input=waveform, cache={}, language=language or "auto",
                                      use_itn=False, batch_size_s=120, disable_pbar=True)
        text = "".join(re.sub(r"<\|[^|]*\|>", "", item.get("text", "")) for item in results)
        detected = language or "auto"
        if language is None:
            for item in results:
                tag = re.search(r"<\|(zh|yue|en|ja|ko)\|>", item.get("text", ""))
                if tag:
                    detected = tag.group(1)
                    break
        # Only strip model metadata tags; numbers, negation and names remain.
        return iter([SimpleNamespace(text=text)]), SimpleNamespace(language=detected)
