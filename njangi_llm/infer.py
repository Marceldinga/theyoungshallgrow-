from __future__ import annotations

import json
from pathlib import Path

import torch
import torch.nn as nn

ART = Path("artifacts")
CKPT_DIR = ART / "checkpoints"
VOCAB = ART / "data" / "vocab.json"


class TinyLM(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        n_emb: int = 128,
        n_head: int = 4,
        n_layer: int = 2,
        block_size: int = 256,
    ):
        super().__init__()
        self.block_size = block_size
        self.tok = nn.Embedding(vocab_size, n_emb)
        self.pos = nn.Embedding(block_size, n_emb)
        enc = nn.TransformerEncoderLayer(d_model=n_emb, nhead=n_head, batch_first=True)
        self.tr = nn.TransformerEncoder(enc, num_layers=n_layer)
        self.lm_head = nn.Linear(n_emb, vocab_size)

    def forward(self, idx):
        B, T = idx.shape
        if T > self.block_size:
            idx = idx[:, -self.block_size :]
            T = idx.shape[1]
        pos = torch.arange(0, T, device=idx.device).unsqueeze(0)
        x = self.tok(idx) + self.pos(pos)
        x = self.tr(x)
        return self.lm_head(x)


def _latest_ckpt() -> Path | None:
    if not CKPT_DIR.exists():
        return None
    ckpts = sorted(CKPT_DIR.glob("ckpt_step*.pt"), key=lambda p: p.stat().st_mtime)
    return ckpts[-1] if ckpts else None


def load_vocab():
    if not VOCAB.exists():
        return None, None
    v = json.loads(VOCAB.read_text(encoding="utf-8"))
    stoi = {k: int(vv) for k, vv in v["stoi"].items()}
    itos = {int(k): v for k, v in v["itos"].items()}
    return stoi, itos


def encode(text: str, stoi: dict[str, int]):
    return torch.tensor([stoi[c] for c in text if c in stoi], dtype=torch.long)


def decode(t: torch.Tensor, itos: dict[int, str]):
    return "".join([itos[int(i)] for i in t])


@torch.no_grad()
def generate(prompt: str, max_new_tokens: int = 200, temperature: float = 0.9) -> str:
    ckpt = _latest_ckpt()
    if ckpt is None:
        return "No checkpoint yet. Start training first."

    stoi, itos = load_vocab()
    if stoi is None or itos is None:
        return "No vocab yet. Start training to create vocab.json."

    ck = torch.load(ckpt, map_location="cpu")
    vocab_size = ck["vocab_size"]
    block_size = ck["block_size"]
    n_emb = ck.get("n_emb", 128)
    n_head = ck.get("n_head", 4)
    n_layer = ck.get("n_layer", 2)

    model = TinyLM(vocab_size=vocab_size, n_emb=n_emb, n_head=n_head, n_layer=n_layer, block_size=block_size)
    model.load_state_dict(ck["model"])
    model.eval()

    x = encode(prompt, stoi)
    if len(x) == 0:
        return "Prompt has no known characters in vocab yet."

    x = x[-block_size:].unsqueeze(0)  # (1, T)

    for _ in range(max_new_tokens):
        logits = model(x)[:, -1, :]  # (1, vocab)
        if temperature <= 0:
            next_id = torch.argmax(logits, dim=-1).view(1, 1)
        else:
            probs = torch.softmax(logits / float(temperature), dim=-1)
            next_id = torch.multinomial(probs, num_samples=1)
        x = torch.cat([x, next_id], dim=1)
        x = x[:, -block_size:]

    return decode(x[0], itos)
